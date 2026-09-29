from difflib import SequenceMatcher

from markupsafe import Markup, escape


def build_word_diff(old_text: str, new_text: str) -> tuple[Markup, Markup]:
    old_words = old_text.split()
    new_words = new_text.split()

    matcher = SequenceMatcher(
        None,
        old_words,
        new_words,
        autojunk=False,
    )

    old_parts = []
    new_parts = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            old_parts.extend(escape(word) for word in old_words[i1:i2])
            new_parts.extend(escape(word) for word in new_words[j1:j2])

        elif tag == "delete":
            old_parts.extend(Markup(f'<span class="removed-word">{escape(word)}</span>') for word in old_words[i1:i2])

        elif tag == "insert":
            new_parts.extend(Markup(f'<span class="added-word">{escape(word)}</span>') for word in new_words[j1:j2])

        elif tag == "replace":
            old_parts.extend(Markup(f'<span class="removed-word">{escape(word)}</span>') for word in old_words[i1:i2])

            new_parts.extend(Markup(f'<span class="added-word">{escape(word)}</span>') for word in new_words[j1:j2])

    return (
        Markup(" ").join(old_parts),
        Markup(" ").join(new_parts),
    )
