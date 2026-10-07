from app.backend.email_service.html_bodies import inline_styles, parse_stylesheet

RULES = parse_stylesheet(
    """
    /* A comment that is ignored */
    .badge, .note { padding: 2px; color: black; }
    .badge.added { color: green; }
    """
)


def test_parse_stylesheet_reads_each_rule_and_ignores_comments() -> None:
    assert [rule.selector for rule in RULES] == [".badge, .note", ".badge.added"]
    assert RULES[0].declarations == {"padding": "2px", "color": "black"}


def test_inline_styles_writes_the_rules_of_each_class_on_the_tag() -> None:
    html: str = inline_styles('<span class="note">Hi</span>', RULES)

    assert html == '<span class="note" style="padding: 2px; color: black;">Hi</span>'


def test_inline_styles_lets_a_later_rule_win() -> None:
    html: str = inline_styles('<span class="badge added">Hi</span>', RULES)

    assert 'style="padding: 2px; color: green;"' in html


def test_inline_styles_keeps_the_styles_a_tag_already_has() -> None:
    html: str = inline_styles('<span class="note" style="margin: 0;">Hi</span>', RULES)

    assert 'style="margin: 0; padding: 2px; color: black;"' in html


def test_inline_styles_leaves_tags_without_a_matching_class_alone() -> None:
    assert inline_styles("<p>Hi</p>", RULES) == "<p>Hi</p>"
