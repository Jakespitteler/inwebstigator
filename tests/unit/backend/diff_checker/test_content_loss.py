from app.backend.diff_checker.content_diff import has_lost_most_content
from app.backend.diff_checker.models import ContentBlock, DiffSettings, HTMLBlockType, PageContent

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
