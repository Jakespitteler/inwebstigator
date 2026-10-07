from collections import defaultdict
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from typing import NamedTuple

from app.backend.diff_checker.models import ChangedBlock, ChangedRegion, ContentBlock, MatchedText


class _PlacedBlock(NamedTuple):
    """A block in a changed region, with where it sits so it can be found again after matching.

    Attributes:
        region: Which changed region the block is in, counting from the top of the page.
        position: The block's position among its side's blocks in that region.
        block: The content block itself.
    """

    region: int
    position: int
    block: ContentBlock


def _place_blocks(block_groups: Iterable[Sequence[ContentBlock]]) -> list[_PlacedBlock]:
    """Lists every block in the changed regions with its region and position, in page order.

    Args:
        block_groups: One side's blocks (old or new) for each changed region, in page order.

    Returns:
        Each block with its place.
    """
    return [
        _PlacedBlock(region=region, position=position, block=block)
        for region, blocks in enumerate(block_groups)
        for position, block in enumerate(blocks)
    ]


def _copies_by_text(placed_blocks: Iterable[_PlacedBlock]) -> defaultdict[str, list[_PlacedBlock]]:
    """Groups blocks by their text, keeping each group in page order.

    Args:
        placed_blocks: The blocks to group.

    Returns:
        The blocks with each text.
    """
    copies: defaultdict[str, list[_PlacedBlock]] = defaultdict(list)
    for placed_block in placed_blocks:
        copies[placed_block.block.text].append(placed_block)
    return copies


def _pair_copies(
    old_copies: Sequence[_PlacedBlock],
    new_copies: Sequence[_PlacedBlock],
) -> list[tuple[_PlacedBlock, _PlacedBlock]]:
    """Pairs the old and new copies of one text, one for one.

    A copy that is still under the same heading is paired with that copy first, so text that repeats on a page
    (e.g. "Read more") is matched within its own section. The copies left over are paired in page order.

    Args:
        old_copies: The blocks with this text on the old side, in page order.
        new_copies: The blocks with this text on the new side, in page order.

    Returns:
        The (old, new) pairs. Copies without a partner are left out.
    """
    unpaired_new: list[_PlacedBlock] = list(new_copies)
    pairs: list[tuple[_PlacedBlock, _PlacedBlock]] = []
    unpaired_old: list[_PlacedBlock] = []

    for old_copy in old_copies:
        same_section = next(
            (new_copy for new_copy in unpaired_new if new_copy.block.parent_heading == old_copy.block.parent_heading),
            None,
        )
        if same_section is None:
            unpaired_old.append(old_copy)
            continue
        pairs.append((old_copy, same_section))
        unpaired_new.remove(same_section)

    return [*pairs, *zip(unpaired_old, unpaired_new, strict=False)]


def _is_move(
    old_block: ContentBlock,
    new_block: ContentBlock,
    old_sections: set[str | None],
    new_sections: set[str | None],
) -> bool:
    """Checks whether a block whose text is on both versions of the page moved to a different section.

    It only counts as a move when both sections are on both versions of the page, e.g. "Closed" moving from
    "Saturday" to "Sunday". If its old section was renamed or removed, or its new section is new, the text is
    the same content under a changed heading, and the heading's own block reports that change.

    Args:
        old_block: The block on the old page.
        new_block: The block with the same text on the new page.
        old_sections: The headings of every section on the old page.
        new_sections: The headings of every section on the new page.

    Returns:
        True if the text moved between two sections that are on both versions of the page.
    """
    return (
        old_block.parent_heading != new_block.parent_heading
        and old_block.parent_heading in new_sections
        and new_block.parent_heading in old_sections
    )


def _unmatched(blocks: Sequence[ContentBlock], region: int, matched: set[tuple[int, int]]) -> list[ContentBlock]:
    """Keeps the blocks of one side of a changed region that were not matched.

    Args:
        blocks: One side's blocks (old or new) in the region, in page order.
        region: Which changed region the blocks are in.
        matched: The (region, position) of each matched block on that side.

    Returns:
        The blocks that were not matched, in page order.
    """
    return [block for position, block in enumerate(blocks) if (region, position) not in matched]


