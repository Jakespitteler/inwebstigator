# Inwebstigator

Inwebstigator monitors websites for changes. For each website it watches a set of
**critical pages** for edited, added and removed text, links and documents, and
crawls the rest of the site for pages that appear or disappear. Changes are shown
on a dashboard and emailed to each website's notification addresses.

It's a Windows desktop app: a [FastAPI](https://fastapi.tiangolo.com/) web app
running locally, shown in a native window by [pywebview](https://pywebview.flowrl.com/),
with scheduled scans while it runs. Everything is stored in a local SQLite database.

| Document | For |
| --- | --- |
| [INSTALL.md](INSTALL.md) | People using the app: installing it and using the dashboard. |
| This README | Developers: setting up, running, testing and building. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit together. |
| [docs/HANDOVER.md](docs/HANDOVER.md) | A team taking the project over: status, decisions, known issues and next steps. |

**Contents:** [Setting up](#setting-up) · [Running](#running) ·
[Configuration](#configuration) · [Email](#email) · [Testing](#testing) ·
[Building the Windows app](#building-the-windows-app) ·
[Project layout](#project-layout) · [Database](#database) ·
[Contributing](#contributing)

---

## Setting up

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which
installs the right Python (3.13) and every dependency for you.

```bash
git clone https://github.com/Jakespitteler/inwebstigator.git
cd inwebstigator
uv sync                      # creates .venv with the app and dev tools
cp .env.example .env         # then fill it in (see Configuration)
```

The app runs on macOS and Linux for development, but the desktop launcher
(`inwebstigator.py`) and the built `.exe` are Windows only.

> **macOS:** if the project folder is synced by iCloud (e.g. it's on your
> Desktop), iCloud can corrupt `.venv`. If imports suddenly fail, run
> `uv sync --reinstall`.

---

## Running

### As a web app (any OS)

```bash
uv run uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> for the dashboard, or <http://127.0.0.1:8000/docs>
for the API. In VS Code, the **Python Debugger: FastAPI** launch configuration
does the same with the debugger attached.

Useful while developing:

```bash
AUTOMATIC_SCANS=false uv run uvicorn app.main:app --reload    # no scheduled scans
DB_PATH=/tmp/inwebstigator-test.db uv run uvicorn app.main:app  # a separate database
```

Use `DB_PATH` (not `DB_NAME`) to point at another database. See
[Database](#database) for where the real one is.

### As the desktop app (Windows)

```bash
uv run python inwebstigator.py
```

This starts the web app on a local port (48731 if it's free) and opens it in a
window. Closing the window hides it to the system tray, where **Open**, **Hide**
and **Quit** control it. Only one copy can run at a time. `Ctrl+C` in the
terminal quits it. Pass `--debug` to open the browser developer tools.

---

## Configuration

Settings live in `app/core/config.py` and can be overridden by environment
variables or a `.env` file, using the setting's name in capitals (e.g.
`smtp_host` → `SMTP_HOST`). Variables already set in the environment win over
`.env`.

| Where `.env` is read from | |
| --- | --- |
| Development | The folder you run the command from (the project root). |
| Built app | The folder `inwebstigator.exe` is started from, i.e. next to the `.exe`. |

The settings you're most likely to need:

| Setting | Default | What it does |
| --- | --- | --- |
| `EMAIL` / `EMAIL_PASSWORD` | blank | The account emails are sent from. Also the From address. |
| `SMTP_HOST` / `SMTP_PORT` | blank / `465` | The mail server, over SSL. |
| `IMAP_HOST` / `IMAP_PORT` | blank / `993` | The same account's inbox, used to spot bounced confirmation emails. Optional. |
| `AUTOMATIC_SCANS` | `true` | Run scheduled scans and health check emails. |
| `DB_PATH` | `<home>/AppData/Local/inwebstigator/inwebstigator.db` | The SQLite database file. |
| `SERVER_PREFERRED_PORT` | `48731` | The desktop app's port, if free. |
| `WEB_CRAWLER_DEFAULT_MAX_PAGES` | `50000` | Websites with more pages are deactivated (critical pages only). |
| `WEB_CRAWLER_DEFAULT_DELAY` / `WEB_CRAWLER_DEFAULT_CONCURRENT` | `0.5` / `5` | Default crawl speed for new websites. |
| `SCHEDULER_DEFAULT_DAYS_BETWEEN_SCANS` | `1` | Default scan interval for new websites. |
| `SCHEDULER_MINIMUM_DAYS_BETWEEN_SCANS` | `0.5` | The shortest allowed interval, and how often the scheduler checks for due websites. |
| `SCHEDULER_DEFAULT_DAYS_BETWEEN_HEALTH_CHECKS` | `7` | Days without an email before a recipient gets a "Health Check". |

See `app/core/config.py` for the rest (crawler retries, cooldowns, diff
thresholds). `.env` is gitignored, so never commit real values.

---

## Email

Without email settings the app still works, but adding a notification email
fails because the confirmation can't be sent. Websites with no notification
emails are unaffected.

To send real email with a Gmail account:

1. Turn on 2-Step Verification on the Google account.
2. Create an **App Password** (Google account → Security → 2-Step Verification
   → App passwords). Your normal password won't work.
3. Fill in `.env`:

   ```bash
   EMAIL=you@gmail.com
   EMAIL_PASSWORD=abcdefghijklmnop   # the 16-character app password
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=465
   IMAP_HOST=imap.gmail.com           # optional: lets the app spot bounces
   ```

When an address is added, the app emails it and, if `IMAP_HOST` is set, watches
the sending account's inbox for about 30 seconds for a bounce. An address that
bounces isn't added.

UWA accounts won't work: Microsoft has turned off the basic SMTP login the app
uses. Use a personal or project Gmail account.

---

## Testing

```bash
uv run pytest                  # everything
uv run pytest -m "not e2e"     # skip the browser tests (much faster)
uv run pytest tests/e2e        # only the browser tests
```

| Folder | What's in it |
| --- | --- |
| `tests/unit/` | Fast tests of each module, using an in-memory database and fake websites. |
| `tests/e2e/` | Browser tests: the real dashboard in Chrome or Edge, driven by [Playwright](https://playwright.dev/python/), against the app running in the background with a throwaway database, fake websites and recorded (unsent) emails. |
| `tests/integration/` | Manual scripts for crawling real websites. Not run by `pytest`. |

The browser tests use Google Chrome or Microsoft Edge if installed. Otherwise
run `uv run playwright install chromium`, or skip them with `-m "not e2e"`.
They always run after the other tests, and if any test runs for over 5 minutes
pytest prints where it's stuck.

Linting and formatting use [Ruff](https://docs.astral.sh/ruff/):

```bash
uv run ruff check .
uv run ruff format .
```

---

## Building the Windows app

On Windows:

```bash
uv sync
uv run pyinstaller inwebstigator.spec
```

This produces `dist/inwebstigator/`, containing `inwebstigator.exe` and its
supporting files. To send it to someone:

1. Copy a `.env` with the email settings into `dist/inwebstigator/`, next to
   `inwebstigator.exe`.
2. Zip the `dist/inwebstigator` folder.

The `.exe` opens a console window alongside the app, which shows its logs.
The app's data is kept outside this folder (see [Database](#database)), so a
new version can be unzipped over an old one.

---

## Project layout

```
inwebstigator.py         Desktop launcher (Windows): local server, window, system tray
inwebstigator.spec       PyInstaller build settings
app/
  main.py                Creates the FastAPI app, database tables and upgrades
  scheduler.py           Scheduled scans and health check emails (APScheduler)
  scanner.py             Scanning websites: the scan queue, cancelling, reports, emails
  backend/
    site_crawler.py      Crawls a website for its internal pages
    engine.py            Works out what changed on a website and its critical pages
    diff_checker.py      Compares page text block by block
    format_message.py    Email HTML
    email_service.py     Sending email and spotting bounces
    utils/               Fetching pages, reading HTML, URLs and links
  core/                  Settings, errors, logging, file paths
  db/
    schema.py            Database tables (SQLAlchemy)
    migrations.py        Upgrades databases made by older versions
    repository.py        Generic database operations
    services/            Database operations for websites, pages, links, recipients
  models/                Request and response shapes (Pydantic)
  frontend/
    api/routers.py       Dashboard page and API routes
    templates/           Dashboard HTML (Jinja2)
    static/              CSS, JavaScript, logos and icon
tests/                   See Testing
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how these work together.

---

## Database

The database is a SQLite file at `<home>/AppData/Local/inwebstigator/inwebstigator.db`
(`%LOCALAPPDATA%\inwebstigator` on Windows; the same path under your home
folder on macOS). Delete it to start again from nothing.

There's no migration tool. On startup, `app/main.py` runs:

- `Base.metadata.create_all()`, which creates missing tables.
- `add_missing_columns()`, which adds columns added to the models since a
  database was made. New columns must be nullable or have a server default.
- `update_indexes()`, which recreates indexes whose uniqueness changed and adds
  new ones. Only suitable for loosening a rule, or one existing rows already meet.

Anything else (renaming or removing a column, changing a type, rewriting data)
needs its own upgrade step in `app/db/migrations.py`, so people's existing
databases keep working.

---

## Contributing

- Work on a branch per issue, and open a pull request into `main`.
- Write `Closes #<issue>` in the pull request so the issue closes when it's merged.
- Before opening it, run `uv run pytest` and `uv run ruff check .` and
  `uv run ruff format .`.
- Add tests with your change: unit tests for logic, browser tests for anything
  on the dashboard.
- Open issues for bugs and ideas rather than TODO lists in the code or docs.
