"""The friendly title each website's dashboard cards show, e.g. "UWA - Study" for uwa.edu.au/study.

Emails and the add website form name websites by `website_name` in `app.core.urls` instead (e.g. "uwa.edu.au/study").
The title is read from the website's saved home page, which is slow for a large page, so each scan works it out once
and saves it rather than the dashboard working it out every time it loads.
"""

import re
from ipaddress import ip_address
from typing import NamedTuple
from urllib.parse import SplitResult, unquote, urlsplit

from bs4 import BeautifulSoup
from bs4.filter import SoupStrainer

from app.models.website_models import WebsiteRead

# Second-level endings under a country's two-letter ending, e.g. the "edu" of uwa.edu.au and the "co" of bbc.co.uk
COUNTRY_CATEGORIES: frozenset[str] = frozenset({"ac", "asn", "co", "com", "edu", "gov", "id", "mil", "net", "org"})
# Providers that host separate websites on their subdomains, so the website is named after the subdomain
HOSTING_DOMAINS: frozenset[str] = frozenset({"vercel.app", "github.io", "netlify.app"})
# Australian states and territories, whose government and education websites end e.g. ".nsw.gov.au"
AUSTRALIAN_STATES: frozenset[str] = frozenset({"act", "nsw", "nt", "qld", "sa", "tas", "vic", "wa"})
AUSTRALIAN_STATE_CATEGORIES: frozenset[str] = frozenset({"gov", "edu"})
# Site-wide names in a page's metadata, which name the website rather than the page
SITE_NAME_META_TAGS: frozenset[str] = frozenset({"og:site_name", "application-name"})


class HostParts(NamedTuple):
    """The parts of a website's host name that its title is made from.

    Attributes:
        name: The label that identifies the website, e.g. "uwa" for handbook.uwa.edu.au.
        state: The Australian state or territory of a state government or education website, e.g. "nsw" for
            education.nsw.gov.au, otherwise None.
        subdomains: The labels before the name, e.g. ["handbook"] for handbook.uwa.edu.au.
    """

    name: str
    state: str | None
    subdomains: list[str]


def _state_ending(labels: list[str]) -> str | None:
    """Finds the Australian state or territory a host name's ending belongs to, e.g. "nsw" for education.nsw.gov.au.

    Args:
        labels: The host name's labels, without a leading "www.", e.g. ["education", "nsw", "gov", "au"].

    Returns:
        The state or territory, or None if the host name does not end with one (e.g. ".nsw.gov.au").
    """
    has_state_ending: bool = (
        len(labels) >= 4
        and labels[-1] == "au"
        and labels[-2] in AUSTRALIAN_STATE_CATEGORIES
        and labels[-3] in AUSTRALIAN_STATES
    )
    return labels[-3] if has_state_ending else None


def _shared_ending_length(labels: list[str]) -> int:
    """Counts how many labels at the end of a host name are an ending shared by many websites.

    For example 3 for education.nsw.gov.au (".nsw.gov.au"), 2 for uwa.edu.au (".edu.au") and my-site.netlify.app
    (".netlify.app"), and 1 for example.com (".com").

    Args:
        labels: The host name's labels, without a leading "www.".

    Returns:
        How many labels the shared ending has, or 0 for a host name with a single label (e.g. "localhost").
    """
    if _state_ending(labels):
        return 3
    if len(labels) >= 3 and (
        ".".join(labels[-2:]) in HOSTING_DOMAINS or (len(labels[-1]) == 2 and labels[-2] in COUNTRY_CATEGORIES)
    ):
        return 2
    return 1 if len(labels) > 1 else 0


def _host_parts(host_name: str) -> HostParts:
    """Splits a host name into the label that identifies the website, its state (if any) and its subdomains.

    Args:
        host_name: The website's host name, e.g. "www.handbook.uwa.edu.au".

    Returns:
        The parts, e.g. the name "uwa" with the subdomain "handbook".
    """
    labels: list[str] = host_name.removeprefix("www.").split(".")
    name_position: int = len(labels) - _shared_ending_length(labels) - 1
    return HostParts(name=labels[name_position], state=_state_ending(labels), subdomains=labels[:name_position])


