from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple, Self

from pydantic import BaseModel, Field


class HTMLBlockType(StrEnum):
    """The kinds of HTML element a block of page text can come from."""

    PARAGRAPH = "p"
    HEADING_1 = "h1"
    HEADING_2 = "h2"
    HEADING_3 = "h3"
    HEADING_4 = "h4"
    HEADING_5 = "h5"
    HEADING_6 = "h6"
    CODE = "pre"
    QUOTE = "blockquote"
    LIST_ITEM = "li"
    TABLE_ROW = "tr"
    TABLE_CAPTION = "caption"
    FIGURE_CAPTION = "figcaption"
    DEFINITION_TERM = "dt"
    DEFINITION = "dd"
    TEXT = "text"


class ContentBlock(BaseModel):
    """Represents a structured block of parsed HTML text contextually tied to its parent heading.

    Attributes:
        parent_heading: The most recent section heading preceding this content block,
            or None if no preceding heading exists.
        block_type: The structural HTML block type classification for this content. Text that sits straight
            inside a container such as a `<div>` or `<section>`, rather than in a paragraph or list, is `TEXT`.
        text: The normalized text content contained within the block.
    """

    parent_heading: str | None = None
    block_type: HTMLBlockType
    text: str


class ChangedBlock(BaseModel):
    """A block of text that was edited, or moved to a different section, between two versions of a page.

    Attributes:
        old_block: The original content block state before modification.
        new_block: The updated content block state after modification. Its text is the same as the old block's
            when the block only moved to a different section.
        similarity: A floating-point score ranging from 0.0 to 1.0 indicating
            the similarity between the two blocks' text.
    """

    old_block: ContentBlock
    new_block: ContentBlock
    similarity: float

    @property
    def is_move(self) -> bool:
        """Whether the block kept its text and only moved to a different section."""
        return self.old_block.text == self.new_block.text


class PageContent(BaseModel):
    """Container model representing the complete parsed content and metadata of an HTML page.

    Attributes:
        headings: A list of all section titles extracted sequentially from the document.
        blocks: A list of all structured content blocks extracted sequentially.
        last_updated: A string representation of the page's last update timestamp/date,
            if extracted, otherwise None.
    """

    headings: list[str] = Field(default_factory=list[str])
    blocks: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    last_updated: str | None = None

    @property
    def sections(self) -> set[str | None]:
        """The headings of every section on the page, including the section before the first heading."""
        return {*self.headings, *(block.parent_heading for block in self.blocks)}

    @property
    def text_length(self) -> int:
        """How many characters of text the page's blocks hold, used to tell a real page from a stand-in page."""
        return sum(len(block.text) for block in self.blocks)


@dataclass(frozen=True, slots=True)
class ChangedRegion:
    """A stretch of the page between two unchanged runs of blocks, where the old and new versions differ.

    Attributes:
        old_blocks: The blocks in this stretch of the old page, in page order.
        new_blocks: The blocks in this stretch of the new page, in page order.
    """

    old_blocks: Sequence[ContentBlock]
    new_blocks: Sequence[ContentBlock]


@dataclass(frozen=True, slots=True)
class MatchedText:
    """The changed regions of a page once the text found on both versions of the page has been matched up.

    Attributes:
        regions: The changed regions, without the blocks whose text was found on both versions.
        moves: The blocks whose text moved to a different section, in the order they appear on the new page.
    """

    regions: list[ChangedRegion]
    moves: list[ChangedBlock]


class ContentDiff(NamedTuple):
    """What changed in a page's text between two scans.

    It is a NamedTuple, so it can still be unpacked as `added, removed, changed = compare_page_content(...)`.

    Attributes:
        added: Blocks that are new on the page, in page order.
        removed: Blocks that are no longer on the page, in page order.
        changed: Blocks whose text was edited, in the order they appear on the new page, followed by blocks
            that moved to a different section.
    """

    added: list[ContentBlock]
    removed: list[ContentBlock]
    changed: list[ChangedBlock]

    @classmethod
    def combine(cls, diffs: Sequence[Self]) -> Self:
        """Joins several diffs (e.g. one per changed region) into one, keeping their order.

        Args:
            diffs: The diffs to join, in page order.

        Returns:
            One diff holding every block from the given diffs.
        """
        return cls(
            added=[block for diff in diffs for block in diff.added],
            removed=[block for diff in diffs for block in diff.removed],
            changed=[change for diff in diffs for change in diff.changed],
        )

    def with_moves(self, moves: Sequence[ChangedBlock]) -> Self:
        """Adds the blocks that moved to a different section to the changed blocks.

        Args:
            moves: The blocks that moved, in the order they appear on the new page.

        Returns:
            A copy of the diff with the moves listed after the edits.
        """
        return self._replace(changed=[*self.changed, *moves])


class LinkDiff(NamedTuple):
    """Which links were added and removed between two scans.

    It is a NamedTuple, so it can still be unpacked as `added, removed = find_link_difference(...)`.

    Attributes:
        added: Links found now that were not there before.
        removed: Links that were there before but are no longer found.
    """

    added: list[str]
    removed: list[str]
