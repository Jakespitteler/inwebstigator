from app.backend.diff_checker.models import LinkDiff
from app.backend.links import find_added_links, find_removed_links


def find_link_difference(previous_state: list[str], current_state: list[str]) -> LinkDiff:
    """Compares stored links against newly extracted links to determine additions and removals.

    Args:
        previous_state: A list of URL strings representing the stored links.
        current_state: A list of URL strings representing the newly found links.

    Returns:
        The added and removed URL strings, which unpack like a tuple of those two lists.
    """
    return LinkDiff(
        added=find_added_links(previous_state, current_state),
        removed=find_removed_links(previous_state, current_state),
    )