def _readable(label: str) -> str:
    """Writes part of a URL as words, e.g. "summer_sale" as "Summer Sale".

    Args:
        label: A host name label or path section.

    Returns:
        The words, each starting with a capital.
    """
    return unquote(label).replace("-", " ").replace("_", " ").strip().title()


def _site_name_candidates(html: str) -> list[str]:
    """Lists the names a home page gives its website: its site-wide names from its metadata, then its title.

    Args:
        html: The website's saved home page.

    Returns:
        The candidate names, in the order to try them.
    """
    soup = BeautifulSoup(html, "html.parser", parse_only=SoupStrainer(["title", "meta"]))
    candidates: list[str] = [
        str(meta.get("content") or "")
        for meta in soup.find_all("meta")
        if str(meta.get("property") or meta.get("name") or "").lower() in SITE_NAME_META_TAGS
    ]
    candidates.extend(title.get_text(" ", strip=True) for title in soup.find_all("title"))
    return candidates


def _words_spelling(target: str, candidate: str) -> str | None:
    """Finds the words in a candidate name that spell a target when joined, e.g. "UWA" in "Study | UWA".

    Args:
        target: The website's name label, lower case without punctuation, e.g. "uwa".
        candidate: A name the home page gives its website.

    Returns:
        The matching words, keeping the website's own capitals, or None if no words spell the target.
    """
    words: list[str] = re.findall(r"[^\W_]+", candidate)
    for start in range(len(words)):
        combined: str = ""
        for end in range(start, len(words)):
            combined += words[end].casefold()
            if combined == target:
                return " ".join(word[0].upper() + word[1:] for word in words[start : end + 1])
            if not target.startswith(combined):
                break
    return None


def _name_from_html(name: str, html: str) -> str | None:
    """Finds how the website writes its own name in its saved home page, e.g. "UWA" rather than "Uwa".

    Only words that spell the domain's name label are used, so an article's title is never taken as the name.

    Args:
        name: The label that identifies the website, e.g. "uwa".
        html: The website's saved home page.

    Returns:
        The name as the website writes it, or None if it cannot be found.
    """
    target: str = re.sub(r"[\W_]+", "", name).casefold()
    return next(
        (words for candidate in _site_name_candidates(html) if (words := _words_spelling(target, candidate))),
        None,
    )


def _with_path(name: str, path: str) -> str:
    """Adds the readable sections of a URL's path to a title, e.g. "Example - Latest News".

    Args:
        name: The website's name, including any state and subdomains.
        path: The URL's path, without its query or #section.

    Returns:
        The name, followed by each path section.
    """
    sections: list[str] = [_readable(section) for section in path.split("/") if section]
    return " - ".join([name, *(section for section in sections if section)])


def website_card_title(url: str, html: str | None = None) -> str:
    """Writes the friendly title a website's dashboard cards show.

    The title is the label that identifies the website, written the way the website writes it in its saved home
    page's metadata or title where it can be found, then its state for an Australian state website, then any
    subdomains, then its path. For example "UWA - Study" for uwa.edu.au/study, "Education NSW" for
    education.nsw.gov.au and "Example News" for news.example.com, so websites that share a name can be told apart.

    Args:
        url: The website's URL.
        html: The website's saved home page, or None if it has not been saved yet.

    Returns:
        The title, or "Website" if the URL cannot be read or has no host name.
    """
    try:
        parsed_url: SplitResult = urlsplit(url)
        host_name: str = (parsed_url.hostname or "").rstrip(".")
    except ValueError:
        return "Website"
    if not host_name:
        return "Website"

    try:
        ip_address(host_name)
        return _with_path(host_name, parsed_url.path)
    except ValueError:
        pass

    parts: HostParts = _host_parts(host_name)
    name: str = (html and _name_from_html(parts.name, html)) or _readable(parts.name)
    state: list[str] = [parts.state.upper()] if parts.state else []
    subdomains: list[str] = [_readable(subdomain) for subdomain in parts.subdomains]
    return _with_path(" ".join([name, *state, *subdomains]), parsed_url.path)


def saved_home_page_html(website: WebsiteRead) -> str | None:
    """Finds the HTML saved for a website's home page, which its card title is read from.

    Args:
        website: The website, with its critical pages.

    Returns:
        The saved HTML, or None if the home page has not been saved yet.
    """
    return next(
        (page.text_body for page in website.critical_pages if page.url == website.url and page.text_body),
        None,
    )
