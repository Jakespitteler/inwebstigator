from collections.abc import Sequence

from app.backend.email_service.website_health import WebsiteHealth
from app.backend.links import website_name


def _website_names(website_urls: Sequence[str]) -> list[str]:
    """Names each website once, in the order given, e.g. "example.gov.au".

    Args:
        website_urls: The websites' URLs.

    Returns:
        Each website's name, without repeats.
    """
    return list(dict.fromkeys(website_name(url) for url in website_urls))


def _list_names(names: Sequence[str]) -> str:
    """Lists website names for a subject line, shortening long lists, e.g. "a.com and 3 other websites".

    Args:
        names: The website names.

    Returns:
        The names as a phrase.
    """
    if len(names) <= 2:
        return " and ".join(names)
    other_count: int = len(names) - 1
    return f"{names[0]} and {other_count} other websites"


def scan_report_subject(website_urls: Sequence[str]) -> str:
    """Writes the subject of a scan report email, naming the websites it covers.

    Args:
        website_urls: The URL of each website with a report in the email.

    Returns:
        The subject, e.g. "Website update: example.gov.au".
    """
    names: list[str] = _website_names(website_urls)
    heading: str = "Website update" if len(names) == 1 else "Website updates"
    return f"{heading}: {_list_names(names)}"


def manual_scan_subject(website_url: str) -> str:
    """Writes the subject of the email sent after a website is scanned from the dashboard.

    Args:
        website_url: The website that was scanned.

    Returns:
        The subject, e.g. "Manual scan: example.gov.au".
    """
    return f"Manual scan: {website_name(website_url)}"


def health_check_subject(website_healths: Sequence[WebsiteHealth]) -> str:
    """Writes the subject of a health check email, saying straight away if anything needs attention.

    Args:
        website_healths: How monitoring is going for each of the recipient's websites.

    Returns:
        The subject, e.g. "Health check: monitoring is running for 3 websites".
    """
    website_count: int = len(website_healths)
    websites: str = "1 website" if website_count == 1 else f"{website_count} websites"
    attention_count: int = sum(health.needs_attention for health in website_healths)
    if attention_count:
        verb: str = "needs" if attention_count == 1 else "need"
        return f"Health check: {attention_count} of {websites} {verb} attention"
    return f"Health check: monitoring is running for {websites}"
