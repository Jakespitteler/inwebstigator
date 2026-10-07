import re
from collections.abc import Sequence

from app.backend.diff_checker.models import ContentBlock, PageContent
from app.backend.diff_checker.page_parser import normalize_text


def _without_matches(text: str, patterns: Sequence[re.Pattern[str]]) -> str:
    """Removes every part of a block's text that matches one of the patterns.

    Args:
        text: The block's text.
        patterns: The compiled ignore rules.

    Returns:
        The text that is left, with its spacing tidied, or an empty string if nothing is left.
    """
    for pattern in patterns:
        text = pattern.sub("", text)
    return normalize_text(text)


def without_ignored_text(content: PageContent, ignore_rules: Sequence[str]) -> PageContent:
    """Removes the text matching a critical page's ignore rules, so text that changes on every scan is not reported.

    Each rule is a regular expression, e.g. `Page last updated: .*` or `\\d+ people found this useful`. Only the
    matching part of a block is removed, and a block with no text left is dropped. Rules are applied to both the
    saved and the new version of the page, so adding a rule never reports a change by itself.

    Args:
        content: The parsed page.
        ignore_rules: The page's ignore rules, already checked to be valid regular expressions.

    Returns:
        The page without the ignored text. The same object is returned when there are no rules.
    """
    if not ignore_rules:
        return content

    patterns: list[re.Pattern[str]] = [re.compile(rule) for rule in ignore_rules]
    kept_blocks: list[ContentBlock] = [
        block.model_copy(update={"text": remaining_text})
        for block in content.blocks
        if (remaining_text := _without_matches(block.text, patterns))
    ]
    return content.model_copy(update={"blocks": kept_blocks})
