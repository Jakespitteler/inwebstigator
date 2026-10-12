# Inwebstigator: User Guide

Inwebstigator watches websites for you. It checks the websites you choose on a
schedule (daily by default), shows you what changed, and emails you about it,
so you don't have to keep checking them yourself.

This is a test version. If anything is confusing or doesn't work, please let
the team know.

**Contents**

- [Inwebstigator: User Guide](#inwebstigator-user-guide)
  - [Getting started](#getting-started)
    - [Install Inwebstigator](#install-inwebstigator)
    - [Around the dashboard](#around-the-dashboard)
  - [Adding a website](#adding-a-website)
    - [If a website can't be added](#if-a-website-cant-be-added)
  - [The Websites tab](#the-websites-tab)
    - [Scanning a website now](#scanning-a-website-now)
    - [Critical pages](#critical-pages)
    - [Notification emails](#notification-emails)
    - [Warnings on a card](#warnings-on-a-card)
    - [Scan settings](#scan-settings)
    - [Deleting a website](#deleting-a-website)
  - [The Updates tab](#the-updates-tab)
  - [Emails you'll receive](#emails-youll-receive)
  - [Keeping Inwebstigator running](#keeping-inwebstigator-running)
  - [If something goes wrong](#if-something-goes-wrong)
  - [Good to know](#good-to-know)

---

## Getting started

### Install Inwebstigator

1. Double-click the **Inwebstigator Installer** the team sent you.
2. Windows may warn that it **"protected your PC"**, because the program is
   new. Click **More info**, then **Run anyway**.
3. Follow the installer's steps. You don't need administrator access.
   - Leave **Start Inwebstigator when I sign in to Windows** ticked, so scans
     carry on after the computer restarts.
   - Tick **Create a desktop shortcut** if you want one.
4. On the last page, leave **Launch Inwebstigator** ticked and click
   **Finish**.

A small "Starting Inwebstigator" window appears, then the dashboard opens in
its own window. There's no account or password to set up. You can open it
again later from the Start menu.

If Inwebstigator is already open, opening it again shows a message saying so.
Look for its icon in the **system tray**, at the bottom right of the screen
(you may need to click the **^** arrow to see it).

To install a newer version, run its installer the same way. If Inwebstigator
is running, the installer asks for it to be closed first. Your websites and
their history are kept.

### Around the dashboard

| Part | What it's for |
| --- | --- |
| **Websites** tab | The websites you're monitoring, and adding new ones. |
| **Updates** tab | What each website's last 7 scans found. |
| **Refresh** (top right) | Reloads the dashboard to show the latest results, e.g. after a scan finished in the background. |
| **Dark mode / Light mode** (top right) | Switches between a light and a dark look. Your choice is remembered. |
| **Inwebstigator** (top left) | Takes you back to the dashboard from any page. |
| **About** (bottom of the page) | What Inwebstigator does, the team, and copyright information. |

---

## Adding a website

The first time you open Inwebstigator, the **Add a website** steps are already
open. Once you have a website, click **+ Add website** on the Websites tab to
open them again.

1. **Website.** Enter the website's address, e.g. `www.example.edu.au` (you
   don't need to type `https://`). Its main page is always watched.

   Under **Critical Pages**, add any other pages you want watched closely for
   wording, link and document changes. You can type a full address or just
   the part after the website, e.g. `/fees`. Use **+ Add another critical
   page** for more.
2. **Emails.** Enter who should be emailed about this website. This is
   optional, as changes always show on the dashboard. Addresses you've used
   before are suggested as you type. Use **+ Add another email** for more.
3. **Scan settings.** These are already set to sensible values, so you can
   usually click **Next**. See [Scan settings](#scan-settings) below.
4. **Review.** Check the details, then click **Add Website**.

Inwebstigator checks the website and its critical pages can be loaded, and
sends each email address a **Website monitoring started** email. It then runs
the website's first scan, showing **Scanning…**. This can take several minutes
for a large website. You can add another website while it runs, close the
window and let the scan carry on in the background, or click **Cancel Scan**
to stop it, in which case the website isn't added.

The first scan saves each page as a starting point. You'll be told about
changes from the next scan onwards, not about everything already on the pages.

You can move between the steps with **Next**, **Back**, or the numbered dots.
Pressing **Enter** in a box moves to the next step.

### If a website can't be added

The message says what went wrong. Click **Edit and Try Again** to fix it.

| Message | What to do |
| --- | --- |
| "*address* could not be loaded" | Check the address is right, and that the page opens in your web browser. |
| "*website* is already being monitored" | It's already on your list, perhaps written slightly differently (e.g. with or without `www.`). |
| "We can't send an email to *address*" | The address couldn't receive Inwebstigator's email. Check it for typos. |

Emailing a new address can take up to a minute, as Inwebstigator waits to
check the email wasn't returned.

---

## The Websites tab

Each website has its own card. Its header shows the website's name and when it
was last scanned. Click the card (or the arrow at its right) to open it. An
open card also shows how many pages Inwebstigator found on the whole website
(**Internal pages**).

### Scanning a website now

- **Run scan** checks that website straight away instead of waiting for its
  next scheduled scan. The dashboard refreshes when it's finished.
- **Run All Scans** (at the top of the list) checks every website straight
  away, and sends each person one email covering all the changes.
- **Cancel** stops a scan that's waiting or running. Nothing it found is saved.
- While **Run All Scans** is running, a **Cancel** button appears next to it.
  It stops the website being scanned and skips the rest. Websites already
  scanned keep their results, and their changes are still emailed.

Scans run one at a time. If you start several, they wait their turn
("Scan queued or running…").

While a scan runs, a note reminds you that closing the window won't stop it.
The **×** hides it until the next scan, and **Don't show again** hides it for
good.

### Critical pages

Opening a card lists its critical pages. The website's own address is marked
**Main website (automatic)**. It's always watched and can't be deleted.

- To add one, type its address in the box below the list and click **+**.
  It's checked straight away, so a page that doesn't exist, or one that's
  already listed, isn't added.
- To remove one, click the **×** next to it, then **Delete**.

### Notification emails

- To add someone, type their email address below the list and click **+**.
  They're sent an **Email address added to website monitoring** email.
- To stop someone getting emails about this website, click the **×** next to
  their address, then **Remove**.

A card with no notification emails says so, as nobody will be emailed about
that website's changes.

### Warnings on a card

A card also warns you when Inwebstigator can't watch a website properly:

| Card says | What it means |
| --- | --- |
| **Last scan:** *a problem*, e.g. **Could not connect** | The website's last scan didn't finish. The warning goes once a scan works again. See [If something goes wrong](#if-something-goes-wrong). |
| **Little text to watch** | Inwebstigator can read almost no text on the pages it names. They're probably built with JavaScript, which Inwebstigator can't run (e.g. youtube.com), so changes to them may be missed. |

### Scan settings

Click **Scan Settings** on an open card, change anything, then click **Save
Settings**. Changes apply from the next scan.

| Setting | What it does |
| --- | --- |
| **Crawler Active** | Untick to stop scanning the whole website for new and removed pages. Its critical pages are still checked. |
| **Days between scans** | How often the website is checked. The default is 1 (daily). The shortest is half a day. |
| **Request delay** / **Concurrent requests** | How fast Inwebstigator reads the website. Best left as they are unless the team suggests a change. If a website asks Inwebstigator to slow down, these are changed automatically. |

An inactive website's card says why, and its scan button says **Scan critical
pages**:

| Card says | Why |
| --- | --- |
| **Inactive** | **Crawler Active** was unticked. |
| **Too large to scan** | The website has more than 50,000 pages. |
| **Blocked the crawler** | The website kept blocking Inwebstigator, even at the slowest speed. |

Its critical pages are still checked either way.

### Deleting a website

Click **Delete website** on an open card. To make sure it isn't deleted by
mistake, you need to type **CONFIRM** before the **Delete** button works.
Deleting a website also deletes everything saved about it, including its
history.

---

## The Updates tab

This shows the last 7 scans of each website, with the most recent changes
first. **Next scheduled check** (at the top) shows when Inwebstigator next
looks for websites that are due a scan.

Each website has a card summarising its scans. Open it to see each scan, then
open a scan to see what it found:

- **No changes:** the scan found nothing new.
- **Email not sent yet:** the scan's email couldn't be sent at the time. It's
  sent with the next scan.
- **A problem**, such as **Could not connect**, **Rate limited** or **Most
  pages missing** (see [If something goes wrong](#if-something-goes-wrong)).
- **Internal links:** pages added to or removed from the rest of the website.
- **Critical pages:** a card for each critical page that changed. Open it for
  the details:

| What you'll see | What it means |
| --- | --- |
| **Content changed**, with *Before* and *After* side by side | Wording was edited. Words taken out are highlighted on the left and words put in on the right. |
| **Section changed** | Some writing moved under a different heading, or a heading was renamed. |
| **Text**, marked **Added** or **Removed** | Writing appeared on, or was taken off, the page. |
| **Links**, marked Added or Removed | A link on the page was added or taken away. Links in the website's menus aren't included, as they're the same on every page. |
| **Documents**, marked Added or Removed | A document such as a PDF was added or taken away. A replaced document shows as the old one removed and the new one added. |
| **Unreachable** | The page couldn't be loaded on two scans in a row, or it no longer exists. |

Click any link or document to open it.

---

## Emails you'll receive

Emails come from the Inwebstigator email account, not from a person. Their
subjects name the websites they're about.

| Subject starts with | When it's sent |
| --- | --- |
| **Website monitoring started** | A website you're emailed about was added. |
| **Email address added to website monitoring** | Your address was added to a website that was already being watched. |
| **Website update** | A scheduled scan or **Run All Scans** found changes, or a problem (e.g. a website couldn't be reached). You get one email covering all your websites. A long list of new or removed pages is cut short; the Updates tab lists them all. |
| **Manual scan** | **Run scan** on a single website found changes or a problem. |
| **Health check** | You haven't had an email for 7 days. It confirms Inwebstigator is still running, and says if any of your websites need attention. |

If an email can't be sent at the time (e.g. the internet is down), it's sent
with the next scan instead.

If emails don't arrive, check your **Spam/Junk** folder and mark them as "Not
spam".

---

## Keeping Inwebstigator running

**Scans only happen while Inwebstigator is running and the computer is on.**
Inwebstigator checks which websites are due a scan at 8am and 8pm, and just
after it opens. If it was closed for a while, it catches up when it next
opens.

- **Closing the window** (the **X**) keeps Inwebstigator running in the
  background, in the system tray. Scans keep happening.
- **To show it again,** click its icon in the system tray, or right-click it
  and choose **Open**.
- **To close it completely,** right-click the tray icon and choose **Quit**.
  Scans then stop until you open it again.
- **After restarting the computer,** there's nothing to do. Inwebstigator
  starts by itself when you sign in to Windows. (If you unticked that box when
  installing, open it from the Start menu instead.)

When a website seems to be struggling (e.g. it's slow, busy or blocking too
many requests), Inwebstigator gives it a rest before trying again, and reads it
more slowly next time. This is automatic.

---

## If something goes wrong

Inwebstigator keeps a log of what it does, and any errors, in
`%LOCALAPPDATA%\inwebstigator\logs\inwebstigator.log`. To open it, press
**Windows key + R**, paste that address and press **Enter**. The team may ask
you to send it to them.

| Problem | What to do |
| --- | --- |
| Inwebstigator shows an error or closes when opened | Contact the team and send them the log file. It's most likely an email setting on our side. |
| "Inwebstigator is already running" | It's open in the system tray. Click the tray icon to show it. |
| A scan says **Could not connect** or **Rate limited** | The website was down, or asked Inwebstigator to slow down. It's tried again later automatically. |
| A scan says **Most pages missing** | Most of the website's pages couldn't be found, which is usually a problem with the website rather than real changes. If it happens 3 scans in a row, the pages are treated as really removed. |
| The dashboard looks out of date | Click **Refresh** at the top right. |
| A website's card says **Blocked the crawler** | The website kept blocking Inwebstigator, so it was paused. Contact the team. |
| A website's card says **Little text to watch** | The website shows its content with JavaScript, which Inwebstigator can't read. If you can, watch a different page or website with the same information. |
| No emails for over a week | Check Spam/Junk, make sure Inwebstigator is running (see the system tray), and that the website has your email on its card. |
| An email address won't add | Check it for typos. Inwebstigator only adds addresses that can receive its email. |

For anything else, send the team a screenshot of what you're seeing and the
log file.

---

## Good to know

- Everything Inwebstigator saves (your websites, their history and the log) is
  kept on this computer, in `%LOCALAPPDATA%\inwebstigator`.
- Uninstalling Inwebstigator also deletes everything it saved.
- Inwebstigator only reads public web pages. It doesn't log in to websites.
- Changes always show on the dashboard, even for websites with no notification
  emails.
- Websites are scanned one at a time, so when several are due it can take a
  while for all of them to finish.
