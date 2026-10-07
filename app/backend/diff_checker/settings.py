from dataclasses import dataclass
from typing import Self

from app.core.config import config


@dataclass(frozen=True, slots=True)
class DiffSettings:
    """The tuning values used when comparing two versions of a page.

    Attributes:
        similarity_threshold: The minimum text similarity (0.0 to 1.0) for two blocks to count as an edit
            of each other rather than a removal and an addition.
        max_comparisons_per_block: The most old blocks each new block is compared with when looking for edits.
            Normal sized changes are well under this, but it stops a full page rewrite taking minutes.
        min_content_ratio: The share (0.0 to 1.0) of the old page's text the new page must have to count as
            the real page. Below it, the new page is treated as a stand-in (e.g. a "Just a moment..." check
            or a maintenance page) rather than as the page's new content.
        min_content_chars: How much text the old page must have before the stand-in check applies, so a
            page with very little text can still shrink.
    """

    similarity_threshold: float = 0.6
    max_comparisons_per_block: int = 20
    min_content_ratio: float = 0.3
    min_content_chars: int = 200

    @classmethod
    def from_config(cls) -> Self:
        """Reads the settings from the app's configuration, at the time it is called.

        Returns:
            The settings currently configured.
        """
        return cls(
            similarity_threshold=config.diff_checker_text_similarity_threshold,
            max_comparisons_per_block=config.diff_checker_max_comparisons_per_block,
            min_content_ratio=config.diff_checker_min_content_ratio,
            min_content_chars=config.diff_checker_min_content_chars,
        )
