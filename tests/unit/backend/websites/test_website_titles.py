import pytest

from app.backend.websites.website_titles import website_card_title


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
def test_website_card_title_includes_path(url: str, html: str | None, expected: str) -> None:
    assert website_card_title(url, html) == expected


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://webloom-two.vercel.app/test-site", None, "Webloom Two - Test Site"),
        ("https://aria.github.io/", None, "Aria"),
        ("https://another-user.github.io/project/", None, "Another User - Project"),
        ("https://my-site.netlify.app/news/", None, "My Site - News"),
        ("https://www.my-site.netlify.app/", None, "My Site"),
        ("https://preview.my-site.netlify.app/", None, "My Site Preview"),
        ("https://vercel.app/", None, "Vercel"),
        ("https://notvercel.app/", None, "Notvercel"),
        ("https://aria.github.io.example.com/", None, "Example Aria Github Io"),
        ("https://uwa.github.io/study/", "<title>UWA | Study</title>", "UWA - Study"),
        (
            "https://webloom-two.vercel.app/test-site",
            '<meta property="og:site_name" content="Webloom Two">',
            "Webloom Two - Test Site",
        ),
    ],
)
def test_website_card_title_uses_hosted_site_identity(url: str, html: str | None, expected: str) -> None:
    assert website_card_title(url, html) == expected


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://education.nsw.gov.au/", "<title>NSW Department of Education</title>", "Education NSW"),
        ("https://education.vic.gov.au/", None, "Education VIC"),
        ("https://www.health.wa.gov.au/", "<title>WA Health</title>", "Health WA"),
        ("https://www.det.wa.edu.au/schools/", None, "Det WA - Schools"),
        ("https://www.nsw.gov.au/", "<title>NSW Government</title>", "NSW"),
    ],
    ids=["nsw-department", "vic-department", "wa-department", "wa-education-with-path", "state-portal"],
)
def test_website_card_title_names_a_state_website_by_its_department_and_state(
    url: str, html: str | None, expected: str
) -> None:
    """Tests an Australian state government or education website (e.g. ending ".nsw.gov.au") is named by its own
    label and its state, rather than by its state alone, so two departments of one state can be told apart."""
    assert website_card_title(url, html) == expected


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://example.com/", None, "Example"),
        ("https://news.example.com/", None, "Example News"),
        ("https://handbook.uwa.edu.au/courses/", "<title>Handbook | UWA</title>", "UWA Handbook - Courses"),
        ("https://www.example.com/", None, "Example"),
    ],
    ids=["no-subdomain", "subdomain", "subdomain-with-site-name-and-path", "www-is-not-a-subdomain"],
)
def test_website_card_title_keeps_subdomains(url: str, html: str | None, expected: str) -> None:
    """Tests a subdomain is kept in the title, after the website's name, so news.example.com and example.com can be
    told apart, while "www." is left out."""
    assert website_card_title(url, html) == expected
