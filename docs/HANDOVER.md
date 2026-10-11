# Handover

For a team taking Inwebstigator over in a repository of its own. It covers where
the project is, how to start your own copy, what you need to set up, how to
release it, what's unfinished, and why it's built the way it is.

Written on 11 October 2026, at the end of the original team's CITS3200 project.

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
  removed pages; scan on a schedule; keep each website's last 7 scans; email
  each recipient one report, and resend reports that couldn't be sent; slow
  down for or pause websites that struggle; and run as a Windows tray app,
  installed with an installer that starts it at sign-in.
- **It's tested by:** unit tests for each module, browser tests of the
  dashboard, GitHub Actions workflows that check every push and the Windows
  build, and a [checklist](#release-checklist) for checking the installed app
  by hand before each release.
- **It's documented in:** [INSTALL.md](../INSTALL.md) (users),
  the [README](../README.md) (developers) and
  [ARCHITECTURE.md](ARCHITECTURE.md) (how it works).

The client contact is **Jay Jay** (written "JJ" in some old issues and notes).
Most of the dashboard's behaviour (collapsible cards, safer deletion,
per-website scan times, the add-website steps, scan history) came from Jay
Jay's feedback on earlier versions.

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
branches. The GitHub Actions workflows in `.github/workflows/` come with it and
start running in your repository straight away.

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
| `.env` | Your machine, and inside each installer | Never committed. `.env.example` lists the email settings; see the README's [Configuration](../README.md#configuration) for the rest. |
| The client's data | `%LOCALAPPDATA%\inwebstigator` on the client's computer | Their websites, scan history and logs. Nothing is stored anywhere else. Installing your version over theirs keeps it; uninstalling deletes it. |

**Every installer contains the email password.** The `.env` it installs next
to `inwebstigator.exe` is plain text, so anyone with the installer can send
email from that account. Use an account made just for Inwebstigator, and
change its App Password if an installer goes somewhere it shouldn't.

**Your first build changes the sender.** The client's current copy sends from
the original team's account until they install yours. Tell Jay Jay the new
address beforehand, so recipients can mark it as "Not spam".

---

## Releasing a new version

The **Build Windows app** workflow builds the installer on GitHub whenever the
build files change, but only to check the build works: it uses `.env.example`,
so its installer can't send email. A release is built by hand:

