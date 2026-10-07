from app.backend.diff_checker.models import PageContent
from app.backend.diff_checker.settings import DiffSettings


def has_lost_most_content(
    old_content: PageContent,
    new_content: PageContent,
    settings: DiffSettings | None = None,
) -> bool:
    """Checks whether a new version of a page has lost most of its text compared with the old version.

    A page that suddenly has only a fraction of its text is almost always a stand-in served in its place, such
    as a "Just a moment..." browser check, a maintenance page or a login wall, rather than the page's real new
    content. Treating it as the new content would report everything as removed, then everything as added again
    once the real page is back.

    Args:
        old_content: The page's saved content.
        new_content: The content just fetched.
        settings: The share of text the new page must keep, and how much text the old page needs before the
            check applies. Defaults to the app's configured settings.

    Returns:
        True if the old page had enough text to judge and the new page has less than the minimum share of it.
    """
    diff_settings: DiffSettings = settings or DiffSettings.from_config()
    if old_content.text_length < diff_settings.min_content_chars:
        return False
    return new_content.text_length < old_content.text_length * diff_settings.min_content_ratio
