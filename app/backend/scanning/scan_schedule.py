"""When the scheduled checks happen, and whether a website is due a scan at one of them.

Both the scheduler (which sets the checks' times) and each scan of the websites (which picks the websites due a scan)
use these, so they always agree on when a check is.
"""

from datetime import datetime, timedelta

from app.core.config import config
from app.models.website_models import WebsiteRead


def latest_check_time(moment: datetime) -> datetime:
    """Returns the scheduled check a moment falls in: the latest one at or before it.

    The checks are at `SCHEDULER_SCAN_TIME` on the computer's clock (8am by default), then every
    `SCHEDULER_MINIMUM_DAYS_BETWEEN_SCANS` after it (8pm by default), e.g. 3pm falls in the 8am check.

    Args:
        moment: A time with its time zone, e.g. when a website was last scanned.

    Returns:
        The time of the check, in the computer's own time zone.
    """
    local_moment: datetime = moment.astimezone()
    first_check_of_the_day: datetime = local_moment.replace(
        hour=config.scheduler_scan_time.hour, minute=config.scheduler_scan_time.minute, second=0, microsecond=0
    )
    if first_check_of_the_day > local_moment:
        first_check_of_the_day -= timedelta(days=1)
    time_between_checks: timedelta = timedelta(days=config.scheduler_minimum_days_between_scans)
    checks_since_the_first: int = (local_moment - first_check_of_the_day) // time_between_checks
    return first_check_of_the_day + checks_since_the_first * time_between_checks


def is_due_a_scan(website: WebsiteRead, run_started_at: datetime) -> bool:
    """Checks whether enough time has passed since a website's last scan for it to be scanned again.

    Time is counted between the scheduled checks the two scans fall in (see `latest_check_time`), so a scan that ran
    late (e.g. when the app was opened in the afternoon, after the morning's check was missed) does not put the
    website's next scan off from its usual time. Intervals such as 1.5 days are not rounded to whole days, and a small
    tolerance (never more than half the time between checks) lets an interval that does not line up with the checks
    be scanned at the nearest one.

    Args:
        website: The website to check.
        run_started_at: When the current run started.

    Returns:
        True if the website has never been scanned or its interval has (nearly) passed, otherwise False.
    """
    if website.last_scan_at is None:
        return True
    interval: timedelta = timedelta(days=website.days_between_scans)
    tolerance: timedelta = min(
        timedelta(minutes=config.scheduler_scan_due_tolerance_minutes),
        timedelta(days=config.scheduler_minimum_days_between_scans) / 2,
    )
    return latest_check_time(run_started_at) - latest_check_time(website.last_scan_at) >= interval - tolerance
