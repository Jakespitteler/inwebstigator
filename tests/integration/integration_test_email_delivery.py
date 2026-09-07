"""Send one real sample report, to check the mail account actually works.

    python -m tests.integration.integration_test_email_delivery

Unlike integration_test_email_sender.py this talks to a real mail server, so
it needs a filled in .env with DRY_RUN=false (see README). It refuses to run if any of the mail settings
are still blank, so a half configured .env fails loudly rather than
pretending to send.

Sends one digest with made up changes. Nothing is scraped and no state is kept
-- this only answers "can we get mail out of this account".
"""

import sys
from datetime import UTC, datetime

from app.email_sender import build_message, notifier

# Before and after text for the sample content change, so the test email
# actually shows the side by side diff rather than a bare "this page changed".
_BEFORE = """Enrolment deadlines for Semester 2, 2026
Applications close on 15 July 2026.
Late applications may be considered at the discretion of the faculty.
Students must complete the online form before the closing date.
A late fee of $150 applies to applications received after the deadline.
Contact the Student Centre for assistance."""

_AFTER = """Enrolment deadlines for Semester 2, 2026
Applications close on 1 August 2026.
Late applications may be considered at the discretion of the faculty.
Students must complete the online form before the closing date.
A late fee of $220 applies to applications received after the deadline.
Payment plans are available for students experiencing hardship.
Contact the Student Centre for assistance."""

SITE_NAME = "example.edu.au"

SAMPLE = [
    notifier.Change(
        type="PAGE_CONTENT_CHANGED",
        url="https://example.edu.au/enrolment",
        label="Enrolment deadlines",
        old_text=_BEFORE,
        new_text=_AFTER,
    ),
    notifier.Change(
        type="FILE_ADDED",
        url="https://example.edu.au/docs/handbook.pdf",
        label="handbook-2026.pdf",
    ),
]


def main() -> int:
    problems = build_message.configuration_problems()
    if problems:
        print("Not configured to send:")
        for p in problems:
            print(f"  - {p}")
        print("\nSee the 'Sending real test email' section of the README.")
        return 1

    print(f"Sending a sample report as {build_message.SMTP_USER}")
    print(f"  via  {build_message.SMTP_HOST}:{build_message.SMTP_PORT}")
    print(f"  to   {', '.join(build_message.CLIENT_TO)}")

    action = notifier.notify(SAMPLE, site_name=SITE_NAME, now=datetime.now(UTC), dry_run=False)

    if action == "failed":
        print("\nFailed -- the error from the mail server is above.")
        return 1

    print(f"\nSent ({action}). Check the inbox, and the spam folder if it isn't there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