1. Merge everything into `main`, and check the **Checks** workflow passed.
2. Change `MyAppVersion` in `inwebstigator_installer_script.iss`.
3. On a Windows computer, with a `.env` holding the real email settings,
   follow the README's
   [Building the Windows app](../README.md#building-the-windows-app). This
   makes `dist\Inwebstigator Installer.exe`.
4. Go through the [release checklist](#release-checklist).
5. Send the installer with [INSTALL.md](../INSTALL.md) (or a PDF of it). The
   client runs it over the old version. It asks for the app to be quit if it's
   running, their data is kept, and the database is upgraded on startup (see
   the README's [Database](../README.md#database)).

The installer and `.exe` aren't code-signed, so Windows SmartScreen warns about
them the first time. INSTALL.md tells users how to get past this.

### Release checklist

The automated tests run the dashboard in a browser, which is the same engine
as the desktop window (Microsoft Edge's WebView2). They don't cover the window,
the tray or the installer, so check these by hand with the installer you're
about to send, on a Windows computer that has never run Inwebstigator:

- [ ] Run the installer. It needs no administrator access, and "Start
      Inwebstigator when I sign in to Windows" is ticked.
- [ ] Inwebstigator opens when the installer finishes: "Starting
      Inwebstigator" shows, then the dashboard, with its logo.
- [ ] Open it again from the Start menu. A message says it's already running.
- [ ] Add a small website with your own email. One "Website monitoring
      started" email arrives.
- [ ] **Run scan** on it finishes and the dashboard reloads.
- [ ] **Run All Scans** starts, and its **Cancel** stops it.
- [ ] The **About** page opens, and the app name leads back to the dashboard.
- [ ] Close the window with the **X**. The tray icon stays, and **Open** brings
      the window back.
- [ ] Switch to dark mode, choose **Quit** from the tray, and reopen the app.
      It's still dark, and the website is still there.
- [ ] Sign out of Windows and back in. Inwebstigator starts by itself.
- [ ] Run the installer again while the app is open. It asks for the app to be
      closed, and the website is still there afterwards.
- [ ] `%LOCALAPPDATA%\inwebstigator\logs\inwebstigator.log` shows no errors
      (tracebacks).

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

Bugs and rough edges not yet in an issue. Consider opening one for each.

| Issue | Details |
| --- | --- |
| `DB_NAME` is ignored | `db_path` is worked out from `db_name` when `Config` is defined, before the environment is read, so setting `DB_NAME` still uses `inwebstigator.db`. `USER_DATA_DIR` has the same problem. Use `DB_PATH`. The fix is to make `db_path` a property or a validator. |
| Run scan and adding a website keep the request open | The request waits until the scan finishes, which can take several minutes for a large website. They could start a background job and let the dashboard check on it instead. |
| `robots.txt` isn't read | The crawler visits every internal page it finds, including ones a website asks crawlers to avoid. |
| Ignore rules have no dashboard control | A critical page's `ignore_rules` (text to leave out, e.g. a visitor counter) can only be set through the API (`PATCH /critical_pages/{id}`). |
| No migration tool | `db/migrations.py` adds missing tables, columns and indexes, and runs hand-written one-off updates. Renaming or changing a column needs a one-off update written by hand. Alembic would handle this. |

---

## Design choices

Why it's built the way it is, and what each choice costs.

| Choice | Why | Cost |
| --- | --- | --- |
| A desktop app on the client's computer, not a hosted website | No server to pay for or look after, and the client's data stays on their computer. | Scans only happen while the computer is on and the app is running (it starts at sign-in). Only one person can use it. |
| No login, but a token on every change | Only the person at the computer uses it. The token stops other websites in their browser using the app (see [ARCHITECTURE.md](ARCHITECTURE.md#the-request-guard)). | Calling the API by hand needs `API_TOKEN_REQUIRED=false`. |
| SQLite, with upgrades written by hand | One file, no database server. `prepare_database()` covers most changes. | Anything beyond adding tables, columns and indexes needs a one-off update in `db/migrations.py`, written and tested by hand. |
| Server-rendered pages with plain JavaScript files | No build step or JavaScript framework to learn. | The scripts share one scope and their load order matters (see [ARCHITECTURE.md](ARCHITECTURE.md#the-dashboard)). |
| One scan at a time | Polite to the websites, and simpler (scans never compete for the database). | **Run All Scans** on many large websites takes a long time. |
| The first scan only saves a baseline | Users want to hear about changes, not everything already on a page. | A change made between adding a website and its first scan is never reported. |
| Keeping each scan's changes as rows | The Updates tab can show the last 7 scans, and a report whose email failed can be sent later. | The history is capped at 7 scans per website to stop the database growing. |
| Comparing text block by block | Shows what actually changed, in context, rather than just "this page changed" (which hashing, #9, would give). | Pages whose layout changes but whose wording doesn't can still show as changed. The 60% similarity threshold is a judgement call. |
| Crawling links rather than sitemaps (#12) | Works on any website, even one with no sitemap or an out-of-date one. | Slower, and pages nothing links to aren't found. |
| Not trusting a sudden drop | A page that loses most of its text, or a website that loses most of its pages, is usually down or showing a stand-in page, not really changed. | A real redesign that cuts most of a page or website is only reported after 3 scans in a row. |
| Checking an address with a real email | Mail servers accept any address at first and bounce it later, so this is the only reliable check. | Adding an address takes up to 30 seconds, and needs the account's inbox (IMAP) to spot bounces. |
| A PyInstaller folder build in an Inno Setup installer | Starts faster than a single-file `.exe`, and the installer adds Start menu and sign-in shortcuts without administrator access. | Only builds on Windows, and isn't code-signed. |
| Data kept outside the app's folder | Installing a new version keeps it. | Uninstalling deletes it, so a reinstall starts from nothing. |

---

## Ideas for what's next

Roughly in order of how much they'd help the client:

1. **Fix the known issues above.** Most are small.
2. **Background scan jobs,** so **Run scan** and adding a website return
   straight away and the dashboard shows progress.
3. **Read `robots.txt`,** so the crawler respects what websites ask of it.
4. **A dashboard control for ignore rules,** so the client can hide text that
   changes on every scan without help.
5. **Publish releases:** have the **Build Windows app** workflow attach a
   versioned installer to a GitHub release, with the real `.env` from a GitHub
   secret.
6. **Sign the installer and `.exe`** so Windows stops warning about them.

---

## The original team

Ahmed Jashim, Jake Spitteler, Made Aria Ravindrajaya, Nanasa Itami,
Patrick Caputi and Pooja Renjith Nair, for CITS3200 at the University of
Western Australia in 2026.
