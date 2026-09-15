from difflib import SequenceMatcher

from app.backend.utils.html_parser import ChangedBlock, ContentBlock, HTMLBlockType, PageContent
from app.backend.utils.links import find_added_links, find_removed_links


def _evaluate_replacements(
    old_chunk: list[ContentBlock],
    new_chunk: list[ContentBlock],
    similarity_threshold: float,
) -> tuple[list[ContentBlock], list[ContentBlock], list[ChangedBlock]]:
    """Evaluates mismatched content block chunks to distinguish between edits and full replacements.

    Pairs overlapping blocks sequentially and calculates text similarity ratios.
    Blocks meeting or exceeding the threshold are categorised as changed, while
    the remainder are classified as added or removed.

    Args:
        old_chunk: A list of original ContentBlock objects within the replaced slice.
        new_chunk: A list of updated ContentBlock objects within the replaced slice.
        similarity_threshold: The minimum SequenceMatcher ratio (0.0 to 1.0)
            required to classify two blocks as modified rather than replaced.

    Returns:
        A tuple containing three elements:
            - A list of added ContentBlock objects.
            - A list of removed ContentBlock objects.
            - A list of ChangedBlock objects representing modified content.
    """
    added: list[ContentBlock] = []
    removed: list[ContentBlock] = []
    changed: list[ChangedBlock] = []

    pair_count = min(len(old_chunk), len(new_chunk))

    # Evaluate aligned pairs
    for i in range(pair_count):
        old_block = old_chunk[i]
        new_block = new_chunk[i]

        similarity = SequenceMatcher(None, old_block.text, new_block.text, autojunk=False).ratio()

        if similarity >= similarity_threshold:
            changed.append(ChangedBlock(old_block=old_block, new_block=new_block, similarity=similarity))
        else:
            removed.append(old_block)
            added.append(new_block)

    # Handle unaligned leftovers cleanly using Python slices
    removed.extend(old_chunk[pair_count:])
    added.extend(new_chunk[pair_count:])

    return added, removed, changed


def compare_page_content(
    old_content: PageContent,
    new_content: PageContent,
    similarity_threshold: float = 0.60,  # TODO may have to drop to 0
) -> tuple[list[ContentBlock], list[ContentBlock], list[ChangedBlock]]:
    """Compares two PageContent objects to identify added, removed, and modified content blocks.

    Uses sequence matching on structured block signatures (parent heading, block type, and text)
    to compute opcodes and evaluate differences between page revisions.

    Args:
        old_content: The PageContent object representing the previous state.
        new_content: The PageContent object representing the current state.
        similarity_threshold: The text similarity ratio cutoff (0.0 to 1.0) passed to
            replacement evaluation for identifying block edits. Defaults to 0.60.

    Returns:
        A tuple containing three elements:
            - A list of newly added ContentBlock objects.
            - A list of removed ContentBlock objects.
            - A list of ChangedBlock objects representing modified content.
    """

    old_sequence: list[tuple[str | None, HTMLBlockType, str]] = [
        (b.parent_heading, b.block_type, b.text) for b in old_content.blocks
    ]
    new_sequence: list[tuple[str | None, HTMLBlockType, str]] = [
        (b.parent_heading, b.block_type, b.text) for b in new_content.blocks
    ]

    matcher = SequenceMatcher(None, old_sequence, new_sequence, autojunk=False)

    added: list[ContentBlock] = []
    removed: list[ContentBlock] = []
    changed: list[ChangedBlock] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        elif tag == "delete":
            removed.extend(old_content.blocks[i1:i2])

        elif tag == "insert":
            added.extend(new_content.blocks[j1:j2])

        elif tag == "replace":
            rep_added, rep_removed, rep_changed = _evaluate_replacements(
                old_chunk=old_content.blocks[i1:i2],
                new_chunk=new_content.blocks[j1:j2],
                similarity_threshold=similarity_threshold,
            )
            added.extend(rep_added)
            removed.extend(rep_removed)
            changed.extend(rep_changed)

    return added, removed, changed


def find_link_difference(previous_state: list[str], current_state: list[str]) -> tuple[list[str], list[str]]:
    """Compares stored links against newly extracted links to determine additions and removals.

    Args:
        previous_state: A list of URL strings representing the stored links.
        current_state: A list of URL strings representing the newly found links.

    Returns:
        A tuple containing two lists:
            - The first list contains added URL strings.
            - The second list contains removed URL strings.
    """
    return find_added_links(previous_state, current_state), find_removed_links(previous_state, current_state)
