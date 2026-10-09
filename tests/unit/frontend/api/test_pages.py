import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

# ==========================
#  Shared header and footer
# ==========================


def _get_page(api_client: TestClient, path: str) -> BeautifulSoup:
    response = api_client.get(path)
    assert response.status_code == 200, response.text
    return BeautifulSoup(response.text, "html.parser")


@pytest.mark.parametrize("path", ["/", "/about"])
def test_app_name_links_back_to_the_dashboard(api_client: TestClient, path: str) -> None:
    """Tests the "Inwebstigator" heading is a link to the dashboard on every page."""
    page = _get_page(api_client, path)

    home_link = page.select_one(".dashboard-header h1 a")
    assert home_link is not None
    assert home_link.get_text(strip=True) == "Inwebstigator"
    assert home_link["href"].endswith("/")


@pytest.mark.parametrize(("path", "current_link"), [("/", "Dashboard"), ("/about", "About")])
def test_every_page_has_the_footer(api_client: TestClient, path: str, current_link: str) -> None:
    """Tests every page has the copyright footer, linking to the About page, with the current page marked."""
    page = _get_page(api_client, path)

    footer = page.select_one("footer.site-footer")
    assert footer is not None
    assert "©" in footer.get_text()
    assert "Inwebstigator team" in footer.get_text()

    links = {link.get_text(strip=True): link for link in footer.select("a")}
    assert links["About"]["href"].endswith("/about")
    assert links["Dashboard"]["href"].endswith("/")
    assert links[current_link].get("aria-current") == "page"


@pytest.mark.parametrize("path", ["/", "/about"])
def test_every_page_loads_the_shared_theme_script(api_client: TestClient, path: str) -> None:
    """Tests every page loads the light/dark theme script, so the toggle works and the theme is remembered."""
    page = _get_page(api_client, path)

    assert page.select_one('script[src$="/static/theme.js"]') is not None
    assert page.select_one("#themeToggleBtn") is not None
    assert api_client.get("/static/theme.js").status_code == 200


@pytest.mark.parametrize(("path", "has_refresh"), [("/", True), ("/about", False)])
def test_refresh_button_is_only_on_the_dashboard(api_client: TestClient, path: str, has_refresh: bool) -> None:
    """Tests the dashboard has a Refresh button in its header, and the About page, with nothing to refresh, does not."""
    page = _get_page(api_client, path)

    refresh_button = page.select_one(".dashboard-header #reloadPageBtn")
    assert (refresh_button is not None) is has_refresh
    if refresh_button is not None:
        assert refresh_button.get_text(strip=True) == "Refresh"
        assert refresh_button.get("type") == "button"


# ==========================
#  About page
# ==========================


def test_about_page_describes_the_app_team_contact_and_copyright(api_client: TestClient) -> None:
    """Tests the About page has what the app does, the team, contact details and copyright."""
    page = _get_page(api_client, "/about")

    headings = [heading.get_text(strip=True) for heading in page.select("h2.section-title")]
    assert headings == ["About Inwebstigator", "The team", "Contact", "Copyright"]

    team = [member.get_text(strip=True) for member in page.select(".about-team li")]
    assert "Patrick Caputi" in team
    assert len(team) == 6

    assert "©" in page.select_one(".about-page").get_text()
    assert page.select_one("a.back-link")["href"].endswith("/")
