from app.backend.diff_checker.word_diff import DiffWord, WordChange, diff_words


def changed_words(words: list[DiffWord]) -> list[str]:
    return [word.text for word in words if word.change is not WordChange.SAME]


def test_only_the_changed_words_are_marked() -> None:
    word_diff = diff_words("The application fee is $50 per year.", "The application fee is $60 per year.")

    assert changed_words(word_diff.old_words) == ["$50"]
    assert changed_words(word_diff.new_words) == ["$60"]
    assert {word.change for word in word_diff.old_words} == {WordChange.SAME, WordChange.REMOVED}
    assert {word.change for word in word_diff.new_words} == {WordChange.SAME, WordChange.ADDED}


def test_added_words_only_appear_on_the_new_side() -> None:
    word_diff = diff_words("Closed on Sunday.", "Closed on Sunday and public holidays.")

    assert changed_words(word_diff.old_words) == ["Sunday."]
    assert changed_words(word_diff.new_words) == ["Sunday", "and", "public", "holidays."]


def test_each_side_keeps_every_word_of_its_text_in_order() -> None:
    """Tests the Before side holds every old word and the After side every new word, in order, for highlighting."""
    old_text = "Apply online or at an office by 30 June."
    new_text = "Apply online by 14 July, or visit an office."

    word_diff = diff_words(old_text, new_text)

    assert [word.text for word in word_diff.old_words] == old_text.split()
    assert [word.text for word in word_diff.new_words] == new_text.split()
    assert all(word.change is not WordChange.ADDED for word in word_diff.old_words)
    assert all(word.change is not WordChange.REMOVED for word in word_diff.new_words)


def test_removed_words_only_appear_on_the_old_side() -> None:
    """Tests words taken out are marked removed on the old side, and the new side has no changed words."""
    word_diff = diff_words("Closed on Sunday and public holidays.", "Closed on Sunday and holidays.")

    assert changed_words(word_diff.old_words) == ["public"]
    assert changed_words(word_diff.new_words) == []


def test_spacing_differences_are_not_changes() -> None:
    """Tests text that only differs in spaces or line breaks has every word marked the same on both sides."""
    word_diff = diff_words("Fee  is\n$50", "Fee is $50")

    assert (
        word_diff.old_words
        == word_diff.new_words
        == [
            DiffWord("Fee", WordChange.SAME),
            DiffWord("is", WordChange.SAME),
            DiffWord("$50", WordChange.SAME),
        ]
    )


def test_text_added_to_an_empty_block_is_all_added() -> None:
    """Tests every word is marked added when the old text was empty."""
    word_diff = diff_words("", "Now open")

    assert word_diff == ([], [DiffWord("Now", WordChange.ADDED), DiffWord("open", WordChange.ADDED)])
