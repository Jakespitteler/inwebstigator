import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.schema import DBScanRun, DBWebsite, DBWebsiteTag, website_tag_association
from app.frontend.api.request_guard import API_TOKEN_HEADER
from app.models.website_models import WebsiteRead


def _create(client: TestClient, name: str = "News") -> str:
    response = client.post("/tags", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_create_rename_and_sort_tags(api_client: TestClient) -> None:
    news = _create(api_client, "  News   and   media  ")
    _create(api_client, "Education")
    assert [tag["name"] for tag in api_client.get("/tags").json()] == ["Education", "News and media"]
    response = api_client.patch(f"/tags/{news}", json={"name": "NEWS"})
    assert response.status_code == 200
    assert response.json() == {"id": news, "name": "NEWS"}


@pytest.mark.parametrize("name", ["", "  \n\t ", "x" * 61, None, 123])
def test_invalid_tag_names_do_not_create_records(api_client: TestClient, name: object) -> None:
    assert api_client.post("/tags", json={"name": name}).status_code == 422
    assert api_client.get("/tags").json() == []


def test_duplicate_and_rename_conflicts_leave_tags_intact(api_client: TestClient) -> None:
    news = _create(api_client)
    study = _create(api_client, "Study")
    assert api_client.post("/tags", json={"name": " news "}).status_code == 409
    assert api_client.patch(f"/tags/{study}", json={"name": "NEWS"}).status_code == 409
    assert api_client.patch(f"/tags/{news}", json={"name": "NEWS"}).status_code == 200
    assert len(api_client.get("/tags").json()) == 2


def test_multiple_tags_and_idempotent_changes_do_not_overwrite_other_tags(
    api_client: TestClient,
    test_website: WebsiteRead,
) -> None:
    news, study = _create(api_client), _create(api_client, "Study")
    path = f"/websites/{test_website.id}/tags"
    assert api_client.get(path).json() == []
    for tag in (news, study, news):
        assert api_client.post(f"{path}/{tag}").status_code == 200
    assert {tag["id"] for tag in api_client.get(path).json()} == {news, study}
    for _ in range(2):
        response = api_client.delete(f"{path}/{news}")
        assert response.status_code == 200
        assert response.json() == [{"id": study, "name": "Study"}]
    assert api_client.delete(f"{path}/{study}").json() == []


def test_missing_or_invalid_tag_changes_keep_existing_tags(api_client: TestClient, test_website: WebsiteRead) -> None:
    news = _create(api_client)
    path = f"/websites/{test_website.id}/tags"
    api_client.post(f"{path}/{news}")
    missing = uuid.uuid4()
    for method in (api_client.post, api_client.delete):
        assert method(f"{path}/{missing}").status_code == 404
        assert method(f"{path}/bad").status_code == 422
        assert method(f"/websites/{missing}/tags/{news}").status_code == 404
    assert api_client.get(path).json() == [{"id": news, "name": "News"}]
    assert api_client.get(f"/websites/{missing}/tags").status_code == 404
    assert api_client.patch(f"/tags/{missing}", json={"name": "New"}).status_code == 404
    assert api_client.delete(f"/tags/{missing}").status_code == 404


def test_deleting_tag_removes_it_everywhere_and_preserves_websites_and_history(
    api_client: TestClient,
    session: Session,
    test_website: WebsiteRead,
) -> None:
    website = session.get(DBWebsite, test_website.id)
    assert website is not None
    second = DBWebsite(url="https://second.example/")
    scan = DBScanRun(website_id=website.id, scanned_at=datetime.now(UTC), status="success")
    session.add_all([second, scan])
    session.flush()
    recipients = [recipient.id for recipient in website.recipients]
    delay = website.recommended_delay
    news, study = _create(api_client), _create(api_client, "Study")
    for record in (website, second):
        api_client.post(f"/websites/{record.id}/tags/{news}")
    api_client.post(f"/websites/{website.id}/tags/{study}")
    assert api_client.delete(f"/tags/{news}").status_code == 204
    session.expire_all()
    assert session.get(DBWebsite, website.id) is website
    assert session.get(DBWebsite, second.id) is not None
    assert [recipient.id for recipient in website.recipients] == recipients
    assert website.recommended_delay == delay
    assert session.get(DBScanRun, scan.id) is not None
    assert api_client.get(f"/websites/{website.id}/tags").json() == [{"id": study, "name": "Study"}]
    assert api_client.get(f"/websites/{second.id}/tags").json() == []


def test_deleting_website_cleans_its_tags_and_leaves_shared_tags(
    api_client: TestClient,
    session: Session,
    test_website: WebsiteRead,
) -> None:
    news = _create(api_client)
    api_client.post(f"/websites/{test_website.id}/tags/{news}")
    website = session.get(DBWebsite, test_website.id)
    assert website is not None
    session.delete(website)
    session.flush()
    assert session.execute(select(website_tag_association)).all() == []
    assert session.get(DBWebsiteTag, uuid.UUID(news)) is not None


def test_dashboard_has_compact_tags_search_and_escaped_tag_names(
    api_client: TestClient,
    session: Session,
    test_website: WebsiteRead,
) -> None:
    session.add(DBWebsite(url="https://another.example/", card_title="Another"))
    session.flush()
    unusual = _create(api_client, '<img src=x onerror="alert(1)">')
    study = _create(api_client, "Study")
    api_client.post(f"/websites/{test_website.id}/tags/{unusual}")
    api_client.post(f"/websites/{test_website.id}/tags/{study}")
    response = api_client.get("/")
    assert response.status_code == 200
    page = BeautifulSoup(response.text, "html.parser")
    assert len(page.select("#monitored-website-list > .website-card")) == 2
    assert len(page.select(f'[data-website-id="{test_website.id}"] .website-tag')) == 2
    assert page.select_one(".website-card-header .website-tag-row") is None
    assert len(page.select(".card-body[hidden] .website-summary .website-tag-row")) == 2
    assert page.select_one(".website-tag img") is None
    assert page.select_one(".website-category") is None
    assert page.select_one(".websites-header #website-tag-filters") is not None
    assert page.select_one("#website-search") is not None
    assert page.select_one("#manage-tags-button") is not None
    assert page.select_one(".add-website-tags") is not None
    assert page.select_one(".run-scan-button") is not None


def test_tags_can_be_managed_before_any_websites_exist(api_client: TestClient) -> None:
    _create(api_client)
    response = api_client.get("/")
    assert response.status_code == 200
    page = BeautifulSoup(response.text, "html.parser")
    assert page.select_one("#manage-tags-button") is not None
    assert page.select_one("#tag-dialog") is not None
    assert page.select_one("#add-website-form") is not None


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/tags", {"name": "News"}),
        ("patch", f"/tags/{uuid.uuid4()}", {"name": "News"}),
        ("delete", f"/tags/{uuid.uuid4()}", None),
        ("post", f"/websites/{uuid.uuid4()}/tags/{uuid.uuid4()}", None),
        ("delete", f"/websites/{uuid.uuid4()}/tags/{uuid.uuid4()}", None),
    ],
)
def test_tag_writes_require_dashboard_token(
    api_client: TestClient, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    api_client.headers.pop(API_TOKEN_HEADER)
    response = api_client.request(method, path, json=payload)
    assert response.status_code == 403
