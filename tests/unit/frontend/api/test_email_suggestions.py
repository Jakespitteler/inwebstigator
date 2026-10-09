from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.schema import DBRecipient, DBWebsite


def test_email_suggestions_work_with_no_saved_recipients(api_client: TestClient) -> None:
    response = api_client.get("/")
    assert response.status_code == 200
    page = BeautifulSoup(response.text, "html.parser")
    assert len(page.select("#saved-recipient-emails")) == 1
    assert page.select("#saved-recipient-emails option") == []
    field = page.select_one(".recipient-email-input")
    assert field is not None
    assert field.has_attr("data-saved-email-input")
    assert field["type"] == "email"
    assert field.get("value", "") == ""


def test_email_suggestions_include_all_saved_addresses_once(api_client: TestClient, session: Session) -> None:
    shared = DBRecipient(email="Shared@example.com")
    orphan = DBRecipient(email="a&b@example.com")
    # Include more than the standard recipient page size, and an address with no linked website.
    extra = [DBRecipient(email=f"person{number:03}@example.com") for number in range(105)]
    session.add_all(
        [
            DBWebsite(url="https://one.example.com", recipients=[shared]),
            DBWebsite(url="https://two.example.com", recipients=[shared]),
            orphan,
            *extra,
        ]
    )
    session.flush()
    expected = sorted([shared.email, orphan.email, *(person.email for person in extra)], key=str.casefold)

    response = api_client.get("/")
    assert response.status_code == 200
    page = BeautifulSoup(response.text, "html.parser")
    assert [option["value"] for option in page.select("#saved-recipient-emails option")] == expected
    assert "a&amp;b@example.com" in response.text
    fields = page.select(".recipient-email-input, .new-recipient-email")
    assert len(fields) == 3
    assert all(field.has_attr("data-saved-email-input") for field in fields)
    assert all(field["type"] == "email" and field.get("value", "") == "" for field in fields)

    # Suggestions reflect saved records on the next load, rather than stale browser storage.
    session.delete(orphan)
    session.add(DBRecipient(email="new@example.com"))
    session.flush()
    refreshed = BeautifulSoup(api_client.get("/").text, "html.parser")
    values = [option["value"] for option in refreshed.select("#saved-recipient-emails option")]
    assert "new@example.com" in values
    assert "a&b@example.com" not in values
