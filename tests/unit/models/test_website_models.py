"""Tests of the website models: what is accepted when a website is added or its settings are changed."""

from typing import Any

import pytest
from pydantic import HttpUrl, ValidationError

from app.core.config import config
from app.models.website_models import WebsiteCreate, WebsiteSettingsUpdate, WebsiteUpdate


@pytest.mark.parametrize(
    ("typed_url", "saved_url"),
    [
        ("example.com", "https://example.com/"),
        ("www.example.com/research", "https://www.example.com/research"),
        ("http://example.com", "http://example.com/"),
    ],
)
def test_a_website_typed_without_https_has_it_added(typed_url: str, saved_url: str) -> None:
    """Tests a website typed without "https://" is given it, while one typed with its own scheme keeps it."""
    assert str(WebsiteCreate.model_validate({"url": typed_url}).url) == saved_url


def test_critical_pages_are_turned_into_full_urls_on_the_website() -> None:
    """Tests critical pages typed as full URLs, URLs without "https://" or links relative to the website are all
    saved as full URLs on the website."""
    website = WebsiteCreate(
        url=HttpUrl("https://example.com"),
        critical_pages=["/news", "example.com/fees", "https://www.example.com/dates"],
    )

    assert website.critical_pages == [
        "https://example.com/news",
        "https://example.com/fees",
        "https://www.example.com/dates",
    ]


@pytest.mark.parametrize("page_url", ["https://other.example.com/news", "//other.example.com/news"])
def test_a_critical_page_on_another_website_is_refused(page_url: str) -> None:
    """Tests a critical page on a different website is refused, so the website is not added with it."""
    with pytest.raises(ValidationError, match="is not a page on"):
        WebsiteCreate(url=HttpUrl("https://example.com"), critical_pages=[page_url])


def test_a_critical_page_that_is_not_a_valid_url_is_refused() -> None:
    """Tests a critical page that cannot be read as a URL is refused."""
    with pytest.raises(ValidationError):
        WebsiteCreate(url=HttpUrl("https://example.com"), critical_pages=["https://[example.com/news"])


def test_a_recipient_email_that_is_not_an_email_address_is_refused() -> None:
    """Tests a website is not added with a recipient whose address is not an email address."""
    with pytest.raises(ValidationError, match="email address"):
        WebsiteCreate(url=HttpUrl("https://example.com"), recipient_emails=["not-an-email"])


@pytest.mark.parametrize("model", [WebsiteCreate, WebsiteUpdate, WebsiteSettingsUpdate])
def test_scans_cannot_be_closer_together_than_the_minimum(model: type[WebsiteCreate | WebsiteUpdate]) -> None:
    """Tests the days between a website's scans cannot be set below the minimum, but can be set to it."""
    minimum: float = config.scheduler_minimum_days_between_scans
    fields: dict[str, Any] = {"url": "https://example.com"} if model is WebsiteCreate else {}

    with pytest.raises(ValidationError, match="greater than or equal"):
        model(**fields, days_between_scans=minimum / 2)
    assert model(**fields, days_between_scans=minimum).days_between_scans == minimum


def test_website_settings_only_change_the_settings_that_were_given() -> None:
    """Tests a settings change only updates the settings sent, and ignores fields only the scanner may save."""
    settings = WebsiteSettingsUpdate.model_validate({"active": False, "failed_attempts_at_min_speed": 99})

    assert settings.as_website_update().model_dump(exclude_unset=True) == {"active": False}
