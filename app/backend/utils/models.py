from pydantic import BaseModel, Field


class ContentBlock(BaseModel):
    parent_heading: str
    block_type: str
    text: str


class PageContent(BaseModel):
    headings: list[str] = Field(default_factory=list)
    blocks: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    links: list[str] = Field(default_factory=list)
    last_updated: str | None = Field(default=None)


class ChangedBlock(BaseModel):
    """Represents a block that was edited rather than completely replaced."""

    old_block: ContentBlock
    new_block: ContentBlock
    similarity: float


class ContentDiff(BaseModel):
    """The final record containing the results of the comparison."""

    added: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    removed: list[ContentBlock] = Field(default_factory=list[ContentBlock])
    changed: list[ChangedBlock] = Field(default_factory=list[ChangedBlock])
