
## Email notifications

Each daily run is a pipeline, one job per stage:

```
scraper      finds the pages and files on the site
diff finder  compares that against the previous run, produces Changes
notifier     packages those Changes into an email and sends it
```

`app/email_sender/notifier.py` is the last stage only. It is handed a list of
`Change` objects and does not scrape, does not work out what changed, and does
not read or write a database. What it sends:

- changes handed to it → a digest listing them
- nothing for 7 days → a "no changes detected" note
- otherwise → nothing

Everything in it is a plain function of its arguments. The envelope and the
one call that touches the network live in `app/email_sender/build_message.py`
(`build_message()` and `send_email()`), so the wording can be tested without a
mail server, and a mail server being down can't affect any stage upstream.

The weekly "no changes" note needs to know when we last emailed, which is not
something any earlier stage knows. So `notify()` takes `last_email_at` as an
argument rather than looking it up — the notifier stays stateless and the
caller owns the remembering. Pass `None` and the note simply never fires.

### run demo

```bash
uv run python -m tests.integration.integration_test_email_sender
```

Simulates three weeks of daily runs, emails are printed rather than sent. It
also stands in as a worked example of the caller: it keeps `last_email_at`
between runs, which is the one bit of state the notifier gives up.

Expect 4 emails over 21 days: digests on days 3 and 12, all clears on days 10
and 19. Day 1 is silent because there is no previous run to compare against,
so the diff finder reports nothing — otherwise the client gets 200 "new page"
lines on the first morning.

### Email format

Reports go out as multipart email: an HTML part and a plain text part carrying
the same information. Mail clients that render HTML show the HTML; anything
else falls back to the text, which is why the text part is never dropped.

For content changes the HTML part shows a **side by side before/after table**,
with the specific words that changed marked in red on the left and green on
the right. Only the differing parts are shown, plus two lines of unchanged text
either side for context, and the whole thing is capped at
`notifier.DIFF_MAX_ROWS` rows so one rewritten page can't produce an enormous
email.

Side by side needs two columns, and plain text only has about 78 characters to
work with, so the text part stacks the same edits instead:

```
Watched pages changed (1)
  * Enrolment deadlines
    https://example.edu.au/enrolment
      - Applications close on 15 July 2026.
      + Applications close on 1 August 2026.
      - A late fee of $150 applies after the deadline.
      + A late fee of $220 applies after the deadline.
```

The diff is built from `Change.old_text` and `Change.new_text`. Both are
optional — added and removed pages have nothing to compare, and a change that
arrives without them still emails fine, just as a plain "this page changed"
line. **Nothing populates them yet**: the diff finder holds both versions at
the moment it decides a page changed, so it has to pass them through. Until it
does, content changes email without a diff.

Scraped page text is untrusted, so everything is HTML-escaped, and only
`http`/`https` URLs are turned into clickable links.

Times are shown in `REPORT_TIMEZONE` (default `Australia/Perth`), because the
client reads them, not the server. The 06:00 UTC run shows as `14:00 AWST`.
Display only — all the date arithmetic stays in UTC so a daylight saving jump
can't shift the weekly heartbeat.

Every message carries `Date` and `Message-ID` headers. Neither is added
automatically, and mail without them scores badly with spam filters — a report
in the junk folder looks exactly like a broken scraper.

### run the scheduler

```bash
uv run python -m app.scheduler.background_scheduler
```

The notification run happens **once a day at a wall clock time**
(`DAILY_RUN_HOUR` / `DAILY_RUN_MINUTE`, in `REPORT_TIMEZONE`), not on a
repeating interval. An interval loop drifts — the wait starts after the task
finishes, so the true period is the interval plus however long the run took,
and at daily intervals that walks the client's report later every day.
Scheduling against the clock has no drift to accumulate.

Restarts are safe. `last_run_at` is stored per website, so a process that comes
back up at lunchtime does not fire a second report for a day already covered.
It fires once on startup precisely so a machine booted after the daily slot
still covers that day.

Ctrl-C stops it. A task that raises is logged and the loop carries on, so one
bad run doesn't end monitoring.

### run tests

```bash
uv sync          # first time only
uv run pytest
```

### Configuration

Settings come from the environment, so no credentials live in source.

```bash
cp .env.example .env      # then fill it in
```

`app/core/config.py` reads `.env` for the whole project on import, so there is
nothing to `source`. Anything already exported wins over the file, so you can
still override a setting for one run
(`REPORT_TIMEZONE=UTC uv run python -m tests.integration.integration_test_email_sender`).
`.env` is gitignored — never commit real values.

