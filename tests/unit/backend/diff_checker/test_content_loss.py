import pytest

from app.backend.diff_checker.content_diff import has_lost_most_content
from app.backend.diff_checker.models import DiffSettings, PageContent
from app.core.config import config
from app.models.content_block_models import ContentBlock, HTMLBlockType

SETTINGS = DiffSettings(min_content_ratio=0.3, min_content_chars=200)


def page_with_text(length: int) -> PageContent:
    """Builds a page holding the given number of characters of text."""
    return PageContent(blocks=[ContentBlock(block_type=HTMLBlockType.PARAGRAPH, text="x" * length)])


def test_a_page_that_lost_most_of_its_text_is_a_stand_in() -> None:
    assert has_lost_most_content(page_with_text(1000), page_with_text(100), SETTINGS) is True


def test_a_page_that_kept_enough_of_its_text_is_real() -> None:
    assert has_lost_most_content(page_with_text(1000), page_with_text(300), SETTINGS) is False


def test_a_page_with_little_text_can_shrink() -> None:
    """Tests a short page can lose most of its few words without being mistaken for a stand-in."""
    assert has_lost_most_content(page_with_text(150), page_with_text(10), SETTINGS) is False


def test_a_page_with_exactly_the_minimum_text_is_checked() -> None:
    """Tests the stand-in check applies once the old page has exactly the minimum amount of text."""
    assert has_lost_most_content(page_with_text(200), page_with_text(10), SETTINGS) is True


def test_a_page_that_is_now_empty_is_a_stand_in() -> None:
    """Tests a page that had plenty of text and now has none is treated as a stand-in."""
    assert has_lost_most_content(page_with_text(1000), PageContent(), SETTINGS) is True


def test_text_is_counted_across_every_block() -> None:
    """Tests the text of all the new page's blocks counts towards what it kept, not just one block."""
    new_page = PageContent(
        blocks=[ContentBlock(block_type=HTMLBlockType.PARAGRAPH, text="x" * 200) for _ in range(2)],
    )

    assert has_lost_most_content(page_with_text(1000), new_page, SETTINGS) is False


def test_the_configured_settings_are_used_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests the app's configured share and minimum amount of text are used when no settings are given."""
    monkeypatch.setattr(config, "diff_checker_min_content_ratio", 0.5)
    monkeypatch.setattr(config, "diff_checker_min_content_chars", 100)

    assert has_lost_most_content(page_with_text(1000), page_with_text(499)) is True
    assert has_lost_most_content(page_with_text(1000), page_with_text(500)) is False
    assert has_lost_most_content(page_with_text(99), page_with_text(0)) is False
