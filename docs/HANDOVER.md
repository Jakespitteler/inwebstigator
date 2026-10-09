# Handover

For a team taking Inwebstigator over in a repository of its own. It covers where
the project is, how to start your own copy, what you need to set up, how to
release it, what's unfinished, and why it's built the way it is.

Written on 9 October 2026, at the end of the original team's CITS3200 project.

Issue and pull request numbers (like #12) are from the
[original repository](https://github.com/Jakespitteler/inwebstigator), which
stays online as a record. They don't come across to your copy.

**Contents:** [Where the project is](#where-the-project-is) ·
[Starting your own repository](#starting-your-own-repository) ·
[First steps](#first-steps) · [Accounts and access](#accounts-and-access) ·
[Releasing a new version](#releasing-a-new-version) · [Unfinished work](#unfinished-work) ·
[Known issues](#known-issues) · [Design choices](#design-choices) ·
[Ideas for what's next](#ideas-for-whats-next) · [The original team](#the-original-team)

---

## Where the project is

Inwebstigator works, and has been given to the client.

- **It can:** monitor any number of websites; watch critical pages for
  changed text, links and documents; crawl whole websites for added and
  removed pages; scan on a schedule; email each recipient one report;
  slow down for or pause websites that struggle; and run as a Windows tray app.
- **It's tested by:** unit tests for each module, browser tests of the
  dashboard, and a [checklist](#release-checklist) for checking the Windows
  app by hand before each release.
- **It's documented in:** [INSTALL.md](../INSTALL.md) (users),
  the [README](../README.md) (developers) and
  [ARCHITECTURE.md](ARCHITECTURE.md) (how it works).

The client contact is **Jay Jay** (written "JJ" in some old issues and notes).
Most of the dashboard's behaviour (collapsible cards, safer deletion,
per-website scan times, the add-website steps) came from Jay Jay's feedback on
earlier versions.

---

## Starting your own repository

Copy the original repository with its history, so `git log` and `git blame`
still explain why code is the way it is:

```bash
git clone https://github.com/Jakespitteler/inwebstigator.git
cd inwebstigator
git remote rename origin original
```

Then create an **empty** repository on GitHub (no README, licence or
`.gitignore`) and push to it:

```bash
git remote add origin https://github.com/<your-account>/<your-repo>.git
git push -u origin main
```

Only `main` is pushed, and that's everything: no work was left on other
branches.

Then:

1. Change the clone address in the README's [Setting up](../README.md#setting-up)
   to your repository.
2. Open an issue in your repository for each of the
   [Known issues](#known-issues) you plan to fix.
3. Set up your own sending email account (see [Accounts and access](#accounts-and-access)).

A GitHub fork would also work, but its pull requests default to the original
repository and its issues start switched off, so a plain copy is simpler.

The repository has no licence file. Before making your copy public, check with
the unit coordinator and the client who owns the code.

---

## First steps

1. Start your own repository as above, or get added to the one your team made.
2. Follow the README's [Setting up](../README.md#setting-up) and
   [Running](../README.md#running), with `AUTOMATIC_SCANS=false` and a `DB_PATH`
   of your own so you don't scan real websites by accident.
3. Run `uv run pytest`. Everything should pass. Add `-m "not e2e"` to skip
   the browser tests, which take a couple of minutes.
4. Read [ARCHITECTURE.md](ARCHITECTURE.md), then add a small website from the
   dashboard and watch the logs as it's scanned.
5. Read [Unfinished work](#unfinished-work) and [Known issues](#known-issues)
   below before picking anything up.

---

## Accounts and access

| What | Where | Notes |
| --- | --- | --- |
| Code | Your new repository | The [original](https://github.com/Jakespitteler/inwebstigator) is public, so you can always look back at its issues and pull requests. It was once called `Digital-Horizon-Scan-Project`, and that address still redirects. |
| Sending email account | A Gmail account with an App Password | Make one for your team (see the README's [Email](../README.md#email)). The original team's account isn't handed over. UWA accounts don't work. |
| `.env` | Your machine, and next to the `.exe` in each build | Never committed. `.env.example` lists only the first few settings; see the README's [Configuration](../README.md#configuration) for the rest. |
| The client's data | `%LOCALAPPDATA%\inwebstigator` on the client's computer | Their websites and results. Nothing is stored anywhere else, so your new version carries on from it. |

**Every build contains the email password.** The `.env` shipped next to the
`.exe` is plain text, so anyone with the zip can send email from that account.
Use an account made just for Inwebstigator, and change its App Password if a
zip goes somewhere it shouldn't.

**Your first build changes the sender.** The client's current copy sends from
the original team's account until they install yours. Tell Jay Jay the new
address beforehand, so recipients can mark it as "Not spam".

---

## Releasing a new version

There are no GitHub releases or automated builds yet. A release is:

1. Merge everything into `main` and check `uv run pytest` passes.
2. On a Windows computer, follow the README's
   [Building the Windows app](../README.md#building-the-windows-app): build,
   copy `.env` next to `inwebstigator.exe`, and zip `dist/inwebstigator`.
3. Go through the [release checklist](#release-checklist) on a Windows
   computer that has never run Inwebstigator.
4. Send the zip with [INSTALL.md](../INSTALL.md) (or a PDF of it).
   The client quits the old version from the tray and unzips the new one over
   it. Their data is kept, and the database is upgraded on startup (see the
   README's [Database](../README.md#database)).

The `.exe` isn't code-signed, so Windows SmartScreen warns about it the first
time. INSTALL.md tells users how to get past this.

### Release checklist

The automated tests run the dashboard in a browser, which is the same engine
as the desktop window (Microsoft Edge's WebView2). They don't cover the window,
the tray or the build, so check these by hand with the zip you're about to send:

- [ ] Unzip it and open `inwebstigator.exe`. "Starting Inwebstigator" shows,
      then the dashboard, with its logo.
- [ ] Open the `.exe` again. A message says it's already running.
- [ ] Add a small website with your own email. The confirmation and "Website
      monitoring started" emails arrive.
- [ ] **Run scan** on it finishes and the dashboard reloads.
- [ ] **Run All Scans** starts, and its **Cancel** stops it.
- [ ] The **About** page opens, and the app name leads back to the dashboard.
- [ ] Close the window with the **X**. The tray icon stays, and **Open** brings
      the window back.
- [ ] Switch to dark mode, choose **Quit** from the tray, and reopen the app.
      It's still dark, and the website is still there.
- [ ] Unzip the new version over an older one that has websites saved. They're
      all still there.
- [ ] The console window shows no errors (tracebacks) throughout.

---

## Unfinished work

Nothing was left in progress. Every branch was merged or closed, and every
issue was closed except two that were marked out of scope:

- [#12](https://github.com/Jakespitteler/inwebstigator/issues/12) Optimise the
  crawler with sitemaps.
- [#9](https://github.com/Jakespitteler/inwebstigator/issues/9) Text hashing.

[Design choices](#design-choices) explains why. What's left to do is in
[Known issues](#known-issues) and [Ideas for what's next](#ideas-for-whats-next).

---

## Known issues

Bugs and rough edges found while writing this. Consider opening an issue for each.

| Issue | Details |
| --- | --- |
| `DB_NAME` is ignored | `db_path` is worked out from `db_name` when `Config` is defined, before `.env` is read, so setting `DB_NAME` still uses `inwebstigator.db`. `USER_DATA_DIR` has the same problem. Use `DB_PATH`. The fix is to make `db_path` a property or a validator. |
| Websites on different subdomains get the same name | `news.uwa.edu.au` and `uwa.edu.au` can both be shown as "Uwa" if neither page's title gives a better one. |
| A website paused for being blocked just says "Inactive" | `deactivated_reason` is only ever set to `too_large`. A website deactivated after being blocked 3 times gets no reason, so the dashboard can't explain it. |
| The launcher's loading and error text are out of date | `inwebstigator.py` says overdue scans run "before the dashboard opens" (they run in the background now) and its error page says to "run desktop.py again" (now `inwebstigator.py`). |
| Logs only go to the console window | Nothing is written to a log file, so a client can only send a screenshot of the console. Closing that console window also quits the app. |
| A misnamed setting | `web_crawler_batch_402_threshold_seconds` (50) is really the number of 403 responses in one crawl batch that counts as being blocked. |
| Scans only run while the app is open | There's no Windows service or start-up entry, so after a restart the user has to open it again. |

`TODO` comments left in the code: `core/config.py` (similarity threshold),
`backend/utils/links.py` (whether to ignore `<nav>` links),
`backend/utils/html_parser.py` and `frontend/api/routers.py` (minor).

---

## Design choices

Why it's built the way it is, and what each choice costs.

| Choice | Why | Cost |
| --- | --- | --- |
| A desktop app on the client's computer, not a hosted website | No server to pay for or look after, no logins, and the client's data stays on their computer. | Scans only happen while the app is open and the computer is on. Only one person can use it. |
| SQLite, with upgrades written by hand | One file, no database server. `add_missing_columns()` covers most changes. | Anything beyond adding columns needs an upgrade step in `db/migrations.py`, written and tested by hand. |
| Server-rendered pages with inline JavaScript | No build step or JavaScript framework to learn. | `index.html` is long (about 2,500 lines), as it holds the dashboard's HTML and most of its JavaScript. |
| One scan at a time | Polite to the websites, and simpler (scans never compete for the database). | **Run All Scans** on many large websites takes a long time. |
| The first scan only saves a baseline | Users want to hear about changes, not everything already on a page. | A change made between adding a website and its first scan is never reported. |
| Comparing text block by block | Shows what actually changed, in context, rather than just "this page changed" (which hashing, #9, would give). | Pages whose layout changes but whose wording doesn't can still show as changed. The 60% similarity threshold is a judgement call. |
| Crawling links rather than sitemaps (#12) | Works on any website, even one with no sitemap or an out-of-date one. | Slower, and pages nothing links to aren't found. |
| Checking an address with a real email | Mail servers accept any address at first and bounce it later, so this is the only reliable check. | Adding an address takes up to 30 seconds, and needs IMAP to spot bounces. |
| Data kept outside the app's folder | A new version can be unzipped over an old one without losing anything. | Uninstalling means deleting `%LOCALAPPDATA%\inwebstigator` by hand. |
| A PyInstaller folder build rather than a single `.exe` | Starts faster, as nothing has to be unpacked each launch. | It's a folder to zip and unzip rather than one file. |

---

## Ideas for what's next

Roughly in order of how much they'd help the client:

1. **Fix the known issues above.** Most are small.
2. **Start automatically with Windows.** A "start Inwebstigator when I log in"
   option would stop scans silently stopping after a restart.
3. **Write logs to a file** in the data folder, and add a way to open it from
   the tray, so the client can send them.
4. **Automate releases:** a GitHub Actions workflow on Windows that runs the
   tests and builds the zip (without `.env`, which would be added by hand).
5. **Sign the `.exe`** so Windows stops warning about it.

---

## The original team

Ahmed Jashim, Jake Spitteler, Made Aria Ravindrajaya, Nanasa Itami,
Patrick Caputi and Pooja Renjith Nair, for CITS3200 at the University of
Western Australia in 2026.
