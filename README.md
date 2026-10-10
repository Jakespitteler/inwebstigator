# TODO
- Updates page needs a parent card with the website and child card with the critical pages (currently they're all sequential) 
- Each website card should have its own last scan date and time
- Critical page count is unnecessary
- On critical pages sometimes it will add and remove the same text
- Initial scan saves to recent links added and all that so is showing up in the dashboard
- UI isn't allowing days between scans to be a float
- The add website card could probably be the same as the other website cards since Jay Jay wants to keep the advanced settings hidden
- Make the running first scan text bold and add a spinning loading icon
- Need a way to cancel the scan
- Have something saying you can close the window and the scan will run in the background
- Be able to add facebook.com without needing to write https://www.facebook.com
- Maybe only add a website to the db once the scan is complete
- "Daily changes" should be "Changes since last scan"
- check if email when website is added changes the interval it says


### TODOs — Reviewed by JJ

#### Scanning and scheduling

- **Add a loading indicator when a scan is running.**
- **Collapse the review/add-site UI while scanning.** After pressing "Run
  Scan", replace or collapse the larger UI into something small such as
  "Scanning [site]...".
- **Allow sites to be queued while a scan is running.** The UI should remain
  usable while another scan is in progress.
- **Queue scan requests.** If multiple scan buttons are pressed while scans
  are already running, requests should be queued rather than interrupting
  existing scans.
- **Prevent duplicate scan requests.** The same site should not be added to
  the scan queue more than once at a time.
- **Do not interrupt an existing scan when starting another scan.** Currently,
  starting a scan for an existing site while another site is being scanned
  appears to stop the current scan and start the new one. The desired behaviour
  is to queue the new scan and allow the current scan to finish.
- **Investigate scan queue persistence.** When a scan is interrupted/restarted,
  it is unclear whether the queue of unexplored links is being maintained
  correctly.

#### Website display

- **Make monitored websites collapsible.** Each website should initially show
  only its name (and potentially its link) and a scan button. Clicking anywhere
  in the website's box should expand it to show the additional information.
  This should make the dashboard easier to use with multiple monitored sites.
- **Website names (decided).** The emails and the add website form name a
  website by its host name without "www.", then its path if it is only part of
  a website, e.g. `teqsa.gov.au` or `example.gov.au/research` (`website_name`
  in `app/core/urls.py`, and `displayName` in `add-website.js`). The
  dashboard's cards show a friendlier title read from the saved home page,
  e.g. `TEQSA` (`website_card_title` in `app/frontend/api/utils.py`).
- **Make the latest scan time per-site.** Currently the latest scan time appears
  to be global and is displayed for all websites. Each monitored website
  should display its own latest scan time.
- **Add our application icon to the dashboard.**

#### Buttons and UI

- **Decide on the position of the "Add Critical Page" and "Add Email"
  buttons.** The suggestion was to place them to the left of the input field,
  alongside the scan button. JJ prefers either keeping the current layout or
  placing them on the right, as that is closer to his mouse.
- **Style the delete-website confirmation popup.**
- **Make deletion safer.** Default to "Cancel" rather than "OK" in the delete
  confirmation and make the destructive action harder to trigger accidentally.
- **Hide or de-emphasise the delete button** to reduce accidental deletion.
- **Add copyright and team information.** Consider adding a footer and/or an
  About/Contact page containing relevant copyright, team, and contact
  information.

#### Scan history and change tracking

- **Keep the history of the last 7 scans.** We need to keep track of the last 7 
  scans according to JJ
- **Investigate multiple changes per day.** Confirm whether all changes are
  currently being captured. If not, ensure that earlier changes are preserved
  rather than being replaced by the latest change.

### TODOs — Not reviewed by JJ

- **Allow the application to be quit from the terminal using `Ctrl+C`.**
- **Consider allowing advanced users to configure the sender email address.**
  Currently not considered necessary according to JJ.


## Email notifications

Each scan of a website runs in three stages:

```
crawler           finds the pages on the website, and loads each critical page
change detection  compares them with the last scan, and lists the changes
reports           records the scan, then emails its report to the website's recipients
```

| stage | code |
| --- | --- |
| crawler | `app/backend/crawler/` |
| change detection | `app/backend/scanning/change_detection.py`, with `app/backend/diff_checker/` comparing the text of each critical page |
| reports | `app/backend/scanning/website_scan.py` records the scan, `app/backend/scanning/scan_reports.py` emails it, and `app/backend/email_service/` writes and sends the emails |

A website's first scan, when it is added, only saves each page as a starting
point, so it reports nothing. Links in a page's `<nav>` and `<aside>` are left
out, like its text, so a change to a menu shared by every page isn't reported
on every critical page.

### The emails

| email | when it is sent |
| --- | --- |
| Website monitoring started | to each recipient when a website is added. It also checks the address: if it bounces, the website isn't added |
| Email address added to website monitoring | to a recipient added to a website later |
| Website update(s) | after a scheduled scan (or "Run All Scans") that found changes or ran into a problem. Each recipient gets one email covering all their websites |
| Manual scan | after "Run Scan Now", if it found changes or ran into a problem |
| Health check | to each recipient who hasn't been emailed for their `days_between_health_checks` (7 days by default), listing their websites and any that need attention |

The wording (subjects, times) is in `email_service/email_wording.py`, and the
HTML is in `email_service/templates/`, rendered by `email_service/html_bodies.py`,
which also copies the CSS onto each tag, as many email apps ignore `<style>`.

### Email format

Every email is multipart: the HTML, and a plain-text copy made from it
(`message_builder.html_to_text`) for email apps that don't show HTML.

For edited text, the HTML part shows a **side by side Before/After table**,
with the words taken out marked in red on the left and the words put in marked
in green on the right.

Page text is untrusted, so the templates escape everything (Jinja
autoescaping).

Times are shown in `EMAIL_TIME_ZONE` (e.g. `Australia/Perth`), or in the
computer's own time zone when it is blank, because the client reads them.

Every message carries `Date` and `Message-ID` headers, which Python doesn't
add itself, and without which spam filters score email badly — a report in the
junk folder looks exactly like a broken scraper. It also carries
`Auto-Submitted: auto-generated`, so out-of-office replies don't come back.

### Sending, retries and bounces

`SmtpEmailSender` (`email_service/delivery.py`) sends the emails. Port 465 is
encrypted from the start, and any other port (e.g. 587) is upgraded with
STARTTLS. The server's certificate is always checked.

- A temporary failure (a dropped connection, or a busy server's 4xx reply) is
  retried, up to `EMAIL_RETRY_MAX_ATTEMPTS` (3) tries.
- An address refused for good, or an email the server rejects (a 5xx reply),
  isn't retried, as it would fail the same way every time. An address refused
  only for now (a 4xx reply, e.g. greylisting) is kept for the next run.
- A scan report that couldn't be sent is kept, and sent with the next run (see
  "State" below).

Gmail and most other providers accept an email first and only report a missing
mailbox afterwards, by sending a delivery failure email back to the sending
account. So when an address is added, `confirm_address_can_receive_email`
emails it, then watches the sending account's inbox over IMAP for
`EMAIL_BOUNCE_WAIT_SECONDS` (30). The inbox server is worked out from
`SMTP_HOST` (e.g. `smtp.gmail.com` → `imap.gmail.com`) unless `IMAP_HOST` is
set. If the inbox can't be reached, the email is still sent, but a bounce
can't be seen.

### the scheduler

There is no separate scheduler process: it starts and stops with the app
(`app/backend/scanning/scheduler.py`, run from the FastAPI lifespan), so scans only happen while
Inwebstigator is running. Set `AUTOMATIC_SCANS=false` to turn it off.

It checks at 8am and 8pm on the computer's clock (`SCHEDULER_SCAN_TIME`, then
every `SCHEDULER_MINIMUM_DAYS_BETWEEN_SCANS`), at the same times whenever the app
was started. It also checks a second after the app starts, so a check missed
while the computer or the app was off (e.g. that morning's) catches up as soon
as the app opens. Each run:

1. Scans each website whose own "days between scans" has passed since its last
   scan, skipping websites on cooldown. The time is counted between the checks
   the last scan and this run fall in, so a scan that ran late (e.g. at 5pm,
   after the 8am check was missed) is still followed by one at the next
   morning's check. `SCHEDULER_SCAN_DUE_TOLERANCE_MINUTES` lets an interval that
   doesn't line up with the checks be scanned at the nearest one. Each
   recipient is emailed one report of the changes, and of any scans that failed.
2. Sends a health check to each recipient still on a website who has not been
   emailed for their `days_between_health_checks` (7 by default). Being sent a
   confirmation when added to a website counts as being emailed.

"Run All Scans" on the dashboard scans every website straight away, and the
scheduled checks carry on at their usual times. A run that raises is logged, and
the next run happens as normal.

### run the app

```bash
uv sync                          # first time only
uv run python inwebstigator.py   # the desktop app, as testers get it
```

The app answers only on `127.0.0.1` and `localhost`. Every request that changes
something (POST, PATCH, DELETE) must carry the `X-Inwebstigator-Token` header,
a new random token each time the app starts, which only the dashboard page is
given (`app/frontend/api/request_guard.py`). This stops a website open in the
user's normal browser from starting scans, adding websites or emailing their
recipients. To call the API by hand, e.g. from `/docs`, start the app with
`API_TOKEN_REQUIRED=false`.

The app logs to `%LOCALAPPDATA%\inwebstigator\logs\inwebstigator.log`, as well
as the terminal, keeping the last few files (`LOG_FILE_MAX_BYTES`,
`LOG_FILE_BACKUP_COUNT`), so a tester can send it to the team.

### The dashboard's files

The dashboard is one page, `app/frontend/templates/index.html`, built from
smaller templates: `updates/` for the Updates tab, `websites/` for the Websites
tab, plus the header and the delete confirmation dialog. Its styles are in
`app/frontend/static/css/` and its scripts in `app/frontend/static/js/`, one
file per part of the page. `index.html` loads them in order, which matters: a
later stylesheet can override an earlier one (`responsive.css` is last), and
the scripts share one scope, so each only uses what the scripts before it
define. A test checks every file in those folders is loaded and found.

### run tests

```bash
uv sync          # first time only
uv run pytest
```

The tests use a temporary data folder (see `conftest.py`), so they never touch
the real database or log file, and a fake email sender (`tests/fakes.py`), so
they never send email.

### Configuration

Settings come from the environment, so no credentials live in source.

```bash
cp .env.example .env      # then fill it in
```

`app/core/config.py` reads `.env` for the whole project on import, so there is
nothing to `source`. It is read from the project's folder, or in the packaged
app from the folder `inwebstigator.exe` is in, whichever folder the app was
started from. Anything already exported wins over the file, so you can
still override a setting for one run
(`EMAIL_TIME_ZONE=UTC uv run python inwebstigator.py`). Every field of `Config`
can be set this way, by its name in capitals. `.env` is gitignored — never
commit real values.

The addresses and the mail server default to blank rather than to a plausible
looking placeholder, so a half-filled `.env` fails loudly instead of mailing
somewhere nobody reads.

### Setting up the sending account

There is no dry-run mode: adding a website emails its recipients for real, so
while testing, only add your own addresses.

To send from a Gmail account:

1. Turn on 2-Step Verification on the Google account.
2. Create an **App Password** (Google account → Security → 2-Step
   Verification → App passwords). It's 16 characters. Your normal Gmail
   password will not work — Google blocks plain logins from scripts.
3. Fill in `.env`:

   ```bash
   EMAIL=you@gmail.com
   EMAIL_PASSWORD=abcdefghijklmnop     # the app password, spaces optional
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=465
   EMAIL_TIME_ZONE=Australia/Perth
   ```

   `EMAIL` doubles as the From address — we send as the account we
   authenticate as, and Gmail rewrites a mismatched From anyway. `IMAP_HOST`
   can stay blank for Gmail, as it is worked out from `SMTP_HOST`.

4. Start the app and add a website with your own address as its recipient.
   You should get a "Website monitoring started" email.

Check the spam folder if nothing arrives — a brand new sending address often
lands there the first time.

A UWA account won't work for this: Microsoft turned off basic SMTP auth, so
use a personal Gmail (or a throwaway one) for testing. Whatever the client
ends up using is a question for them.

### State: what is remembered between runs

Every scan of a website is recorded, rather than overwriting the last scan's
changes, in two tables (`app/db/schema.py`, read and written by
`app/db/services/scan_run_service.py`):

| table | what it holds |
| --- | --- |
| `scan_runs` | one row per scan of a website: when it ran, how it went (`status`, `message`) and `notified_at`, when its report was emailed |
| `changes` | one row per thing a scan found: a link, document or internal page added or removed, a block of text added, removed or edited, or a critical page that became unreachable |

Each website keeps its last 7 scans (`SCANS_KEPT_PER_WEBSITE`), which the
Updates tab shows newest first. Older scans are deleted after each scan, except
any whose report has not been emailed yet.

Every time is saved in UTC (`UTCDateTime` in `app/db/schema.py`), and code
makes times with `datetime.now(UTC)`: saving a time without a time zone is an
error. The dashboard shows times in the computer's own time zone, and emails in
`EMAIL_TIME_ZONE`. Older versions saved local times, so they are converted to UTC
once when the app starts (`convert_local_times_to_utc` in `app/db/migrations.py`,
recorded in SQLite's `user_version`).

**A failed send no longer loses the changes.** A report with `notified_at`
still empty is waiting to be emailed. Each scheduled run (and "Run All Scans")
emails every waiting report, one email per recipient, then fills in
`notified_at` (`app/backend/scanning/scan_reports.py`). If the mail server is
down, the reports stay waiting and go out with the next run. An address that is
refused for good, or an email the server rejects (a 5xx reply), is not tried
again, as it would fail the same way every time. A website with no recipients has its reports marked as
sent, as there is nobody to send them to.

### Many sites

Each website has its own recipients, critical pages and scan settings
(`days_between_scans`, request delay and concurrency), and each recipient has
their own `days_between_health_checks`. A recipient on several websites gets
one email per run covering all of them, not one per website
(`app/backend/scanning/notifications.py`).

### TODOs

- **Background scan jobs.** "Run Scan Now" and adding a website keep the
  request open until the scan finishes, which can take several minutes. They
  could start a job and let the dashboard check on it instead.
- **Database migrations.** `app/db/migrations.py` adds missing tables,
  columns and indexes when the app starts, but can't rename or change a
  column. Alembic would.
- **robots.txt.** The crawler doesn't read it yet.
