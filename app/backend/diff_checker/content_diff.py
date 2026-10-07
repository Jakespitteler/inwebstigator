from app.backend.diff_checker.changed_regions import find_changed_regions, match_text_found_on_both_pages
from app.backend.diff_checker.edit_pairing import pair_edits
from app.backend.diff_checker.models import ContentDiff, MatchedText, PageContent
from app.backend.diff_checker.settings import DiffSettings


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
