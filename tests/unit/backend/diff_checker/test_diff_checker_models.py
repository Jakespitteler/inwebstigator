import pytest

from app.backend.diff_checker.models import ContentDiff, DiffSettings, PageContent
from app.core.config import config
from app.models.content_block_models import ChangedBlock, ContentBlock, HTMLBlockType


def block(text: str, heading: str | None = "Fees") -> ContentBlock:
    """Builds a paragraph block with the given text and section heading."""
    return ContentBlock(parent_heading=heading, block_type=HTMLBlockType.PARAGRAPH, text=text)


def change(old_text: str, new_text: str, old_heading: str = "Fees", new_heading: str = "Fees") -> ChangedBlock:
    """Builds a changed block from its old and new text and section headings."""
    return ChangedBlock(old_block=block(old_text, old_heading), new_block=block(new_text, new_heading), similarity=1.0)


def test_page_sections_include_every_heading_and_the_part_before_the_first_heading() -> None:
    """Tests a page's sections are its headings plus the section of each block, such as "No heading"."""
    content = PageContent(headings=["Fees", "Hours"], blocks=[block("Intro", "No heading"), block("$50", "Fees")])

    assert content.sections == {"Fees", "Hours", "No heading"}


def test_page_text_length_counts_the_text_of_every_block() -> None:
    """Tests a page's text length adds up the characters of all its blocks."""
    assert PageContent(blocks=[block("Fee is $50."), block("Open 9 to 5")]).text_length == 22
    assert PageContent().text_length == 0


def test_a_changed_block_with_the_same_text_is_a_move() -> None:
    """Tests a block that kept its text and changed section is a move, and an edited block is not."""
    assert change("Closed", "Closed", old_heading="Saturday", new_heading="Sunday").is_move is True
    assert change("Fee is $50.", "Fee is $60.").is_move is False


def test_combine_joins_diffs_in_order() -> None:
    """Tests joining the diffs of several changed regions keeps every block, in the order of the regions."""
    first = ContentDiff(added=[block("A1")], removed=[block("R1")], changed=[change("C1", "C1 edited")])
    second = ContentDiff(added=[block("A2")], removed=[], changed=[change("C2", "C2 edited")])

    combined = ContentDiff.combine([first, second])

    assert [item.text for item in combined.added] == ["A1", "A2"]
    assert [item.text for item in combined.removed] == ["R1"]
    assert [item.old_block.text for item in combined.changed] == ["C1", "C2"]
    assert ContentDiff.combine([]) == ([], [], [])


def test_with_moves_lists_moves_after_edits_without_changing_the_original() -> None:
    """Tests moves are added after the edits in a copy of the diff, leaving the original diff as it was."""
    edit = change("Fee is $50.", "Fee is $60.")
    move = change("Closed", "Closed", old_heading="Saturday", new_heading="Sunday")
    diff = ContentDiff(added=[block("New")], removed=[], changed=[edit])

    with_moves = diff.with_moves([move])

    assert with_moves.changed == [edit, move]
    assert [item.text for item in with_moves.added] == ["New"]
    assert diff.changed == [edit]


def test_diff_settings_from_config_reads_the_configuration_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the diff settings are read from the app's configuration at the time they are asked for."""
    monkeypatch.setattr(config, "diff_checker_text_similarity_threshold", 0.75)
    monkeypatch.setattr(config, "diff_checker_max_comparisons_per_block", 5)
    monkeypatch.setattr(config, "diff_checker_min_content_ratio", 0.5)
    monkeypatch.setattr(config, "diff_checker_min_content_chars", 100)

    assert DiffSettings.from_config() == DiffSettings(
        similarity_threshold=0.75, max_comparisons_per_block=5, min_content_ratio=0.5, min_content_chars=100
    )
