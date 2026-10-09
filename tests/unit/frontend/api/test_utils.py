from datetime import UTC, datetime

import pytest

from app.frontend.api.utils import format_timestamp, scan_time, website_name


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 10, 4, 9, 5).astimezone(), "04 Oct 2026, 09:05"),
        (datetime(2026, 12, 31, 23, 59).astimezone(), "31 Dec 2026, 23:59"),
        (None, "Not scanned yet"),
    ],
)
def test_scan_time(value: datetime | None, expected: str):
    """Tests scan times are shown as a 24-hour date and time, or a note when there hasn't been a scan."""
    assert scan_time(value) == expected


def test_utc_times_are_shown_in_the_computers_own_time_zone() -> None:
    """Tests a time saved in UTC is shown on the dashboard in the user's own time, not in UTC."""
    saved_time = datetime(2026, 10, 4, 1, 5, tzinfo=UTC)

    assert format_timestamp(saved_time) == f"{saved_time.astimezone():%d %b %Y, %H:%M}"


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://www.cnet.com/culture/entertainment/", None, "Cnet - Culture - Entertainment"),
        ("https://www.cnet.com/", None, "Cnet"),
        ("https://www.cnet.com/culture/?sort=new#latest", None, "Cnet - Culture"),
        ("https://example.com/latest-news/summer_sale/", None, "Example - Latest News - Summer Sale"),
        ("https://example.com/caf%C3%A9/", None, "Example - Café"),
        ("https://example.com//news///", None, "Example - News"),
        ("https://www.uwa.edu.au/study/", "<title>UWA</title>", "UWA - Study"),
        ("https://theguardian.com/au/", "<title>The Guardian</title>", "The Guardian - Au"),
        ("https://127.0.0.1/news/", None, "127.0.0.1 - News"),
        ("", None, "Website"),
    ],
)
def test_website_name_includes_path(url: str, html: str | None, expected: str) -> None:
    assert website_name(url, html) == expected


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://webloom-two.vercel.app/test-site", None, "Webloom Two - Test Site"),
        ("https://aria.github.io/", None, "Aria"),
        ("https://another-user.github.io/project/", None, "Another User - Project"),
        ("https://my-site.netlify.app/news/", None, "My Site - News"),
        ("https://www.my-site.netlify.app/", None, "My Site"),
        ("https://preview.my-site.netlify.app/", None, "My Site"),
        ("https://vercel.app/", None, "Vercel"),
        ("https://notvercel.app/", None, "Notvercel"),
        ("https://aria.github.io.example.com/", None, "Example"),
        ("https://uwa.github.io/study/", "<title>UWA | Study</title>", "UWA - Study"),
        (
            "https://webloom-two.vercel.app/test-site",
            '<meta property="og:site_name" content="Webloom Two">',
            "Webloom Two - Test Site",
        ),
    ],
)
def test_website_name_uses_hosted_site_identity(url: str, html: str | None, expected: str) -> None:
    assert website_name(url, html) == expected
