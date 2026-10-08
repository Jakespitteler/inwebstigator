import random
from collections import Counter
from collections.abc import Iterable

from app.backend.diff_checker.content_diff import compare_page_content, find_link_difference
from app.backend.diff_checker.models import DiffSettings, PageContent
from app.backend.diff_checker.page_parser import parse_html
from app.models.content_block_models import ChangedBlock, ContentBlock, HTMLBlockType


def create_block(text: str, heading: str = "H1", block_type: HTMLBlockType = HTMLBlockType.PARAGRAPH) -> ContentBlock:
    """Helper to quickly create ContentBlock instances for testing."""
    return ContentBlock(parent_heading=heading, block_type=block_type, text=text)


def compare_html(old_html: str, new_html: str) -> tuple[list[ContentBlock], list[ContentBlock], list[ChangedBlock]]:
    """Helper to parse two versions of a page and compare them, as a real scan does."""
    return compare_page_content(parse_html(old_html), parse_html(new_html))


def texts(blocks: Iterable[ContentBlock]) -> list[str]:
    """Helper to list just the text of each block."""
    return [block.text for block in blocks]


def text_pairs(changed: Iterable[ChangedBlock]) -> list[tuple[str, str]]:
    """Helper to list the (before, after) text of each changed block."""
    return [(change.old_block.text, change.new_block.text) for change in changed]


def test_compare_identical_content():
    """Identical content should return an empty Content"""
    blocks = [
        create_block("Welcome to the homepage."),
        create_block("Here is a list of features.", block_type=HTMLBlockType.LIST_ITEM),
    ]
    old = PageContent(blocks=blocks)
    new = PageContent(blocks=blocks)

    added, removed, changed = compare_page_content(old, new)

    assert len(added) == 0
    assert len(removed) == 0
    assert len(changed) == 0


def test_compare_pure_insertion():
    """New blocks appended to the content should be marked as added."""
    old = PageContent(blocks=[create_block("First paragraph.")])
    new = PageContent(blocks=[create_block("First paragraph."), create_block("Second paragraph.")])

    added, removed, changed = compare_page_content(old, new)

    assert len(added) == 1
    assert added[0].text == "Second paragraph."
    assert len(removed) == 0
    assert len(changed) == 0


def test_compare_pure_deletion():
    """Blocks removed from the content should be marked as removed."""
    old = PageContent(blocks=[create_block("Keep this paragraph."), create_block("Remove this paragraph.")])
    new = PageContent(blocks=[create_block("Keep this paragraph.")])

    added, removed, changed = compare_page_content(old, new)

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

    added, removed, changed = compare_page_content(old, new)

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

    added, removed, changed = compare_page_content(old, new)

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

    added, removed, changed = compare_page_content(old, new)

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

    added, removed, changed = compare_page_content(old, new)

    assert len(changed) == 1
    assert len(removed) == 0
    assert len(added) == 1
    assert added[0].text == "This is a brand new line appended after."


def test_compare_parent_heading_change_alone_is_not_a_change() -> None:
    """
    Text that only has a different parent heading is the same content, so is not reported.
    A renamed heading is reported through the heading's own block instead.
    """
    old = PageContent(blocks=[create_block("Exact same text.", heading="Old Heading")])
    new = PageContent(blocks=[create_block("Exact same text.", heading="New Heading")])

    assert compare_page_content(old, new) == ([], [], [])


def test_compare_renamed_heading_is_reported_once() -> None:
    """A renamed heading is one change, not a change for every block beneath it."""
    added, removed, changed = compare_html(
        "<main><h2>Fees</h2><p>Application fee is $50.</p><p>Renewal fee is $30.</p></main>",
        "<main><h2>Fees 2026</h2><p>Application fee is $50.</p><p>Renewal fee is $30.</p></main>",
    )

    assert (added, removed) == ([], [])
    assert text_pairs(changed) == [("Fees", "Fees 2026")]


