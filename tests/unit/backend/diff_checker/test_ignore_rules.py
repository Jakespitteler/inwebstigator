import pytest
from pydantic import ValidationError

from app.backend.diff_checker.content_diff import compare_page_content, without_ignored_text
from app.backend.diff_checker.models import PageContent
from app.models.content_block_models import ContentBlock, HTMLBlockType
from app.models.critical_page_models import CriticalPageUpdate


def page(*texts: str) -> PageContent:
    """Builds a page with a paragraph for each text."""
    return PageContent(blocks=[ContentBlock(block_type=HTMLBlockType.PARAGRAPH, text=text) for text in texts])


def texts(content: PageContent) -> list[str]:
    return [block.text for block in content.blocks]


def test_only_the_matching_part_of_a_block_is_removed() -> None:
    content = without_ignored_text(
        page("Fee is $50. 1,234 people found this useful"), [r"[\d,]+ people found this useful"]
    )

    assert texts(content) == ["Fee is $50."]


def test_a_block_with_no_text_left_is_dropped() -> None:
    content = without_ignored_text(page("Page last updated: 7 Oct 2026", "Fee is $50."), [r"Page last updated: .*"])

    assert texts(content) == ["Fee is $50."]


def test_a_page_without_rules_is_returned_unchanged() -> None:
    content = page("Fee is $50.")

    assert without_ignored_text(content, []) is content


def test_every_rule_is_applied_and_the_rest_of_the_page_is_kept() -> None:
    """Tests each rule removes its own text, while each block keeps its section and type and the page its headings."""
    content = PageContent(
        headings=["Fees"],
        blocks=[
            ContentBlock(parent_heading="Fees", block_type=HTMLBlockType.LIST_ITEM, text="Fee is $50. Visitors: 812")
        ],
        last_updated="7 October 2026",
    )

    cleaned = without_ignored_text(content, [r"Visitors: \d+", r"\$50"])

    assert [(block.parent_heading, block.block_type, block.text) for block in cleaned.blocks] == [
        ("Fees", HTMLBlockType.LIST_ITEM, "Fee is ."),
    ]
    assert (cleaned.headings, cleaned.last_updated) == (["Fees"], "7 October 2026")
    assert texts(content) == ["Fee is $50. Visitors: 812"]


def test_a_change_only_in_ignored_text_is_not_reported() -> None:
    """Tests two versions of a page that only differ in ignored text compare as unchanged, while real edits show."""
    rules = [r"[\d,]+ people found this useful"]
    old = without_ignored_text(page("Fee is $50. 1,234 people found this useful", "Open 9 to 5"), rules)
    unchanged = without_ignored_text(page("Fee is $50. 1,240 people found this useful", "Open 9 to 5"), rules)
    edited = without_ignored_text(page("Fee is $60. 1,240 people found this useful", "Open 9 to 5"), rules)

    assert compare_page_content(old, unchanged) == ([], [], [])
    assert [(change.old_block.text, change.new_block.text) for change in compare_page_content(old, edited).changed] == [
        ("Fee is $50.", "Fee is $60.")
    ]


def test_an_ignore_rule_must_be_a_valid_regular_expression() -> None:
    with pytest.raises(ValidationError, match="valid regular expression"):
        CriticalPageUpdate.model_validate({"ignore_rules": ["Fee is ($50"]})
