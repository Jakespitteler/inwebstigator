from app.backend.page_inspector.compare_content import compare_content
from app.backend.utils.models import ContentBlock, PageContent


def create_block(text: str, heading: str = "H1", block_type: str = "paragraph") -> ContentBlock:
    """Helper to quickly create ContentBlock instances for testing."""
    return ContentBlock(parent_heading=heading, block_type=block_type, text=text)


def test_compare_identical_content():
    """Identical content should return an empty ContentDiff."""
    blocks = [
        create_block("Welcome to the homepage."),
        create_block("Here is a list of features.", block_type="list_item"),
    ]
    old = PageContent(blocks=blocks)
    new = PageContent(blocks=blocks)

    diff = compare_content(old, new)

    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.changed) == 0


def test_compare_pure_insertion():
    """New blocks appended to the content should be marked as added."""
    old = PageContent(blocks=[create_block("First paragraph.")])
    new = PageContent(blocks=[create_block("First paragraph."), create_block("Second paragraph.")])

    diff = compare_content(old, new)

    assert len(diff.added) == 1
    assert diff.added[0].text == "Second paragraph."
    assert len(diff.removed) == 0
    assert len(diff.changed) == 0


def test_compare_pure_deletion():
    """Blocks removed from the content should be marked as removed."""
    old = PageContent(blocks=[create_block("Keep this paragraph."), create_block("Remove this paragraph.")])
    new = PageContent(blocks=[create_block("Keep this paragraph.")])

    diff = compare_content(old, new)

    assert len(diff.added) == 0
    assert len(diff.removed) == 1
    assert diff.removed[0].text == "Remove this paragraph."
    assert len(diff.changed) == 0


def test_compare_edited_high_similarity():
    """Blocks that are slightly modified should be marked as changed."""
    # SequenceMatcher will evaluate these strings closely
    old_text = "The quick brown fox jumps over the lazy dog."
    new_text = "The quick brown fox leaps over the lazy dog."

    old = PageContent(blocks=[create_block(old_text)])
    new = PageContent(blocks=[create_block(new_text)])

    diff = compare_content(old, new)

    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.changed) == 1
    assert diff.changed[0].old_block.text == old_text
    assert diff.changed[0].new_block.text == new_text
    assert diff.changed[0].similarity > 0.60


def test_compare_replaced_low_similarity():
    """Blocks that change completely (below threshold) should be removed and added."""
    old = PageContent(blocks=[create_block("Completely unrelated text about apples.")])
    new = PageContent(blocks=[create_block("Totally different text about oranges.")])

    diff = compare_content(old, new)

    assert len(diff.changed) == 0
    assert len(diff.removed) == 1
    assert len(diff.added) == 1
    assert diff.removed[0].text == "Completely unrelated text about apples."
    assert diff.added[0].text == "Totally different text about oranges."


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

    diff = compare_content(old, new)

    assert len(diff.changed) == 1
    assert len(diff.removed) == 1
    assert len(diff.added) == 0
    assert diff.removed[0].text == "This line will be completely removed."


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

    diff = compare_content(old, new)

    assert len(diff.changed) == 1
    assert len(diff.removed) == 0
    assert len(diff.added) == 1
    assert diff.added[0].text == "This is a brand new line appended after."


def test_compare_metadata_change():
    """
    If the text is identical but the parent heading changes, it triggers a SequenceMatcher 'replace'.
    Because text similarity is 1.0, it should be categorised as changed rather than added/removed.
    """
    old = PageContent(blocks=[create_block("Exact same text.", heading="Old Heading")])
    new = PageContent(blocks=[create_block("Exact same text.", heading="New Heading")])

    diff = compare_content(old, new)

    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.changed) == 1

    changed_block = diff.changed[0]
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
    diff_default = compare_content(old, new)
    assert len(diff_default.changed) == 1

    # Using strict threshold (0.95), should classify as removed/added
    diff_strict = compare_content(old, new, similarity_threshold=0.95)
    assert len(diff_strict.changed) == 0
    assert len(diff_strict.removed) == 1
    assert len(diff_strict.added) == 1
