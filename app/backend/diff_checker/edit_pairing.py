from collections.abc import Iterable, Iterator
from difflib import SequenceMatcher
from typing import NamedTuple

from app.backend.diff_checker.models import ChangedBlock, ChangedRegion, ContentDiff
from app.backend.diff_checker.settings import DiffSettings


class _ScoredPair(NamedTuple):
    """A possible pairing of an old block with a new block, scored by how similar their text is.

    Attributes:
        similarity: The SequenceMatcher ratio (0.0 to 1.0) between the two blocks' text.
        old_index: The position of the old block in its region.
        new_index: The position of the new block in its region.
    """

    similarity: float
    old_index: int
    new_index: int


def _comparison_window(old_count: int, new_position: int, new_count: int, max_comparisons: int) -> range:
    """Picks which old blocks in a region a new block is compared with when looking for edits.

    All of them in a normal sized region. In a very large region (e.g. a page rewrite), only the
    `max_comparisons` old blocks nearest the new block's relative position in the region,
    so a full page rewrite doesn't take minutes to compare.

    Args:
        old_count: How many old blocks are in the region.
        new_position: The new block's position among the region's new blocks.
        new_count: How many new blocks are in the region.
        max_comparisons: The most old blocks to compare it with.

    Returns:
        The positions of the old blocks to compare the new block with.
    """
    if old_count <= max_comparisons:
        return range(old_count)

    centre: int = new_position * old_count // new_count
    start: int = min(max(centre - max_comparisons // 2, 0), old_count - max_comparisons)
    return range(start, start + max_comparisons)


def _similarity_meeting_threshold(matcher: SequenceMatcher[str], similarity_threshold: float) -> float | None:
    """Scores how similar a matcher's two texts are, if they are similar enough to count as an edit.

    The two quick ratios are cheap upper bounds, so they are checked first and most unrelated
    pairs skip the slow exact ratio.

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


def _similar_pairs(region: ChangedRegion, settings: DiffSettings) -> Iterator[_ScoredPair]:
    """Scores each pairing of an old and a new block in a region that is similar enough to be an edit.

    Args:
        region: The changed region whose blocks are compared.
        settings: The similarity threshold and the most comparisons to make for each block.

    Yields:
        A _ScoredPair for each old/new pairing that meets the threshold.
    """

    for new_index, new_block in enumerate(region.new_blocks):
        matcher = SequenceMatcher(None, b=new_block.text, autojunk=False)

        for old_index in _comparison_window(
            old_count=len(region.old_blocks),
            new_position=new_index,
            new_count=len(region.new_blocks),
            max_comparisons=settings.max_comparisons_per_block,
        ):
            matcher.set_seq1(region.old_blocks[old_index].text)
            similarity: float | None = _similarity_meeting_threshold(matcher, settings.similarity_threshold)
            if similarity is not None:
                yield _ScoredPair(similarity=similarity, old_index=old_index, new_index=new_index)


def _best_pairs(scored_pairs: Iterable[_ScoredPair]) -> list[_ScoredPair]:
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


def pair_edits(region: ChangedRegion, settings: DiffSettings) -> ContentDiff:
    """Pairs each old block in a region with the new block it was most likely edited into.

    Blocks left without a similar enough partner are genuine additions or removals.

    Args:
        region: The changed region to look for edits in.
        settings: The similarity threshold and the most comparisons to make for each block.

    Returns:
        The region's added and removed blocks in page order, and its edits in the order they appear on the new page.
    """
    best_pairs: list[_ScoredPair] = sorted(
        _best_pairs(_similar_pairs(region, settings)),
        key=lambda pair: pair.new_index,
    )
    paired_old_indexes: set[int] = {pair.old_index for pair in best_pairs}
    paired_new_indexes: set[int] = {pair.new_index for pair in best_pairs}

    return ContentDiff(
        added=[block for index, block in enumerate(region.new_blocks) if index not in paired_new_indexes],
        removed=[block for index, block in enumerate(region.old_blocks) if index not in paired_old_indexes],
        changed=[
            ChangedBlock(
                old_block=region.old_blocks[pair.old_index],
                new_block=region.new_blocks[pair.new_index],
                similarity=pair.similarity,
            )
            for pair in best_pairs
        ],
    )
