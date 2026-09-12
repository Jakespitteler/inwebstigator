from difflib import SequenceMatcher

from app.backend.utils.models import ChangedBlock, ContentBlock, ContentDiff, PageContent


def _evaluate_replacements(
    old_chunk: list[ContentBlock],
    new_chunk: list[ContentBlock],
    similarity_threshold: float,
) -> tuple[list[ContentBlock], list[ContentBlock], list[ChangedBlock]]:
    """
    Takes two mismatched chunks of text and determines
    if they were edited or completely replaced.
    Returns: (added_blocks, removed_blocks, changed_blocks)
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


def compare_content(
    old_content: PageContent, new_content: PageContent, similarity_threshold: float = 0.60
) -> ContentDiff:
    """
    Orchestrates the comparison of two content snapshots.
    """

    old_sequence: list[tuple[str, str, str]] = [(b.parent_heading, b.block_type, b.text) for b in old_content.blocks]
    new_sequence: list[tuple[str, str, str]] = [(b.parent_heading, b.block_type, b.text) for b in new_content.blocks]

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

    return ContentDiff(added=added, removed=removed, changed=changed)
