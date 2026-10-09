from difflib import SequenceMatcher
from enum import StrEnum
from typing import NamedTuple


class WordChange(StrEnum):
    """Whether a word in an edited block was kept, added or removed."""

    SAME = "same"
    ADDED = "added"
    REMOVED = "removed"


class DiffWord(NamedTuple):
    """One word of an edited block, marked with how it changed.

    Attributes:
        text: The word.
        change: Whether the word was kept, added or removed.
    """

    text: str
    change: WordChange


class WordDiff(NamedTuple):
    """The words of an edited block before and after the edit, each marked with how it changed.

    Attributes:
        old_words: The old text's words, each kept or removed.
        new_words: The new text's words, each kept or added.
    """

    old_words: list[DiffWord]
    new_words: list[DiffWord]


def _marked(words: list[str], change: WordChange) -> list[DiffWord]:
    """Marks each word in a run of words with the same change.

    Args:
        words: The words.
        change: How they changed.

    Returns:
        The marked words, in order.
    """
    return [DiffWord(text=word, change=change) for word in words]


def diff_words(old_text: str, new_text: str) -> WordDiff:
    """Works out which words changed between the old and new text of an edited block.

    The same word diff is shown on the dashboard and in the email, so a long paragraph with one changed
    amount points straight at the amount.

    Args:
        old_text: The block's text before the edit.
        new_text: The block's text after the edit.

    Returns:
        The old and new words, each marked as kept, removed or added.
    """
    old_words: list[str] = old_text.split()
    new_words: list[str] = new_text.split()
    matcher = SequenceMatcher(None, old_words, new_words, autojunk=False)

    old_side: list[DiffWord] = []
    new_side: list[DiffWord] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        is_same: bool = tag == "equal"
        old_side.extend(_marked(old_words[old_start:old_end], WordChange.SAME if is_same else WordChange.REMOVED))
        new_side.extend(_marked(new_words[new_start:new_end], WordChange.SAME if is_same else WordChange.ADDED))

    return WordDiff(old_words=old_side, new_words=new_side)