def test_compare_renamed_heading_and_new_paragraph_do_not_repeat_unchanged_text() -> None:
    """
    Regression: a new paragraph at the top of a section whose heading was renamed shifted every later
    block by one, so unchanged paragraphs were reported as both removed and added.
    """
    added, removed, changed = compare_html(
        "<main><h2>Fees</h2><p>Application fee is $50.</p><p>Renewal fee is $30.</p><p>Late fee is $10.</p></main>",
        "<main><h2>Fees 2026</h2><p>Note: fees are reviewed yearly.</p>"
        "<p>Application fee is $50.</p><p>Renewal fee is $30.</p><p>Late fee is $10.</p></main>",
    )

    assert texts(added) == ["Note: fees are reviewed yearly."]
    assert removed == []
    assert text_pairs(changed) == [("Fees", "Fees 2026")]


def test_compare_new_sub_heading_mid_section_only_reports_new_content() -> None:
    """Regression: blocks moved under a new sub-heading were wrongly paired with each other as changes."""
    added, removed, changed = compare_html(
        "<main><h2>Eligibility</h2><p>You must be 18 or over.</p><p>You must live in WA.</p>"
        "<p>You must hold a licence.</p></main>",
        "<main><h2>Eligibility</h2><p>You must be 18 or over.</p><h3>Residency</h3>"
        "<p>This section covers where you live.</p><p>You must live in WA.</p><p>You must hold a licence.</p></main>",
    )

    assert texts(added) == ["Residency", "This section covers where you live."]
    assert (removed, changed) == ([], [])


def test_compare_tag_change_alone_is_not_a_change() -> None:
    """Regression: paragraphs turned into list items were reported as both removed and added."""
    added, removed, changed = compare_html(
        "<main><h2>Info</h2><p>Bring photo ID.</p><p>Bring proof of address.</p></main>",
        "<main><h2>Info</h2><p>What to bring:</p>"
        "<ul><li>Bring photo ID.</li><li>Bring proof of address.</li></ul></main>",
    )

    assert texts(added) == ["What to bring:"]
    assert (removed, changed) == ([], [])


def test_compare_moved_paragraph_is_not_a_change() -> None:
    """Regression: a paragraph moved elsewhere on the page was reported as both removed and added."""
    added, removed, changed = compare_html(
        "<main><h2>Info</h2><p>Alpha paragraph text.</p><p>Bravo paragraph text.</p>"
        "<p>Charlie paragraph text.</p></main>",
        "<main><h2>Info</h2><p>Bravo paragraph text.</p><p>Charlie paragraph text.</p>"
        "<p>Alpha paragraph text.</p></main>",
    )

    assert (added, removed, changed) == ([], [], [])


def test_compare_edit_is_paired_with_its_own_block_not_its_position() -> None:
    """Regression: a new paragraph before an edited one meant the edit was compared with the new paragraph."""
    added, removed, changed = compare_html(
        "<main><h2>Info</h2><p>Opening hours are 9am to 5pm weekdays.</p><p>Closed on public holidays.</p></main>",
        "<main><h2>Info</h2><p>New: online bookings now available.</p>"
        "<p>Opening hours are 9am to 4pm weekdays.</p><p>Closed on public holidays.</p></main>",
    )

    assert texts(added) == ["New: online bookings now available."]
    assert removed == []
    assert text_pairs(changed) == [("Opening hours are 9am to 5pm weekdays.", "Opening hours are 9am to 4pm weekdays.")]


def test_compare_reordered_edits_are_paired_by_similarity() -> None:
    """Two blocks that were both edited and swapped round are each paired with their own new version."""
    old = PageContent(
        blocks=[
            create_block("The office opens at 9am on weekdays."),
            create_block("Parking is free for all visitors."),
        ]
    )
    new = PageContent(
        blocks=[
            create_block("Parking is free for registered visitors."),
            create_block("The office opens at 8am on weekdays."),
        ]
    )

    added, removed, changed = compare_page_content(old, new)

    assert (added, removed) == ([], [])
    assert text_pairs(changed) == [
        ("Parking is free for all visitors.", "Parking is free for registered visitors."),
        ("The office opens at 9am on weekdays.", "The office opens at 8am on weekdays."),
    ]


