import pytest
from pydantic import ValidationError

from app.backend.diff_checker.ignore_rules import without_ignored_text
from app.backend.diff_checker.models import ContentBlock, HTMLBlockType, PageContent
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


def test_an_ignore_rule_must_be_a_valid_regular_expression() -> None:
    with pytest.raises(ValidationError, match="is not a valid regular expression"):
        CriticalPageUpdate(ignore_rules=["Fee is ($50"])