def _without_matched(
    regions: Sequence[ChangedRegion],
    matched_old: set[tuple[int, int]],
    matched_new: set[tuple[int, int]],
) -> list[ChangedRegion]:
    """Removes the matched blocks from each changed region.

    Args:
        regions: The changed regions, from the top of the page down.
        matched_old: The (region, position) of each matched block on the old side.
        matched_new: The (region, position) of each matched block on the new side.

    Returns:
        The same regions, holding only the blocks that were not matched.
    """
    return [
        ChangedRegion(
            old_blocks=_unmatched(region.old_blocks, index, matched_old),
            new_blocks=_unmatched(region.new_blocks, index, matched_new),
        )
        for index, region in enumerate(regions)
    ]


def _matched_pairs(
    old_copies: dict[str, list[_PlacedBlock]],
    new_copies: dict[str, list[_PlacedBlock]],
) -> list[tuple[_PlacedBlock, _PlacedBlock]]:
    """Pairs the old and new copies of every text found on both sides of the changes.

    Args:
        old_copies: The old side's blocks, grouped by text.
        new_copies: The new side's blocks, grouped by text.

    Returns:
        The (old, new) pairs of every shared text.
    """
    return [
        pair
        for text, copies in old_copies.items()
        if text in new_copies
        for pair in _pair_copies(copies, new_copies[text])
    ]


def find_changed_regions(old_blocks: Sequence[ContentBlock], new_blocks: Sequence[ContentBlock]) -> list[ChangedRegion]:
    """Lines up two versions of a page and returns the stretches between their unchanged runs of blocks.

    Blocks are lined up by their parent heading as well as their text, so text that repeats on a page
    (e.g. "Read more") is matched within its own section. A block left over only because its heading
    changed, or because it moved, is caught afterwards by `match_text_found_on_both_pages`.

    Args:
        old_blocks: The content blocks from the previous version of the page, in page order.
        new_blocks: The content blocks from the current version of the page, in page order.

    Returns:
        Each changed region, from the top of the page down.
    """
    matcher = SequenceMatcher(
        None,
        [(block.parent_heading, block.text) for block in old_blocks],
        [(block.parent_heading, block.text) for block in new_blocks],
        autojunk=False,
    )
    return [
        ChangedRegion(old_blocks=old_blocks[old_start:old_end], new_blocks=new_blocks[new_start:new_end])
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes()
        if tag != "equal"
    ]


def match_text_found_on_both_pages(
    regions: Sequence[ChangedRegion],
    old_sections: set[str | None],
    new_sections: set[str | None],
) -> MatchedText:
    """Matches up each block whose exact text is on both sides of the changes, anywhere on the page.

    Matched text that stayed in its section, or sits under a heading that was renamed, or under a different HTML
    tag, has not changed and is dropped. Matched text that moved between two sections that are on both versions
    of the page (e.g. "Closed" moving from "Saturday" to "Sunday") is reported as a move. Blocks are matched one
    for one, so text that repeats on a page is only matched as many times as it appears on both sides, and a copy
    that really was removed is still kept.

    Args:
        regions: The changed regions of the page, from the top down.
        old_sections: The headings of every section on the old page.
        new_sections: The headings of every section on the new page.

    Returns:
        The regions without the matched blocks, and the blocks that moved.
    """
    old_copies = _copies_by_text(_place_blocks(region.old_blocks for region in regions))
    new_copies = _copies_by_text(_place_blocks(region.new_blocks for region in regions))
    pairs: list[tuple[_PlacedBlock, _PlacedBlock]] = _matched_pairs(old_copies, new_copies)

    moved_pairs = sorted(
        (pair for pair in pairs if _is_move(pair[0].block, pair[1].block, old_sections, new_sections)),
        key=lambda pair: (pair[1].region, pair[1].position),
    )
    return MatchedText(
        regions=_without_matched(
            regions,
            matched_old={(old.region, old.position) for old, _ in pairs},
            matched_new={(new.region, new.position) for _, new in pairs},
        ),
        moves=[ChangedBlock(old_block=old.block, new_block=new.block, similarity=1.0) for old, new in moved_pairs],
    )