def test_compare_repeated_text_only_reports_the_removed_copy() -> None:
    """Text that appears more than once (e.g. "Read more") is only reported for the copy that was removed."""
    added, removed, changed = compare_html(
        "<main><h2>Alpha</h2><p>Read more</p><h2>Bravo</h2><p>Read more</p><h2>Charlie</h2><p>Read more</p></main>",
        "<main><h2>Alpha</h2><p>Read more</p><h2>Charlie</h2><p>Read more</p></main>",
    )

    assert [(block.parent_heading, block.text) for block in removed] == [("Alpha", "Bravo"), ("Bravo", "Read more")]
    assert (added, changed) == ([], [])


def test_compare_large_rewrite_still_pairs_each_edit_with_its_own_block() -> None:
    """
    A changed region far bigger than MAX_COMPARISONS_PER_BLOCK only compares nearby blocks,
    which must still find each block's own edit.
    """
    old = PageContent(
        blocks=[create_block(f"Rule {i} says the fee for item {i} is due in 30 days.") for i in range(120)]
    )
    new = PageContent(
        blocks=[create_block(f"Rule {i} says the fee for item {i} is due in 14 days.") for i in range(120)]
    )

    added, removed, changed = compare_page_content(old, new)

    assert (added, removed) == ([], [])
    assert text_pairs(changed) == list(zip(texts(old.blocks), texts(new.blocks), strict=True))


def _random_sentence(rng: random.Random) -> str:
    """Builds a short sentence from a small vocabulary, so repeated and similar text is common."""
    vocabulary = ["fee", "form", "apply", "online", "renew", "licence", "office", "hours", "free", "late"]
    return " ".join(rng.choices(vocabulary, k=rng.randint(1, 6)))


def _randomly_edit(rng: random.Random, blocks: list[ContentBlock]) -> list[ContentBlock]:
    """Applies a few random inserts, deletes, moves, text edits, heading changes and tag changes."""
    edited = list(blocks)
    for _ in range(rng.randint(1, 5)):
        action = rng.choice(["insert", "delete", "move", "edit", "reheading", "retag"])
        if action == "insert" or not edited:
            edited.insert(rng.randint(0, len(edited)), create_block(_random_sentence(rng)))
            continue

        index = rng.randrange(len(edited))
        block = edited[index]
        match action:
            case "delete":
                edited.pop(index)
            case "move":
                edited.insert(rng.randint(0, len(edited) - 1), edited.pop(index))
            case "edit":
                edited[index] = block.model_copy(update={"text": f"{block.text} {_random_sentence(rng)}"})
            case "reheading":
                edited[index] = block.model_copy(update={"parent_heading": "Renamed heading"})
            case _:
                edited[index] = block.model_copy(update={"block_type": HTMLBlockType.LIST_ITEM})
    return edited


def test_compare_never_reports_unchanged_text_after_random_edits() -> None:
    """
    Whatever edits are made, once the reported changes are taken away, what is left of the old page and the
    new page must be exactly the same text. So the same text is never both added and removed, and a change
    always changes the text or the section it is in.
    """
    rng = random.Random(2026)

    for _ in range(500):
        old_blocks = [create_block(_random_sentence(rng)) for _ in range(rng.randint(0, 12))]
        new_blocks = _randomly_edit(rng, old_blocks)

        added, removed, changed = compare_page_content(PageContent(blocks=old_blocks), PageContent(blocks=new_blocks))

        old_counts, new_counts = Counter(texts(old_blocks)), Counter(texts(new_blocks))
        reported_old_counts = Counter(texts(removed)) + Counter(change.old_block.text for change in changed)
        reported_new_counts = Counter(texts(added)) + Counter(change.new_block.text for change in changed)

        assert not set(texts(added)) & set(texts(removed))
        assert all(
            change.old_block.text != change.new_block.text
            or change.old_block.parent_heading != change.new_block.parent_heading
            for change in changed
        )
        assert reported_old_counts <= old_counts
        assert reported_new_counts <= new_counts
        assert old_counts - reported_old_counts == new_counts - reported_new_counts