The addresses and the mail server default to blank rather than to a plausible
looking placeholder, so a half-filled `.env` fails loudly instead of mailing
somewhere nobody reads. The demo prints rather than sends, so it still runs
with no setup at all.

### Sending real test email

Nothing leaves the machine until you ask it to: `DRY_RUN` defaults to `true`,
which prints emails instead of sending them. That default is deliberate — a
fresh checkout can't mail anyone, and forgetting to configure `.env` fails
loudly rather than quietly mailing an address nobody reads.

To send yourself a real sample report with a Gmail account:

1. Turn on 2-Step Verification on the Google account.
2. Create an **App Password** (Google account → Security → 2-Step
   Verification → App passwords). It's 16 characters. Your normal Gmail
   password will not work — Google blocks plain logins from scripts.
3. Fill in `.env`:

   ```bash
   DRY_RUN=false
   EMAIL=you@gmail.com
   EMAIL_PASSWORD=abcdefghijklmnop     # the app password, spaces optional
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   CLIENT_TO=you@gmail.com             # your own address while testing
   ```

   `EMAIL` doubles as the From address — we send as the account we
   authenticate as, and Gmail rewrites a mismatched From anyway.

4. Send one sample report:

   ```bash
   uv run python -m tests.integration.integration_test_email_delivery
   ```

It checks the settings first and tells you exactly what's missing rather than
failing inside SMTP. Check the spam folder if nothing arrives — a brand new
sending address often lands there the first time.

A UWA account won't work for this: Microsoft turned off basic SMTP auth, so
use a personal Gmail (or a throwaway one) for testing. Whatever the client
ends up using is a question for them.

**Put `DRY_RUN` back to `true` when you're done testing**, so nobody runs the
scheduler and mails a real person by accident.

### State: what is remembered between runs

The notifier is stateless on purpose — it is handed a list of changes and told
when we last emailed, and it looks nothing up. That keeps the wording testable
without a database and stops a mail server outage reaching back up the
pipeline. The remembering happens in `app/services/notification_service.py`,
against two tables:

| table | what it holds |
| --- | --- |
| `notification_states` | per website: `last_run_at`, `last_email_at`, `last_action` |
| `pending_notifications` | reports whose send failed, with an attempt count and a next-attempt time |

`last_run_at` and `last_email_at` are deliberately separate. A run that finds
nothing still counts as a run — that is what stops a restart firing a second
report for the same day — but it must not move the email clock, or a site that
never changes would reset its own weekly window every morning and the all-clear
would never become due.

**A failed send no longer loses the day's changes.** `notify()` returns
`"failed"`, the service parks the changes as serialised `Change` dicts, and the
next run retries them before doing anything else. Retries back off
exponentially (`RETRY_BASE_DELAY_SECONDS`, capped at `RETRY_MAX_DELAY_SECONDS`)
and are given up on loudly after `RETRY_MAX_ATTEMPTS` rather than retrying
forever in silence. A failed *all-clear* is not parked — it carries no changes
and next week's note says the same thing.

### Many sites

The database models many websites, so each one gets its own report stamped with
its own URL, and its own weekly all-clear window. `notify()` takes `site_name`
and `recipients` as arguments for the same reason it takes `last_email_at`: the
notifier has no business looking any of that up. `site_name` is required — the
two scripts in `tests/integration/` pass their own, and `NotificationService`
passes the site's URL. `CLIENT_TO` in `.env` is still the fallback recipient.

Per-site recipients are not wired up — every report currently goes to
`CLIENT_TO`. That needs a client/subscriber table, which is a question for the
team rather than a code change.

### TODOs

These are marked in the code as well:

- **Diff text from the scraper side.** `Change.old_text`/`new_text` drive the
  side by side comparison but nothing fills them in yet — see "Email format".
  `DBCriticalPage.text_body` already stores the previous page text, so the data
  is there; the diff finder holds both versions at the moment it decides a page
  changed, so it is the one that has to pass them through.
- **The diff finder itself.** `collect_changes()` in the scheduler is the seam
  it plugs into, and returns an empty list today. Until it exists every run is
  quiet, which is also why a fresh database emails nobody.
- **Safety net for the site being down.** If most checks failed, "every page
  was deleted" is the wrong thing to email anyone. Needs the scraper to report
  how many checks passed and failed per run.
- **Per-site recipients.** See "Many sites" above — needs a team decision.
- **Tests.** The notifier, the scheduler and the notification service are
  covered under `tests/unit/`. The scraper and diff finder still need theirs.
