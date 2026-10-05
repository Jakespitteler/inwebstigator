from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from difflib import SequenceMatcher
from typing import NamedTuple

from app.backend.utils.html_parser import ChangedBlock, ContentBlock, PageContent
from app.backend.utils.links import find_added_links, find_removed_links
from app.core.config import config

# The most removed blocks each added block is compared with when looking for edits. Normal sized changes are
# well under this so every pair is compared, but it stops a full page rewrite taking minutes to compare.
MAX_COMPARISONS_PER_BLOCK: int = 20
TEXT_SIMILARITY_THRESHOLD: float = config.text_similarity_threshold


class _UnmatchedBlock(NamedTuple):
    """A block outside the unchanged runs of a page, tagged with the changed region of the page it is in.

    Attributes:
        region: Which changed region the block is in, counting from the top of the page. A region is a
            stretch of the page between two unchanged runs, so an edited block stays in the same region.
        block: The content block itself.
    """

    region: int
    block: ContentBlock


class _ScoredPair(NamedTuple):
    """A possible pairing of a removed block with an added block, scored by how similar their text is.

    Attributes:
        similarity: The SequenceMatcher ratio (0.0 to 1.0) between the two blocks' text.
        old_index: The position of the removed block in its list.
        new_index: The position of the added block in its list.
    """

    similarity: float
    old_index: int
    new_index: int


def _unmatched_blocks(
    old_blocks: Sequence[ContentBlock],
    new_blocks: Sequence[ContentBlock],
) -> tuple[list[_UnmatchedBlock], list[_UnmatchedBlock]]:
    """Finds the blocks that are not part of an unchanged run between two versions of a page.

    Blocks are lined up by their parent heading as well as their text, so text that repeats on a page
    (e.g. "Read more") is matched within its own section. A block left over only because its heading
    changed is still the same content, and is caught afterwards by `_without_shared_text`.

    Args:
        old_blocks: The content blocks from the previous version of the page.
        new_blocks: The content blocks from the current version of the page.

    Returns:
        A tuple containing two elements, each in page order and tagged with their changed region:
            - The old blocks with no match in the same place on the new page.
            - The new blocks with no match in the same place on the old page.
    """
    matcher = SequenceMatcher(
        None,
        [(block.parent_heading, block.text) for block in old_blocks],
        [(block.parent_heading, block.text) for block in new_blocks],
        autojunk=False,
    )
    differing_opcodes = [opcode for opcode in matcher.get_opcodes() if opcode[0] != "equal"]

    unmatched_old = [
        _UnmatchedBlock(region=region, block=block)
        for region, (_, i1, i2, _, _) in enumerate(differing_opcodes)
        for block in old_blocks[i1:i2]
    ]
    unmatched_new = [
        _UnmatchedBlock(region=region, block=block)
        for region, (_, _, _, j1, j2) in enumerate(differing_opcodes)
        for block in new_blocks[j1:j2]
    ]
    return unmatched_old, unmatched_new


def _without_shared_text(
    items: Sequence[_UnmatchedBlock],
    other_items: Sequence[_UnmatchedBlock],
) -> list[_UnmatchedBlock]:
    """Drops each block whose exact text also appears in another list of blocks, anywhere on the page.

    This catches text that only moved, or now sits under a different heading or HTML tag. Blocks are
    matched one for one, so text that repeats on a page (e.g. "Read more") is only dropped as many times
    as it appears in `other_items`, and a copy that really was removed is still kept.

    Args:
        items: The blocks to filter, in page order.
        other_items: The blocks whose text cancels out matching blocks in `items`.

    Returns:
        The blocks from `items` with no exact text match left in `other_items`, in page order.
    """
    unclaimed_text_counts = Counter(item.block.text for item in other_items)
    kept_items: list[_UnmatchedBlock] = []

    for item in items:
        if unclaimed_text_counts[item.block.text] > 0:
            unclaimed_text_counts[item.block.text] -= 1
        else:
            kept_items.append(item)

    return kept_items


