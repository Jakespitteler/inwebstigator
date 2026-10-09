# Handover

For a team taking Inwebstigator over in a repository of its own. It covers where
the project is, how to start your own copy, what you need to set up, how to
release it, what's unfinished, and why it's built the way it is.

Written on 9 October 2026, at the end of the original team's CITS3200 project.

Issue and pull request numbers (like #89) are from the
[original repository](https://github.com/Jakespitteler/inwebstigator), which
stays online as a record. They don't come across to your copy.

**Contents:** [Where the project is](#where-the-project-is) ·
[Starting your own repository](#starting-your-own-repository) ·
[Your first day](#your-first-day) · [Accounts and access](#accounts-and-access) ·
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
- **It's tested by:** unit tests for each module, plus browser tests of the
  dashboard (see [Unfinished work](#unfinished-work) if they aren't in `main`
  yet).
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

Only `main` is pushed. To bring over a branch that wasn't merged (see
[Unfinished work](#unfinished-work)):

```bash
git push origin original/<branch>:refs/heads/<branch>
```

Then:

1. Change the clone address in the README's [Setting up](../README.md#setting-up)
   to your repository.
2. Open an issue in your repository for each item in
   [Work to carry over](#work-to-carry-over) and [Known issues](#known-issues)
   you plan to do.
3. Set up your own sending email account (see [Accounts and access](#accounts-and-access)).

A GitHub fork would also work, but its pull requests default to the original
repository and its issues start switched off, so a plain copy is simpler.

The repository has no licence file. Before making your copy public, check with
the unit coordinator and the client who owns the code.

---

## Your first day

1. Start your own repository as above, or get added to the one your team made.
2. Follow the README's [Setting up](../README.md#setting-up) and
   [Running](../README.md#running), with `AUTOMATIC_SCANS=false` and a `DB_PATH`
   of your own so you don't scan real websites by accident.
3. Run `uv run pytest -m "not e2e"`. Everything should pass.
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
3. Try the zip on a Windows computer that has never run Inwebstigator:
   add a website, run a scan, and check the emails arrive.
4. Send the zip with [INSTALL.md](../INSTALL.md) (or a PDF of it).
   The client quits the old version from the tray and unzips the new one over
   it. Their data is kept, and the database is upgraded on startup (see the
   README's [Database](../README.md#database)).

The `.exe` isn't code-signed, so Windows SmartScreen warns about it the first
time. INSTALL.md tells users how to get past this.

---

## Unfinished work

### Branches not yet in `main`

If any of these are still open on the original repository when you start,
bring the branch over as shown in
[Starting your own repository](#starting-your-own-repository).

| Branch | What it is | What's needed |
| --- | --- | --- |
| `about-page-footer` ([#105](https://github.com/Jakespitteler/inwebstigator/pull/105)) | The About page, footer, Refresh button and home link. INSTALL.md already describes these. | Ready to merge. |
| `dashboard-browser-tests` | The browser tests in `tests/e2e/`. | Ready to merge. One more set of tests (the About page, footer and Refresh) is planned for after `about-page-footer`. |
| `jake-bug-fixes` ([#99](https://github.com/Jakespitteler/inwebstigator/pull/99)) | A large restructure: it moves most of `app/` into new packages, splits the CSS and JavaScript into files, and adds `changes` and `scan_runs` tables (the start of scan history, #71). | Has merge conflicts. Merging it changes most file paths in ARCHITECTURE.md and the README, so they'll need updating. It also changes the database, so test it against a database made by the current version. |

The original repository's other branches (`demo`, `diff_check`,
`weekly-changes-dashboard`, `windows-executable` and older ones) are from
earlier in the project and out of date. There's no need to bring them over.

### Work to carry over

Open issues from the original repository that still need doing:

| Original issue | What's left |
| --- | --- |
| [#89](https://github.com/Jakespitteler/inwebstigator/issues/89) A trailing slash makes a duplicate critical page | **Still a bug.** See [Known issues](#known-issues). |
| [#82](https://github.com/Jakespitteler/inwebstigator/issues/82) Two emails are sent when a website is added | The confirmation, then "Website monitoring started". Decide with Jay Jay whether to merge them or drop one. |
| [#83](https://github.com/Jakespitteler/inwebstigator/issues/83) Cancel button for Run All Scans | Each website's scan can be cancelled, but not the whole run. |
| [#71](https://github.com/Jakespitteler/inwebstigator/issues/71) Keep the last 7 scans | Started in `jake-bug-fixes`. Today only each page's latest change is kept. |
| [#98](https://github.com/Jakespitteler/inwebstigator/issues/98) Test that the scheduler is working | `tests/unit/test_scheduler.py` covers the logic. Still worth leaving the app running for a few days to watch scheduled scans and health checks happen for real. |
| [#21](https://github.com/Jakespitteler/inwebstigator/issues/21) How many requests TEQSA allows | The crawler already slows down when blocked, so this is about choosing better default speeds. |

[#12](https://github.com/Jakespitteler/inwebstigator/issues/12) (sitemaps) and
[#9](https://github.com/Jakespitteler/inwebstigator/issues/9) (text hashing)
were marked out of scope. See [Design choices](#design-choices).

---

## Known issues

Bugs and rough edges found while writing this. Consider opening an issue for each.

| Issue | Details |
| --- | --- |
| `DB_NAME` is ignored | `db_path` is worked out from `db_name` when `Config` is defined, before `.env` is read, so setting `DB_NAME` still uses `inwebstigator.db`. `USER_DATA_DIR` has the same problem. Use `DB_PATH`. The fix is to make `db_path` a property or a validator. |
| A trailing `/` makes a different critical page (#89) | `resolve_critical_page_url()` doesn't normalise the address, so `/news` and `/news/` are saved and scanned as two pages. Websites themselves are already compared with `same_page_key()`. Critical pages need the same. |
| Websites on different subdomains get the same name | `news.uwa.edu.au` and `uwa.edu.au` can both be shown as "Uwa" if neither page's title gives a better one. |
| A website paused for being blocked just says "Inactive" | `deactivated_reason` is only ever set to `too_large`. A website deactivated after being blocked 3 times gets no reason, so the dashboard can't explain it. |
| The launcher's loading and error text are out of date | `inwebstigator.py` says overdue scans run "before the dashboard opens" (they run in the background now) and its error page says to "run desktop.py again" (now `inwebstigator.py`). |
| Logs only go to the console window | Nothing is written to a log file, so a client can only send a screenshot of the console. Closing that console window also quits the app. |
| A misnamed setting | `web_crawler_batch_402_threshold_seconds` (50) is really the number of 403 responses in one crawl batch that counts as being blocked. |
| Scans only run while the app is open | There's no Windows service or start-up entry, so after a restart the user has to open it again. |
| An earlier change can be replaced before anyone sees it | If a page changes in two scans in a row, the dashboard shows only the second. The first was still emailed. Scan history (#71) would fix this. |

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
| Server-rendered pages with inline JavaScript | No build step or JavaScript framework to learn. | `index.html` is long. `jake-bug-fixes` splits it into files. |
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

1. **Bring over the unfinished branches:** `about-page-footer`, the browser
   tests, and `jake-bug-fixes` (with its conflicts fixed), then update the docs
   for its new file layout.
2. **Fix the known issues above.** Most are small.
3. **Start automatically with Windows.** A "start Inwebstigator when I log in"
   option would stop scans silently stopping after a restart.
4. **Write logs to a file** in the data folder, and add a way to open it from
   the tray, so the client can send them.
5. **Scan history (#71).** Keep past changes rather than only the latest.
6. **Automate releases:** a GitHub Actions workflow on Windows that runs the
   tests and builds the zip (without `.env`, which would be added by hand).
7. **Sign the `.exe`** so Windows stops warning about it.

---

## The original team

Ahmed Jashim, Jake Spitteler, Made Aria Ravindrajaya, Nanasa Itami,
Patrick Caputi and Pooja Renjith Nair, for CITS3200 at the University of
Western Australia in 2026.