def test_compare_reports_text_that_moved_to_another_section() -> None:
    """Regression: "Closed" moving from Saturday to Sunday was reported as no change at all."""
    added, removed, changed = compare_html(
        "<main><h2>Saturday</h2><p>Closed</p><h2>Sunday</h2><p>Open 10am to 2pm</p></main>",
        "<main><h2>Saturday</h2><p>Open 10am to 2pm</p><h2>Sunday</h2><p>Closed</p></main>",
    )

    assert (added, removed) == ([], [])
    assert [
        (change.new_block.text, change.old_block.parent_heading, change.new_block.parent_heading) for change in changed
    ] == [
        ("Open 10am to 2pm", "Sunday", "Saturday"),
        ("Closed", "Saturday", "Sunday"),
    ]
    assert all(change.is_move for change in changed)


def test_compare_reports_prices_that_swapped_sections() -> None:
    """Regression: two prices swapping between sections was reported as no change at all."""
    _, _, changed = compare_html(
        "<main><h2>Application</h2><p>$30</p><h2>Renewal</h2><p>$50</p></main>",
        "<main><h2>Application</h2><p>$50</p><h2>Renewal</h2><p>$30</p></main>",
    )

    assert sorted((change.new_block.text, change.new_block.parent_heading) for change in changed) == [
        ("$30", "Renewal"),
        ("$50", "Application"),
    ]


def test_compare_text_in_divs_and_definition_lists_is_monitored() -> None:
    """Regression: text straight inside a <div> or <dd> was never read, so its edits were not reported."""
    _, _, changed = compare_html(
        "<main><h2>Fees</h2><div>Application fee is $50.</div><dl><dt>Renewal</dt><dd>$30</dd></dl></main>",
        "<main><h2>Fees</h2><div>Application fee is $500.</div><dl><dt>Renewal</dt><dd>$35</dd></dl></main>",
    )

    assert text_pairs(changed) == [("Application fee is $50.", "Application fee is $500."), ("$30", "$35")]


def test_compare_never_reports_unchanged_text_across_sections_after_random_edits() -> None:
    """The same checks as above, with blocks spread over several sections, so text can move between them."""
    rng = random.Random(7)
    headings = ["Fees", "Hours", "Contact"]

    for _ in range(500):
        old_blocks = [
            create_block(_random_sentence(rng), heading=rng.choice(headings)) for _ in range(rng.randint(0, 12))
        ]
        new_blocks = [
            block.model_copy(update={"parent_heading": rng.choice(headings)}) if rng.random() < 0.2 else block
            for block in _randomly_edit(rng, old_blocks)
        ]

        added, removed, changed = compare_page_content(PageContent(blocks=old_blocks), PageContent(blocks=new_blocks))

        old_counts, new_counts = Counter(texts(old_blocks)), Counter(texts(new_blocks))
        reported_old_counts = Counter(texts(removed)) + Counter(change.old_block.text for change in changed)
        reported_new_counts = Counter(texts(added)) + Counter(change.new_block.text for change in changed)

        assert not set(texts(added)) & set(texts(removed))
        assert reported_old_counts <= old_counts
        assert reported_new_counts <= new_counts
        assert old_counts - reported_old_counts == new_counts - reported_new_counts


def test_compare_custom_similarity_threshold():
    """Tests that overriding the default threshold correctly categories blocks."""
    str1 = "apple pie recipe"
    str2 = "apple tart recipe"
    # Similarity ratio is about 0.79

    old = PageContent(blocks=[create_block(str1)])
    new = PageContent(blocks=[create_block(str2)])

    # Using default threshold (0.60), should classify as changed
    _, _, default_changed = compare_page_content(old, new)
    assert len(default_changed) == 1

    # Using strict threshold (0.95), should classify as removed/added
    strict_added, strict_removed, strict_changed = compare_page_content(
        old, new, DiffSettings(similarity_threshold=0.95)
    )
    assert len(strict_changed) == 0
    assert len(strict_removed) == 1
    assert len(strict_added) == 1