def _blocks_by_region(items: Iterable[_UnmatchedBlock]) -> dict[int, list[ContentBlock]]:
    """Groups blocks by the changed region of the page they are in, keeping them in page order.

    Args:
        items: The blocks to group, in page order.

    Returns:
        The blocks in each region, by region number. Regions with no blocks are left out.
    """
    blocks_by_region: defaultdict[int, list[ContentBlock]] = defaultdict(list)
    for item in items:
        blocks_by_region[item.region].append(item.block)
    return blocks_by_region


def _comparison_window(old_count: int, new_position: int, new_count: int) -> range:
    """Picks which removed blocks in a region an added block is compared with when looking for edits.

    All of them in a normal sized region. In a very large region (e.g. a page rewrite), only the
    MAX_COMPARISONS_PER_BLOCK removed blocks nearest the added block's relative position in the region.

    Args:
        old_count: How many removed blocks are in the region.
        new_position: The added block's position among the region's added blocks.
        new_count: How many added blocks are in the region.

    Returns:
        The positions of the removed blocks to compare the added block with.
    """
    if old_count <= MAX_COMPARISONS_PER_BLOCK:
        return range(old_count)

    centre = new_position * old_count // new_count
    start = min(max(centre - MAX_COMPARISONS_PER_BLOCK // 2, 0), old_count - MAX_COMPARISONS_PER_BLOCK)
    return range(start, start + MAX_COMPARISONS_PER_BLOCK)


def _similarity_meeting_threshold(matcher: SequenceMatcher[str], similarity_threshold: float) -> float | None:
    """Scores how similar a matcher's two texts are, if they are similar enough to count as an edit.

    The two cheap upper-bound estimates are checked first, so most unrelated pairs skip the slow full comparison.

    Args:
        matcher: A SequenceMatcher already set up with the two texts to compare.
        similarity_threshold: The minimum ratio (0.0 to 1.0) for the texts to count as an edit of each other.

    Returns:
        The similarity ratio, or None if it is below the threshold.
    """
    if matcher.real_quick_ratio() < similarity_threshold or matcher.quick_ratio() < similarity_threshold:
        return None

    similarity = matcher.ratio()
    return similarity if similarity >= similarity_threshold else None


def _score_candidate_pairs(
    old_blocks: Sequence[ContentBlock],
    new_blocks: Sequence[ContentBlock],
    similarity_threshold: float,
) -> Iterator[_ScoredPair]:
    """Scores each pairing of a removed and an added block (from one region) that is similar enough to be an edit.

    Each new block's text is set as the matcher's second sequence, which difflib analyses once and caches,
    so it is reused while comparing against the removed blocks.

    Args:
        old_blocks: The removed blocks in the region that could have been edited, in page order.
        new_blocks: The added blocks in the region that could be edited versions of them, in page order.
        similarity_threshold: The minimum ratio (0.0 to 1.0) for two blocks to count as an edit.

    Yields:
        A _ScoredPair for each old/new pairing that meets the threshold.
    """
    for new_index, new_block in enumerate(new_blocks):
        matcher = SequenceMatcher(None, b=new_block.text, autojunk=False)

        for old_index in _comparison_window(len(old_blocks), new_index, len(new_blocks)):
            matcher.set_seq1(old_blocks[old_index].text)
            similarity = _similarity_meeting_threshold(matcher, similarity_threshold)
            if similarity is not None:
                yield _ScoredPair(similarity=similarity, old_index=old_index, new_index=new_index)


def _select_best_pairs(scored_pairs: Iterable[_ScoredPair]) -> list[_ScoredPair]:
    """Keeps the most similar pairs first, using each block in at most one pair.

    Taking the closest match first means an edited block is paired with its own new version, rather than
    whichever block happens to sit in the same position. Ties are broken by page position so the result
    is always the same for the same input.

    Args:
        scored_pairs: Every candidate pairing that met the similarity threshold.

    Returns:
        The chosen pairs, from most to least similar.
    """
    used_old_indexes: set[int] = set()
    used_new_indexes: set[int] = set()
    best_pairs: list[_ScoredPair] = []

    for pair in sorted(scored_pairs, key=lambda pair: (-pair.similarity, pair.old_index, pair.new_index)):
        if pair.old_index in used_old_indexes or pair.new_index in used_new_indexes:
            continue
        best_pairs.append(pair)
        used_old_indexes.add(pair.old_index)
        used_new_indexes.add(pair.new_index)

    return best_pairs


def _pair_edited_blocks(
    old_blocks: Sequence[ContentBlock],
    new_blocks: Sequence[ContentBlock],
    similarity_threshold: float,
) -> tuple[list[ContentBlock], list[ContentBlock], list[ChangedBlock]]:
    """Pairs each removed block in a region with the added block it was most likely edited into.

    Blocks left without a similar enough partner are genuine additions or removals.

    Args:
        old_blocks: The removed blocks in the region, in page order.
        new_blocks: The added blocks in the region, in page order.
        similarity_threshold: The minimum ratio (0.0 to 1.0) for two blocks to count as an edit.

    Returns:
        A tuple containing three elements:
            - The added ContentBlock objects with no partner, in page order.
            - The removed ContentBlock objects with no partner, in page order.
            - A ChangedBlock for each edited pair, in the order they appear on the new page.
    """
    candidate_pairs = _score_candidate_pairs(old_blocks, new_blocks, similarity_threshold)
    best_pairs = sorted(_select_best_pairs(candidate_pairs), key=lambda pair: pair.new_index)

    paired_old_indexes = {pair.old_index for pair in best_pairs}
    paired_new_indexes = {pair.new_index for pair in best_pairs}

    added = [block for index, block in enumerate(new_blocks) if index not in paired_new_indexes]
    removed = [block for index, block in enumerate(old_blocks) if index not in paired_old_indexes]
    changed = [
        ChangedBlock(
            old_block=old_blocks[pair.old_index],
            new_block=new_blocks[pair.new_index],
            similarity=pair.similarity,
        )
        for pair in best_pairs
    ]
    return added, removed, changed


def compare_page_content(
    old_content: PageContent,
    new_content: PageContent,
    similarity_threshold: float = TEXT_SIMILARITY_THRESHOLD,
) -> tuple[list[ContentBlock], list[ContentBlock], list[ChangedBlock]]:
    """Compares two PageContent objects to identify added, removed, and modified content blocks.

    Works in three steps:
        1. Blocks are lined up in page order by their parent heading and text. Blocks in an unchanged
           run are unchanged, and the stretches between unchanged runs are the page's changed regions.
        2. A leftover block whose exact text is still anywhere on the other page only moved, or now sits
           under a different heading or HTML tag, so it is unchanged too. A renamed heading is reported
           through the heading's own block instead.
        3. What is left in each region is paired by how similar the text is (most similar first), not by
           position, so an edit is matched to the right block. Unpaired blocks are reported as added or removed.

    As a result, the same text can never be reported as both added and removed.

    Args:
        old_content: The PageContent object representing the previous state.
        new_content: The PageContent object representing the current state.
        similarity_threshold: The text similarity ratio cutoff (0.0 to 1.0) for two blocks
            to count as an edit of each other. Defaults to 0.60.

    Returns:
        A tuple containing three elements:
            - A list of newly added ContentBlock objects.
            - A list of removed ContentBlock objects.
            - A list of ChangedBlock objects representing modified content.
    """
    unmatched_old, unmatched_new = _unmatched_blocks(old_content.blocks, new_content.blocks)

    removed_by_region = _blocks_by_region(_without_shared_text(unmatched_old, unmatched_new))
    added_by_region = _blocks_by_region(_without_shared_text(unmatched_new, unmatched_old))

    region_diffs = [
        _pair_edited_blocks(removed_by_region.get(region, []), added_by_region.get(region, []), similarity_threshold)
        for region in sorted(removed_by_region.keys() | added_by_region.keys())
    ]

    added = [block for region_added, _, _ in region_diffs for block in region_added]
    removed = [block for _, region_removed, _ in region_diffs for block in region_removed]
    changed = [change for _, _, region_changed in region_diffs for change in region_changed]
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
