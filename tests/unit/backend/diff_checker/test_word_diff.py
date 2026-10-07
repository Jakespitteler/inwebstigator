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
