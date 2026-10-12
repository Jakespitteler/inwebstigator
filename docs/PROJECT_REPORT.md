# Project report

Inwebstigator, built for CITS3200 at the University of Western Australia in
2026 by Ahmed Jashim, Jake Spitteler, Made Aria Ravindrajaya, Nanasa Itami,
Patrick Caputi and Pooja Renjith Nair, for Jay Jay as the client.

This report covers what was built, how the code is organised, how it was
tested, and the trade-offs and ethical choices made along the way. The other
documents go into more detail:

| Document | Covers |
| --- | --- |
| [INSTALL.md](../INSTALL.md) | Using the app. |
| [README](../README.md) | Setting up, running, testing and building it. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How it works. |
| [EMAIL.md](EMAIL.md) | Looking after the email account. |
| [HANDOVER.md](HANDOVER.md) | Taking the project over. |

**Contents:** [Summary](#summary) · [Depth and breadth](#depth-and-breadth) ·
[Codebase structure](#codebase-structure) · [Testing](#testing) ·
[The email account: convenience against security](#the-email-account-convenience-against-security) ·
[Ethical choices](#ethical-choices)

---

## Summary

Inwebstigator watches websites for changes so people don't have to keep
checking them by hand. For each website it:

- watches a set of **critical pages** for edited, added and removed text,
  links and documents
- crawls the rest of the website for pages that appear or disappear
- shows the results on a dashboard, and emails them to the website's
  recipients

It runs as a Windows desktop app on the client's own computer, installed with
an installer, and scans on a schedule while it runs.

The project in numbers, as of 12 October 2026:

| | |
| --- | --- |
| Development | 27 July to October 2026 |
| Commits on `main` | 343 |
| Pull requests merged | 37 |
| Issues closed | 75 |
| Application code (Python) | about 9,600 lines |
| Test code | about 16,200 lines |
| Automated tests | 1,067 (1,016 unit, 51 browser) |

---

## Depth and breadth

### What it does

| Area | What was built |
| --- | --- |
| **Watching pages** | Reads the main content of each critical page into blocks of text under their headings. It reports edits word by word (before and after, side by side), text added, removed or moved to another section, links, and documents such as PDFs. |
| **Watching whole websites** | Crawls up to 50,000 pages per website and reports pages added and removed. Overlapping websites (e.g. a website and one of its sections) can both be watched. |
| **Avoiding false alarms** | Leaves out menus, side panels and each page's own header and footer. Doesn't trust a page that suddenly loses most of its text (often a "Just a moment…" check) or a website that suddenly loses most of its pages (often a partial outage) until it lasts 3 scans. Per-page rules can ignore text that changes on every visit. |
| **Scheduling** | Scans at 8am and 8pm, catching up on any check missed while the computer was off. Each website has its own interval. Scans run one at a time, and can be cancelled. |
| **Being polite to websites** | Slows down, and backs off for 24 hours, when a website rate limits or blocks it. Switches a website off after repeated blocking, and tells the user why. |
| **Emails** | One email per recipient covering all their websites, a weekly health check, and checks that a new address can receive email. A report that couldn't be sent is kept and sent later. |
| **History** | Keeps each website's recent scans and what they found, shown on the dashboard's Updates tab. |
| **The dashboard** | Adding websites in four steps, a card per website, Run and Cancel for one website or all of them, light and dark themes, and an About page. |
| **The desktop app** | A window and system tray icon, one copy at a time, starting at sign-in, logging to a file, and an installer that needs no administrator access. |

### Depth

Some areas went well beyond a first working version:

- **Change detection** went through several rounds:
  - **Text and sections:** from comparing whole pages, to blocks of text, to
    blocks with their headings (so moved text is told apart from new text).
  - **Edits:** word-by-word highlighting of what was edited.
  - **False alarms:** protection against stand-in pages and partial outages.
- **Reliability:**
  - Reports survive the mail server being down.
  - Times are saved in UTC.
  - Databases made by older versions are upgraded on startup.
  - A failed scan is reported as failed, rather than looking like "no changes".
- **Security of a local app:** the app has no login, so a token on every
  request stops other websites open in the user's browser from using it (see
  [ARCHITECTURE.md](ARCHITECTURE.md#the-request-guard)).

### What was left out, and why

| Left out | Why |
| --- | --- |
| Finding pages from sitemaps (#12) | Crawling links works on any website, including those with missing or out-of-date sitemaps. Marked out of scope. |
| Detecting changes by hashing pages (#9) | A hash only says a page changed, not what changed, which is what users need. Marked out of scope. |
| A hosted, multi-user version | A desktop app needs no server, login or hosting costs, and keeps the client's data on their computer. Scans therefore only run while the computer is on. |
| Reading `robots.txt` | Not done yet. See [Ethical choices](#ethical-choices). |

---

## Codebase structure

```
inwebstigator.py        The desktop app: window, system tray, local server
app/
  main.py               Starts the web app: logging, database, scheduler
  core/                 Settings, errors, logging, file paths, URL handling
  backend/
    crawler/            Fetching pages and crawling websites
    diff_checker/       Reading pages into blocks of text and comparing them
    scanning/           Scans, the queue, reports, scheduling, health checks
    websites/           Adding websites, critical pages and emails
    email_service/      Writing and sending emails
  db/                   The database: tables, upgrades, one service per table
  models/               The shapes of data passed around
  frontend/             The dashboard: routes, HTML templates, CSS, JavaScript
tests/                  Unit tests (tests/unit) and browser tests (tests/e2e)
scripts/, *.iss, *.spec Building the Windows app and its installer
.github/workflows/      Checks on every push, and the Windows build
```

The code is split by job: each folder under `backend/` does one thing, and the
dashboard, the scanning and the database only meet through small, named
functions. Everything runs in one process, so there's no separate worker,
queue or server to look after. [ARCHITECTURE.md](ARCHITECTURE.md) explains how
the pieces work together, with diagrams.

The code is checked on every push by Ruff (linting and formatting) and Pyright
in strict mode (types), as well as the tests.

---

## Testing

### Approach

The app's behaviour depends on websites and an email account that can't be
used in automated tests, so the tests replace them with fakes:

- **Websites** are fake pages served from memory, which a test can change,
  slow down, or make fail with any HTTP status.
- **Email** goes to a fake sender (`tests/fakes.py`) that records each email,
  so tests can check who was emailed what, and simulate failures.
- **The database** is a fresh in-memory database for each test, and the test
  run uses a temporary data folder, so tests never touch real data.

### Levels of testing

| Level | What it covers | How it runs |
| --- | --- | --- |
| **Unit tests** (1,016) | Each module on its own: parsing pages, comparing text, crawling, scheduling rules, failure handling, emails, the database and upgrades, the API routes, and the desktop launcher's window and tray logic (with the window mocked). | `uv run pytest -m "not e2e"`, about a minute and a half. |
| **Browser tests** (51) | The real dashboard in Chrome or Edge (driven by Playwright), against the real app running in the background: the Add Website steps, website cards, the Updates tab, Run All Scans and cancelling it, themes, the About page and Refresh. | `uv run pytest tests/e2e`, a few minutes. |
| **Continuous integration** | Every push and pull request runs Ruff, Pyright and the unit tests on GitHub. Changes to the build also build the installer on a Windows machine and check the packaged app starts. | GitHub Actions. |
| **Manual release checklist** | What can't be automated: the installer, the real window and tray, starting at sign-in, and real emails arriving. | By hand before each release ([HANDOVER.md](HANDOVER.md#release-checklist)). |

Unit tests by area:

| Area | Test functions |
| --- | --- |
| Scanning, scheduling and reports | 136 |
| Database and upgrades | 122 |
| Comparing page text | 97 |
| Emails | 92 |
| Dashboard routes and display | 90 |
| Crawler | 62 |
| Desktop launcher, app startup, overlapping websites and limits | 49 |
| Adding websites | 28 |
| Settings, URLs and logging | 28 |
| Data models | 24 |

(Some test functions run several cases each, which is why there are more tests
than functions.)

### Results

On 12 October 2026, all 1,015 runnable unit tests passed (1 is skipped), and
all 51 browser tests passed in Chrome. Many bugs were fixed along with a test
that reproduces them, so they can't quietly come back, e.g. overlapping
websites failing to save, a hidden 100-website limit, and the same critical
page being added twice with and without a trailing `/`.

### What isn't covered

- **Real websites:** the tests use fake pages, so a website built in an
  unusual way could still confuse the parser. Early on, scripts crawled real
  websites by hand, but these were removed when the code was restructured.
- **Real email delivery:** only checked by hand, with the release checklist.
- **The installed app on different Windows versions:** only checked by the
  Windows build workflow (that it starts) and by hand.

---

## The email account: convenience against security

To send email, the app needs the login for an email account. The team chose
convenience for the client: the login (a Gmail **App Password**) is put in a
`.env` file inside the installer, so the client installs the app and it can
email straight away, with nothing to set up.

**The cost is security.** The password is stored as plain text next to the
app, both in the installer and on the client's computer. Anyone who gets a
copy of the installer, or access to that folder, can send email as the
Inwebstigator account.

**How the risk is limited:**

- **A dedicated account.** It's made just for Inwebstigator, so the password
  opens nothing else: no personal or university email, documents or files.
- **An App Password, not the real password.** It can only be used for email,
  can't change the account's settings, and can be cancelled at any time
  without affecting anything else.
- **Kept out of the code.** `.env` is never committed (it's in `.gitignore`),
  and the app keeps the password in a type that hides it from logs and error
  messages.
- **Clear instructions:** the README and handover say to only send the
  installer to people who should have it, and [EMAIL.md](EMAIL.md) explains
  how to change the password if a copy goes astray.

**More secure alternatives,** for a future version:

| Alternative | Gains | Costs |
| --- | --- | --- |
| The client enters the login in the app, stored in Windows Credential Manager | No password in the installer, and it's encrypted for the user's Windows account | The client has to set up an account and App Password themselves |
| Signing in with Google (OAuth) | No password stored at all, and access can be limited to sending | Registering the app with Google, and a more complex sign-in flow |
| Sending through an email service's API with a send-only key | The key can only send, and can be limited and monitored | A third-party service, possibly with costs |

For a test version given to one client, the team judged the risk acceptable,
given these limits.

---

## Ethical choices

### Towards the websites being watched

A crawler visits many pages of other people's websites, so it can cost them
bandwidth or even slow them down. The app tries to be a good guest:

- **It says who it is.** Every request carries the user agent
  `Inwebstigator/0.1; website change monitor`, so website owners can see what
  is visiting and why, rather than it pretending to be a browser.
- **It goes slowly.** One website is scanned at a time, with a delay before
  each request (half a second by default) and a limit on requests at once.
- **It backs off when asked.** When a website rate limits it, or is
  overloaded, it waits as long as the website asks (up to a minute), then slows down further
  and rests for 24 hours. If a website keeps blocking it, even at the slowest
  speed, it stops crawling that website and tells the user why. It never tries
  to get around a block.
- **It only reads public pages.** It doesn't log in, fill in forms or try to
  reach private pages, and it stops at 50,000 pages.
- **Not yet done: `robots.txt`.** The crawler doesn't read the file in which
  websites say which pages crawlers should avoid. Since it identifies itself
  and backs off when blocked, the harm is limited, but reading it is the most
  important next step (see [HANDOVER.md](HANDOVER.md#known-issues)).

### Towards the people emailed

- **Nobody is added without being told.** Every address gets an email as soon
  as it's added, saying what it will receive. An address that bounces isn't
  added, so a typo doesn't send reports to a stranger.
- **Automated emails are marked as automated** (`Auto-Submitted`), so they
  don't trigger out-of-office replies.
- **Honest reporting.** A scan that failed, or a website that was switched
  off, is reported as such, rather than looking like "no changes". The health
  check says if any website needs attention, rather than always saying all is
  well.

### Towards the client

- **Their data stays on their computer.** The websites they watch and what
  was found are stored locally and never sent to the team or anyone else. The
  app has no analytics or tracking. Its only outside requests are to the
  watched websites, the email account, and Google Fonts for the dashboard's
  fonts.
- **Protection from other websites.** As the app runs a local server, the
  request token stops other websites open in the user's browser from using
  it, e.g. to send emails.
- **Page content is treated as untrusted.** Text from watched websites is
  escaped everywhere it's shown, on the dashboard and in emails, so a
  malicious page can't inject code.
- **Accuracy over noise.** The false-alarm protections (stand-in pages,
  partial outages, menus left out) mean users can trust an alert when they get
  one.
