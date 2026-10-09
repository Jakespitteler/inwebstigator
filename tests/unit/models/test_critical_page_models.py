"""Tests of the critical page models: what a scan of a critical page reports, and what can be changed."""

import re

import pytest
from pydantic import HttpUrl, ValidationError

from app.core.config import config
from app.models.content_block_models import ContentBlock, HTMLBlockType
from app.models.critical_page_models import CriticalPageSettingsUpdate, CriticalPageUpdate


def test_a_page_update_with_only_a_new_baseline_has_no_changes() -> None:
    """Tests saving a page's copy, without anything new found on it, is not reported as a change."""
    assert not CriticalPageUpdate(text_body="<p>Fees</p>", links=[HttpUrl("https://example.com/a")]).has_changes


def test_a_page_update_with_new_text_has_changes() -> None:
    """Tests text found on the page that was not there before is reported as a change."""
    new_text = ContentBlock(block_type=HTMLBlockType.PARAGRAPH, text="Late fee is $20.")

    assert CriticalPageUpdate(recent_text_added=[new_text]).has_changes


@pytest.mark.parametrize(
    ("consecutive_failures", "reported"),
    [
        (config.critical_page_alert_after_failures - 1, False),
        (config.critical_page_alert_after_failures, True),
        (config.critical_page_alert_after_failures + 1, False),
    ],
)
def test_an_unreachable_page_is_reported_once_when_it_reaches_the_failure_limit(
    consecutive_failures: int, reported: bool
) -> None:
    """Tests a page that keeps failing is reported on the scan where it reaches the failure limit, and not on the
    scans before or after."""
    update = CriticalPageUpdate(consecutive_failures=consecutive_failures, last_failure_reason="HTTP 500")

    assert update.has_just_reached_failure_limit is reported
    assert update.has_changes is reported


def test_a_bad_link_set_after_the_update_is_made_is_refused() -> None:
    """Tests a value set on an update after it is made is checked too, so a bad link cannot be saved."""
    update = CriticalPageUpdate()

    with pytest.raises(ValidationError):
        update.links = ["not a url"]  # pyright: ignore[reportAttributeAccessIssue]


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
