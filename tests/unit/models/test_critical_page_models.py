"""Tests of the critical page models: what can be changed on a critical page."""

import re

import pytest
from pydantic import ValidationError

from app.models.critical_page_models import CriticalPageSettingsUpdate


def test_page_settings_only_change_the_ignore_rules() -> None:
    """Tests a settings change only updates the ignore rules sent, and ignores the page's saved copy, which only the
    scanner may save."""
    settings = CriticalPageSettingsUpdate.model_validate({"ignore_rules": ["Updated .*"], "text_body": "<p>Fake</p>"})

    assert settings.as_critical_page_update().model_dump(exclude_unset=True) == {
        "ignore_rules": [re.compile("Updated .*")]
    }


def test_an_ignore_rule_that_is_not_a_regular_expression_is_refused() -> None:
    """Tests an ignore rule that is not a valid regular expression is refused."""
    with pytest.raises(ValidationError, match="regular expression"):
        CriticalPageSettingsUpdate.model_validate({"ignore_rules": ["Fee is ($50"]})
