from app.backend.diff_checker.compare_content import compare_content
from app.backend.utils.html_parser import ContentBlock, HTMLBlockType, PageContent


def create_block(text: str, heading: str = "H1", block_type: HTMLBlockType = HTMLBlockType.PARAGRAPH) -> ContentBlock:
    """Helper to quickly create ContentBlock instances for testing."""
    return ContentBlock(parent_heading=heading, block_type=block_type, text=text)


def test_compare_identical_content():
    """Identical content should return an empty Content"""
    blocks = [
        create_block("Welcome to the homepage."),
        create_block("Here is a list of features.", block_type=HTMLBlockType.UNORDERED_LIST),
    ]
    old = PageContent(blocks=blocks)
    new = PageContent(blocks=blocks)

    added, removed, changed = compare_content(old, new)

    assert len(added) == 0
    assert len(removed) == 0
    assert len(changed) == 0


def test_compare_pure_insertion():
    """New blocks appended to the content should be marked as added."""
    old = PageContent(blocks=[create_block("First paragraph.")])
    new = PageContent(blocks=[create_block("First paragraph."), create_block("Second paragraph.")])

    added, removed, changed = compare_content(old, new)

    assert len(added) == 1
    assert added[0].text == "Second paragraph."
    assert len(removed) == 0
    assert len(changed) == 0


def test_compare_pure_deletion():
    """Blocks removed from the content should be marked as removed."""
    old = PageContent(blocks=[create_block("Keep this paragraph."), create_block("Remove this paragraph.")])
    new = PageContent(blocks=[create_block("Keep this paragraph.")])

    added, removed, changed = compare_content(old, new)

    assert len(added) == 0
    assert len(removed) == 1
    assert removed[0].text == "Remove this paragraph."
    assert len(changed) == 0


def test_compare_edited_high_similarity():
    """Blocks that are slightly modified should be marked as changed."""
    # SequenceMatcher will evaluate these strings closely
    old_text = "The quick brown fox jumps over the lazy dog."
    new_text = "The quick brown fox leaps over the lazy dog."

    old = PageContent(blocks=[create_block(old_text)])
    new = PageContent(blocks=[create_block(new_text)])

    added, removed, changed = compare_content(old, new)

    assert len(added) == 0
    assert len(removed) == 0
    assert len(changed) == 1
    assert changed[0].old_block.text == old_text
    assert changed[0].new_block.text == new_text
    assert changed[0].similarity > 0.60


def test_compare_replaced_low_similarity():
    """Blocks that change completely (below threshold) should be removed and added."""
    old = PageContent(blocks=[create_block("Alpha zebra 12345")])
    new = PageContent(blocks=[create_block("Qwerty uiop 67890")])

    added, removed, changed = compare_content(old, new)

    assert len(changed) == 0
    assert len(removed) == 1
    assert len(added) == 1
    assert removed[0].text == "Alpha zebra 12345"
    assert added[0].text == "Qwerty uiop 67890"


def test_compare_uneven_replacements_more_old():
    """
    Tests when a larger chunk of old blocks is replaced by a smaller chunk of new blocks.
    The leftover old blocks should be classified as removed.
    """
    old = PageContent(
        blocks=[
            create_block("This line will be slightly edited."),
            create_block("This line will be completely removed."),
        ]
    )
    new = PageContent(blocks=[create_block("This line will be slightly tweaked.")])

    added, removed, changed = compare_content(old, new)

    assert len(changed) == 1
    assert len(removed) == 1
    assert len(added) == 0
    assert removed[0].text == "This line will be completely removed."


def test_compare_uneven_replacements_more_new():
    """
    Tests when a smaller chunk of old blocks is replaced by a larger chunk of new blocks.
    The leftover new blocks should be classified as added.
    """
    old = PageContent(blocks=[create_block("This line gets slightly edited.")])
    new = PageContent(
        blocks=[
            create_block("This line got slightly edited."),
            create_block("This is a brand new line appended after."),
        ]
    )

    added, removed, changed = compare_content(old, new)

    assert len(changed) == 1
    assert len(removed) == 0
    assert len(added) == 1
    assert added[0].text == "This is a brand new line appended after."


def test_compare_metadata_change():
    """
    If the text is identical but the parent heading changes, it triggers a SequenceMatcher 'replace'.
    Because text similarity is 1.0, it should be categorised as changed rather than added/removed.
    """
    old = PageContent(blocks=[create_block("Exact same text.", heading="Old Heading")])
    new = PageContent(blocks=[create_block("Exact same text.", heading="New Heading")])

    added, removed, changed = compare_content(old, new)

    assert len(added) == 0
    assert len(removed) == 0
    assert len(changed) == 1

    changed_block = changed[0]
    assert changed_block.similarity == 1.0
    assert changed_block.old_block.parent_heading == "Old Heading"
    assert changed_block.new_block.parent_heading == "New Heading"


def test_compare_custom_similarity_threshold():
    """Tests that overriding the default threshold correctly categories blocks."""
    str1 = "apple pie recipe"
    str2 = "apple tart recipe"
    # Similarity ratio is around ~0.87

    old = PageContent(blocks=[create_block(str1)])
    new = PageContent(blocks=[create_block(str2)])

    # Using default threshold (0.60), should classify as changed
    _, _, default_changed = compare_content(old, new)
    assert len(default_changed) == 1

    # Using strict threshold (0.95), should classify as removed/added
    strict_added, strict_removed, strict_changed = compare_content(old, new, similarity_threshold=0.95)
    assert len(strict_changed) == 0
    assert len(strict_removed) == 1
    assert len(strict_added) == 1