def test_find_link_difference() -> None:
    previous: list[str] = ["/page1", "/page2"]
    current: list[str] = ["/page2", "/page3"]
    added, removed = find_link_difference(previous, current)
    assert added == ["/page3"]
    assert removed == ["/page1"]


def test_compare_similarity_exactly_at_the_threshold_is_an_edit() -> None:
    """Tests two blocks exactly as similar as the threshold count as an edit, as the threshold is the minimum."""
    old = PageContent(blocks=[create_block("Fee is $50")])
    new = PageContent(blocks=[create_block("Fee is $60")])  # Similarity ratio is exactly 0.9

    added, removed, changed = compare_page_content(old, new, DiffSettings(similarity_threshold=0.9))

    assert (added, removed) == ([], [])
    assert [(change.similarity, change.is_move) for change in changed] == [(0.9, False)]


def test_compare_only_looks_for_edits_among_nearby_blocks_past_the_comparison_cap() -> None:
    """
    Tests a new block in a changed region with more old blocks than the cap is only compared with the old blocks
    nearest its own position, so two edited blocks that also swapped places are reported as removed and added.
    """
    old = PageContent(
        blocks=[create_block("The office opens at 9am on weekdays."), create_block("Parking is free for all visitors.")]
    )
    new = PageContent(
        blocks=[
            create_block("Parking is free for registered visitors."),
            create_block("The office opens at 8am on weekdays."),
        ]
    )

    capped_added, capped_removed, capped_changed = compare_page_content(
        old, new, DiffSettings(max_comparisons_per_block=1)
    )
    _, _, uncapped_changed = compare_page_content(old, new, DiffSettings(max_comparisons_per_block=2))

    assert capped_changed == []
    assert texts(capped_removed) == texts(old.blocks)
    assert texts(capped_added) == texts(new.blocks)
    assert len(uncapped_changed) == 2


def test_compare_lists_moves_after_edits() -> None:
    """Tests moved text is listed after the edited text, even when the move is higher up the page."""
    added, removed, changed = compare_html(
        "<main><h2>Saturday</h2><p>Closed</p><h2>Sunday</h2><p>Open 10am to 2pm</p>"
        "<h2>Fees</h2><p>Entry is $5 per person.</p></main>",
        "<main><h2>Saturday</h2><p>Open 10am to 2pm</p><h2>Sunday</h2><p>Closed</p>"
        "<h2>Fees</h2><p>Entry is $6 per person.</p></main>",
    )

    assert (added, removed) == ([], [])
    assert [(change.new_block.text, change.is_move) for change in changed] == [
        ("Entry is $6 per person.", False),
        ("Open 10am to 2pm", True),
        ("Closed", True),
    ]


def test_compare_text_from_a_removed_section_is_not_a_move() -> None:
    """
    Tests text whose section heading was removed, now sitting in another section, is not reported as moved,
    as the removed heading's own block reports the change.
    """
    added, removed, changed = compare_html(
        "<main><h2>Saturday</h2><p>Closed</p><h2>Sunday</h2><p>Open 10am to 2pm</p></main>",
        "<main><h2>Sunday</h2><p>Closed</p><p>Open 10am to 2pm</p></main>",
    )

    assert [(block.block_type, block.text) for block in removed] == [(HTMLBlockType.HEADING_2, "Saturday")]
    assert (added, changed) == ([], [])


def test_compare_edited_table_cell_is_reported_as_a_changed_row() -> None:
    """Tests an edit to one cell of a table is reported as an edit of that row, with the other rows unchanged."""
    added, removed, changed = compare_html(
        "<main><table><tr><th>Licence</th><th>Fee</th></tr><tr><td>Car</td><td>$50</td></tr></table></main>",
        "<main><table><tr><th>Licence</th><th>Fee</th></tr><tr><td>Car</td><td>$55</td></tr></table></main>",
    )

    assert (added, removed) == ([], [])
    assert text_pairs(changed) == [("Car | $50", "Car | $55")]
