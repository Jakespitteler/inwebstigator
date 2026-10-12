import pytest

from app.backend.diff_checker.content_diff import has_little_readable_text, has_lost_most_content
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


@pytest.mark.parametrize(
    ("text_length", "html_length", "little"),
    [
        (145, 943_000, True),  # youtube.com: a large page of scripts, with almost no text until they run
        (0, 8_000, True),  # A page that is only a script that loads the app
        (156, 577, False),  # example.com: a small plain page, whose little text is most of it
        (1_600, 235_000, False),  # A normal page with plenty of text, even among lots of scripts
        (499, 10_000, True),
        (500, 100_000, False),  # Enough text, however much HTML is around it
        (499, 9_980, False),  # Too much of the HTML is text for it to be a page of scripts
    ],
)
def test_a_page_with_little_text_in_a_lot_of_html_has_little_readable_text(
    text_length: int, html_length: int, little: bool
) -> None:
    """Tests a page is said to have little readable text (so it is probably built by JavaScript) only when it has
    under 500 characters of text, making up under 5% of its HTML."""
    assert has_little_readable_text(page_with_text(text_length), "x" * html_length, 500, 0.05) is little
