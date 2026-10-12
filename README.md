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
| [docs/EMAIL.md](docs/EMAIL.md) | Whoever looks after the app: fixing the emails if they stop, and handing over the email account. |
| [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md) | The project report: what was built, how it was tested, and the trade-offs and ethical choices made. |

**Contents:** [Setting up](#setting-up) · [Running](#running) ·
[Configuration](#configuration) · [Email](#email) · [Logs](#logs) ·
[Testing](#testing) · [Building the Windows app](#building-the-windows-app) ·
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
(`inwebstigator.py`) and the installer are Windows only.

> **macOS:** if the project folder is synced by iCloud (e.g. it's on your
> Desktop), iCloud can corrupt `.venv`. If imports suddenly fail, run
> `uv sync --reinstall`.

---

## Running

### As the desktop app (Windows)

```bash
uv run python inwebstigator.py
```

This starts the web app on a local port (48731 if it's free) and opens it in a
window. Closing the window hides it to the system tray, where **Open**, **Hide**
and **Quit** control it. Only one copy can run at a time. `Ctrl+C` in the
terminal quits it. Pass `--debug` to open the browser developer tools.

### As a web app (any OS)

```bash
uv run uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> for the dashboard.

Useful while developing:

```bash
AUTOMATIC_SCANS=false uv run uvicorn app.main:app --reload       # no scheduled scans
DB_PATH=/tmp/inwebstigator-test.db uv run uvicorn app.main:app     # a separate database
API_TOKEN_REQUIRED=false uv run uvicorn app.main:app               # call the API by hand
```

- **A separate database:** use `DB_PATH`, not `DB_NAME`, which is ignored (see
  [Database](#database)).
- **Calling the API:** every request that changes something (POST, PATCH,
  DELETE) needs the `X-Inwebstigator-Token` header, a random token made each
  time the app starts and only given to the dashboard page. This stops a
  website open in the user's normal browser from using the app. To try the API
  from FastAPI's docs at <http://127.0.0.1:8000/docs>, turn the check off with
  `API_TOKEN_REQUIRED=false`.

**There's no dry-run mode.** Adding a website really emails its recipients, so
only add your own addresses while testing.

---

## Configuration

Settings live in `app/core/config.py` and can be overridden by environment
variables or a `.env` file, using the setting's name in capitals (e.g.
`smtp_host` → `SMTP_HOST`). Variables already set in the environment win over
`.env`.

`.env` is read from the app's folder, whichever folder it was started from:

| | Where `.env` is read from |
| --- | --- |
| Development | The project's root folder. |
| Installed app | The folder `inwebstigator.exe` is in, where the installer puts it. |

The settings you're most likely to need:

| Setting | Default | What it does |
| --- | --- | --- |
| `EMAIL` / `EMAIL_PASSWORD` | blank | The account emails are sent from. Also the From address. |
| `SMTP_HOST` / `SMTP_PORT` | blank / `465` | The mail server. Port 465 uses SSL; any other port (e.g. 587) is upgraded with STARTTLS. |
| `IMAP_HOST` / `IMAP_PORT` | blank / `993` | The same account's inbox, used to spot bounced emails. If blank, it's worked out from `SMTP_HOST` (`smtp.` → `imap.`). |
| `EMAIL_TIME_ZONE` | blank | The time zone emails show times in, e.g. `Australia/Perth`. Blank uses the computer's own. |
| `AUTOMATIC_SCANS` | `true` | Run scheduled scans and health check emails. |
| `DB_PATH` | `<home>/AppData/Local/inwebstigator/inwebstigator.db` | The SQLite database file. |
| `API_TOKEN_REQUIRED` | `true` | Refuse changes that don't come from the dashboard (see [Running](#running)). |
| `SERVER_PREFERRED_PORT` | `48731` | The desktop app's port, if free. |
| `SCHEDULER_SCAN_TIME` | `08:00` | The first scheduled check of the day. |
| `SCHEDULER_MINIMUM_DAYS_BETWEEN_SCANS` | `0.5` | The shortest allowed scan interval, and the time between scheduled checks (so 8am and 8pm). |
| `SCHEDULER_DEFAULT_DAYS_BETWEEN_SCANS` | `1` | Default scan interval for new websites. |
| `SCHEDULER_DEFAULT_DAYS_BETWEEN_HEALTH_CHECKS` | `7` | Days without an email before a recipient gets a health check. |
| `SCANS_KEPT_PER_WEBSITE` | `7` | How many scans of each website are kept and shown on the Updates tab. |
| `WEB_CRAWLER_DEFAULT_MAX_PAGES` | `50000` | Websites with more pages are deactivated (critical pages only). |
| `WEB_CRAWLER_DEFAULT_DELAY` / `WEB_CRAWLER_DEFAULT_CONCURRENT` | `0.5` / `5` | Default crawl speed for new websites. |

See `app/core/config.py` for the rest (crawler retries and limits, cooldowns,
diff thresholds, log size). Email settings default to blank rather than a
placeholder, so a half-filled `.env` fails loudly. `.env` is gitignored, so
never commit real values.

---

## Email

Without email settings the app still works, but adding a website with
notification emails, or adding an email to a website, fails because the email
can't be sent. Websites with no notification emails are unaffected.

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
   EMAIL_TIME_ZONE=Australia/Perth
   ```

4. Start the app and add a website with your own address. You should get a
   "Website monitoring started" email. Check the spam folder if it doesn't
   arrive, as a new sending address often lands there at first.

When an address is added, the app emails it and watches the sending account's
inbox for about 30 seconds for a bounce. An address that bounces isn't added.
The inbox is found from `SMTP_HOST` (`smtp.gmail.com` → `imap.gmail.com`), so
set `IMAP_HOST` only if your provider's is named differently. If the inbox
can't be opened, the email is just sent, without checking for a bounce.

UWA accounts won't work: Microsoft has turned off the basic SMTP login the app
uses. Use a personal or project Gmail account.

If the emails stop, or the account needs handing over to someone else, see
[docs/EMAIL.md](docs/EMAIL.md).

---

## Logs

The app logs to the terminal and to
`<home>/AppData/Local/inwebstigator/logs/inwebstigator.log`
(`%LOCALAPPDATA%\inwebstigator\logs` on Windows), keeping the last few files
(`LOG_FILE_MAX_BYTES`, `LOG_FILE_BACKUP_COUNT`). The installed app has no
console window, so testers can send this file instead.

---

## Testing

```bash
uv run pytest                  # everything
uv run pytest -m "not e2e"     # skip the browser tests (much faster)
uv run pytest tests/e2e        # only the browser tests
```

| Folder | What's in it |
| --- | --- |
| `tests/unit/` | Fast tests of each module, using an in-memory database, fake websites and a fake email sender (`tests/fakes.py`). |
| `tests/e2e/` | Browser tests: the real dashboard in Chrome or Edge, driven by [Playwright](https://playwright.dev/python/), against the app running in the background with a throwaway database, fake websites and recorded (unsent) emails. |

`conftest.py` at the project root gives the tests a temporary data folder and
turns automatic scans off, so they never touch the real database or log file.

The browser tests use Google Chrome or Microsoft Edge if installed. Otherwise
run `uv run playwright install chromium`, or skip them with `-m "not e2e"`.
They always run after the other tests, and if any test runs for over 5 minutes
pytest prints where it's stuck.

The browser tests cover the dashboard, but not the Windows app around it (the
window, tray and installer). Check those by hand before each release, with the
[release checklist](docs/HANDOVER.md#release-checklist).

Linting, formatting and type checking:

```bash
uv run ruff check .
uv run ruff format .
uvx pyright
```

### Continuous integration

Two GitHub Actions workflows run on GitHub:

| Workflow | When | What it does |
| --- | --- | --- |
| **Checks** (`.github/workflows/checks.yml`) | Every push and pull request | Ruff, formatting, Pyright (strict) and the unit tests. Not the browser tests. |
| **Build Windows app** (`.github/workflows/build-windows.yml`) | When the build files change, or run by hand | Builds the installer on Windows and checks the packaged app starts. It uses `.env.example`, so its installer can't send email and is only for checking the build. |

---

## Building the Windows app

On Windows, with [uv](https://docs.astral.sh/uv/) and
[Inno Setup](https://jrsoftware.org/isdl.php) 6.5 or later installed:

```powershell
copy .env.example .env    # then fill in the email settings
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
```

This builds `dist\Inwebstigator Installer.exe` in two steps: PyInstaller
packages the app (`inwebstigator.spec`) into `dist\inwebstigator\`, then Inno
Setup wraps that folder into one installer (`inwebstigator_installer_script.iss`).
It can't be built on macOS or Linux, as PyInstaller only builds for the system
it runs on.

- **`.env` goes in the installer,** next to `inwebstigator.exe`. It holds the
  email account's password, so only send the installer to people who should
  have it.
- **It installs for the current user** (in `%LOCALAPPDATA%\Programs\Inwebstigator`),
  so it needs no administrator access. It adds a Start menu shortcut, an
  optional desktop shortcut and a shortcut that starts the app at sign-in
  (ticked by default). If the app is running, it asks for it to be quit first.
- **Uninstalling deletes the app's data** in `%LOCALAPPDATA%\inwebstigator`.
  Installing a new version over an old one keeps it.
- **There's no console window,** as the app runs in the system tray. See
  [Logs](#logs).
- **Change `MyAppVersion`** in the installer script for each version sent out.

---

## Project layout

```
inwebstigator.py                 Desktop launcher (Windows): local server, window, system tray
inwebstigator.spec               PyInstaller build settings
inwebstigator_installer_script.iss  Inno Setup installer settings
scripts/build_installer.ps1      Builds the app and its installer
conftest.py                      Test settings: a temporary data folder, no automatic scans
app/
  main.py                        Creates the FastAPI app: logging, database, scheduler, error responses
  core/                          Settings, errors, logging, file paths, URL handling
  backend/
    crawler/                     Fetching pages and crawling a website for its internal pages
    diff_checker/                Reading a page's text into blocks and comparing two versions
    scanning/                    Scans: the queue, change detection, failures, reports, scheduling, health checks
    websites/                    Adding websites, critical pages and emails, and website card titles
    email_service/               Writing emails (templates/) and sending them
  db/
    schema.py                    Database tables (SQLAlchemy)
    migrations.py                Brings databases made by older versions up to date
    session.py                   The database engine and sessions
    repository.py                Generic database operations
    services/                    Database operations for each table
  models/                        Request, response and scan result shapes (Pydantic)
  frontend/
    api/                         Dashboard page, API routes and the request guard
    templates/                   Dashboard HTML (Jinja2), split into websites/ and updates/
    static/                      css/, js/, logos and icon
tests/                           See Testing
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how these work together.

---

## Database

The database is a SQLite file at `<home>/AppData/Local/inwebstigator/inwebstigator.db`
(`%LOCALAPPDATA%\inwebstigator` on Windows; the same path under your home
folder on macOS). Delete it to start again from nothing.

There's no migration tool. On startup, `prepare_database()` in
`app/db/migrations.py`:

- **Creates missing tables** with `Base.metadata.create_all()`.
- **Adds missing columns** with `add_missing_columns()`, for columns added to
  the models since a database was made. New columns must be nullable or have a
  server default.
- **Updates indexes:** it drops unique indexes the models no longer have, and
  adds new ones.
- **Runs one-off updates,** such as converting older local times to UTC. Each
  runs once, recorded in SQLite's `user_version`.

Anything else (renaming or removing a column, changing a type, rewriting data)
needs its own one-off update in `app/db/migrations.py`, so people's existing
databases keep working. Every time is saved in UTC: use `datetime.now(UTC)`,
as saving a time without a time zone is an error.

---

## Contributing

- Work on a branch per issue, and open a pull request into `main`.
- Write `Closes #<issue>` in the pull request so the issue closes when it's merged.
- Before opening it, run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format .` and `uvx pyright`. The Checks workflow runs the same
  checks on every pull request.
- Add tests with your change: unit tests for logic, browser tests for anything
  on the dashboard.
- Open issues for bugs and ideas rather than TODO lists in the code or docs.
