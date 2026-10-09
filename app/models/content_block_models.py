"""Blocks of text read from a page, as saved with each critical page and each scan.

They live with the other models, rather than in the diff checker that makes them, so the models and the database
do not depend on the backend.
"""

from enum import StrEnum

from pydantic import BaseModel


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
