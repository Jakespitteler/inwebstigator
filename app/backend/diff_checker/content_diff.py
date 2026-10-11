import re
from collections.abc import Sequence

from app.backend.diff_checker.block_comparison import find_changed_regions, match_text_found_on_both_pages, pair_edits
from app.backend.diff_checker.models import ContentDiff, DiffSettings, LinkDiff, MatchedText, PageContent
from app.backend.diff_checker.page_parser import normalise_text
from app.core.urls import page_key
from app.models.content_block_models import ContentBlock


def compare_page_content(
    old_content: PageContent,
    new_content: PageContent,
    settings: DiffSettings | None = None,
) -> ContentDiff:
    """Compares two PageContent objects to identify added, removed, edited and moved content blocks.

    Works in three steps:
        1. `find_changed_regions`: blocks are lined up in page order by their parent heading and text. Blocks in
           an unchanged run are unchanged, and the stretches between unchanged runs are the page's changed regions.
        2. `match_text_found_on_both_pages`: a leftover block whose exact text is still anywhere on the other page
           is matched with it. It is unchanged if it stayed in its section, or now sits under a renamed heading
           or a different HTML tag (a renamed heading is reported through the heading's own block instead).
           It is reported as moved if it went to a different section that is on both versions of the page.
        3. `pair_edits`: what is left in each region is paired by how similar the text is (most similar first),
           not by position, so an edit is matched to the right block. Unpaired blocks are reported as added
           or removed.

    As a result, the same text can never be reported as both added and removed.

    Args:
        old_content: The PageContent object representing the previous state.
        new_content: The PageContent object representing the current state.
        settings: The similarity threshold and comparison limit. Defaults to the app's configured settings.

    Returns:
        A ContentDiff of the added, removed and changed blocks, which unpacks like a tuple of those three lists.
        Moved blocks are in the changed list, with the same text and a different section.
    """
    diff_settings: DiffSettings = settings or DiffSettings.from_config()
    matched_text: MatchedText = match_text_found_on_both_pages(
        regions=find_changed_regions(old_content.blocks, new_content.blocks),
        old_sections=old_content.sections,
        new_sections=new_content.sections,
    )
    edits: ContentDiff = ContentDiff.combine([pair_edits(region, diff_settings) for region in matched_text.regions])
    return edits.with_moves(matched_text.moves)


def _links_to_pages_not_in(links: list[str], other_links: list[str]) -> list[str]:
    """Finds the links that go to a page none of the other links go to.

    Links are compared as pages (see `page_key`), so a link written differently for a page the other links
    also go to (e.g. with "www.") is not counted. Links found exactly as written in the other links are skipped
    first, so the slower page comparison only runs when something has changed.

    Args:
        links: URL strings to check.
        other_links: URL strings to check them against.

    Returns:
        The links, each only once and in their original order, whose page is not among the other links.
    """
    other_link_set: set[str] = set(other_links)
    unmatched_links: list[str] = [link for link in dict.fromkeys(links) if link not in other_link_set]
    if not unmatched_links:
        return []
    other_pages: set[str] = {page_key(link) for link in other_links}
    return [link for link in unmatched_links if page_key(link) not in other_pages]


def find_added_links(previous_state: list[str], current_state: list[str]) -> list[str]:
    """Identifies links that were added between a previous state and a current state.

    A link now written differently for a page it already had (e.g. with "www.") is not counted as added.

    Args:
        previous_state: A list of URL strings representing the initial state.
        current_state: A list of URL strings representing the updated state.

    Returns:
        A list of URL strings for pages in current_state but missing from previous_state.
    """
    return _links_to_pages_not_in(current_state, previous_state)


def find_removed_links(previous_state: list[str], current_state: list[str]) -> list[str]:
    """Identifies links that were removed between a previous state and a current state.

    A link now written differently for a page it still has (e.g. without "www.") is not counted as removed.

    Args:
        previous_state: A list of URL strings representing the initial state.
        current_state: A list of URL strings representing the updated state.

    Returns:
        A list of URL strings for pages in previous_state but missing from current_state.
    """
    return _links_to_pages_not_in(previous_state, current_state)


def find_link_difference(previous_state: list[str], current_state: list[str]) -> LinkDiff:
    """Compares stored links against newly extracted links to determine additions and removals.

    Args:
        previous_state: A list of URL strings representing the stored links.
        current_state: A list of URL strings representing the newly found links.

    Returns:
        The added and removed URL strings, which unpack like a tuple of those two lists.
    """
    return LinkDiff(
        added=find_added_links(previous_state, current_state),
        removed=find_removed_links(previous_state, current_state),
    )


def has_lost_most_content(
    old_content: PageContent,
    new_content: PageContent,
    settings: DiffSettings | None = None,
) -> bool:
    """Checks whether a new version of a page has lost most of its text compared with the old version.

    A page that suddenly has only a fraction of its text is almost always a stand-in served in its place, such
    as a "Just a moment..." browser check, a maintenance page or a login wall, rather than the page's real new
    content. Treating it as the new content would report everything as removed, then everything as added again
    once the real page is back.

    Args:
        old_content: The page's saved content.
        new_content: The content just fetched.
        settings: The share of text the new page must keep, and how much text the old page needs before the
            check applies. Defaults to the app's configured settings.

    Returns:
        True if the old page had enough text to judge and the new page has less than the minimum share of it.
    """
    diff_settings: DiffSettings = settings or DiffSettings.from_config()
    if old_content.text_length < diff_settings.min_content_chars:
        return False
    return new_content.text_length < old_content.text_length * diff_settings.min_content_ratio


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
    return normalise_text(text)


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
