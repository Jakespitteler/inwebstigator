import re
from functools import cache
from typing import NamedTuple

from bs4 import BeautifulSoup, Tag

from app.core.paths import resource_path

CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


class CssRule(NamedTuple):
    """One rule of the stylesheet.

    Attributes:
        selector: Which tags the rule is for, e.g. ".badge.added".
        declarations: The property names and values, e.g. {"color": "#1f2328"}.
    """

    selector: str
    declarations: dict[str, str]


def _parse_declarations(block: str) -> dict[str, str]:
    """Reads the property names and values from the inside of a rule's braces.

    Args:
        block: The declarations, e.g. "color: red; margin: 0;".

    Returns:
        The property names and values, in the order they were written.
    """
    pairs = (declaration.partition(":") for declaration in block.split(";") if ":" in declaration)
    return {name.strip(): value.strip() for name, _, value in pairs}


def parse_stylesheet(css: str) -> list[CssRule]:
    """Reads the rules of a stylesheet.

    Args:
        css: The stylesheet text. It may have comments but no nested rules (such as media queries).

    Returns:
        The rules, in the order they were written.
    """
    return [
        CssRule(selector.strip(), _parse_declarations(block))
        for selector, block in CSS_RULE.findall(CSS_COMMENT.sub("", css))
    ]


@cache
def _email_rules() -> list[CssRule]:
    """Loads the email stylesheet once, the first time an email is written.

    Returns:
        The rules of the email stylesheet.
    """
    stylesheet = resource_path("app", "backend", "email_service", "templates", "email.css")
    return parse_stylesheet(stylesheet.read_text(encoding="utf-8"))


def _write_style(tag: Tag, declarations: dict[str, str]) -> None:
    """Puts the declarations in a tag's style attribute. A declaration written later wins over an earlier one.

    Args:
        tag: The tag to style.
        declarations: The property names and values to add.
    """
    existing: str = str(tag.get("style", ""))
    merged: dict[str, str] = {**_parse_declarations(existing), **declarations}
    tag["style"] = " ".join(f"{name}: {value};" for name, value in merged.items())


def inline_styles(html: str, rules: list[CssRule] | None = None) -> str:
    """Copies the stylesheet's rules into each tag's style attribute.

    Email apps ignore or remove linked stylesheets, so the styles have to be written on the tags themselves.

    Args:
        html: The email's HTML, which uses classes for its styling.
        rules: The stylesheet rules to use. Defaults to the email stylesheet.

    Returns:
        The HTML with every matching tag styled.
    """
    soup = BeautifulSoup(html, "html.parser")
    for rule in rules if rules is not None else _email_rules():
        for tag in soup.select(rule.selector):
            _write_style(tag, rule.declarations)
    return str(soup)
