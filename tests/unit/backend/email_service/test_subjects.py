import pytest

from app.backend.email_service.email_wording import (
    WebsiteHealth,
    health_check_subject,
    manual_scan_subject,
    scan_report_subject,
)


@pytest.mark.parametrize(
    ("website_urls", "subject"),
    [
        (["https://www.example.gov.au/"], "Website update: example.gov.au"),
        (["https://a.example/", "https://a.example/"], "Website update: a.example"),
        (["https://a.example/", "https://b.example/"], "Website updates: a.example and b.example"),
        (
            ["https://a.example/", "https://b.example/", "https://c.example/"],
            "Website updates: a.example and 2 other websites",
        ),
    ],
)
def test_scan_report_subject_names_the_websites(website_urls: list[str], subject: str) -> None:
    assert scan_report_subject(website_urls) == subject


def test_manual_scan_subject_names_the_website() -> None:
    assert manual_scan_subject("https://www.example.gov.au/fees") == "Manual scan: example.gov.au"


def test_health_check_subject_when_all_is_well() -> None:
    healths = [WebsiteHealth("https://a.example/", "Working.", False), WebsiteHealth("https://b.example/", "", False)]

    assert health_check_subject(healths) == "Health check: monitoring is running for 2 websites"


def test_health_check_subject_when_a_website_needs_attention() -> None:
    healths = [WebsiteHealth("https://a.example/", "Working.", False), WebsiteHealth("https://b.example/", "", True)]

    assert health_check_subject(healths) == "Health check: 1 of 2 websites needs attention"
