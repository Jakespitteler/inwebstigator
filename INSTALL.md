# Inwebstigator: User Guide

Inwebstigator watches websites for you. It checks the websites you choose on a
schedule, shows you what changed, and emails you about it, so you don't have to keep checking them yourself.

This is a test version. If anything is confusing or doesn't work, please let
the team know.

**Contents**

- [Inwebstigator: User Guide](#inwebstigator-user-guide)
  - [Getting started](#getting-started)
    - [Open Inwebstigator](#open-inwebstigator)
    - [Around the dashboard](#around-the-dashboard)
  - [Adding a website](#adding-a-website)
    - [If a website can't be added](#if-a-website-cant-be-added)
  - [The Websites tab](#the-websites-tab)
    - [Scanning a website now](#scanning-a-website-now)
    - [Critical pages](#critical-pages)
    - [Notification emails](#notification-emails)
    - [Scan settings](#scan-settings)
    - [Deleting a website](#deleting-a-website)
  - [The Updates tab](#the-updates-tab)
  - [Emails you'll receive](#emails-youll-receive)
  - [Keeping Inwebstigator running](#keeping-inwebstigator-running)
  - [If something goes wrong](#if-something-goes-wrong)
  - [Good to know](#good-to-know)

---

## Getting started

### Open Inwebstigator

1. Extract the **Inwebstigator** zip file the team sent you.
2. Open the extracted folder and double-click **Inwebstigator.exe**.
3. Windows may warn that it **"protected your PC"**, because the program is
   new. Click **More info**, then **Run anyway**.

A small "Starting Inwebstigator" window appears, then the dashboard opens in
its own window. There's no account or password to set up.

If Inwebstigator is already open, opening it again shows a message saying so.
Look for its icon in the **system tray**, at the bottom right of the screen
(you may need to click the **^** arrow to see it).

### Around the dashboard

| Part | What it's for |
| --- | --- |
| **Websites** tab | The websites you're monitoring, and adding new ones. |
| **Updates** tab | What changed since each website's last scan. |
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
2. **Notification emails.** Enter who should be emailed about this website.
   This is optional, as changes always show on the dashboard. Addresses you've
   used before are suggested as you type. Use **+ Add another email** for more.
3. **Scan settings.** These are already set to sensible values, so you can
   usually click **Next**. See [Scan settings](#scan-settings) below.
4. **Review and add.** Check the details, then click **Add Website**.

Inwebstigator then runs the website's first scan, showing **Scanning…**. This
can take several minutes for a large website. You can close the window and the
scan keeps running in the background, or click **Cancel Scan** to stop it,
in which case the website isn't added.

The first scan saves each page as a starting point. You'll be told about
changes from the next scan onwards, not about everything already on the pages.

You can move between the steps with **Next**, **Back**, or the numbered dots.
Pressing **Enter** in a box moves to the next step.

### If a website can't be added

| Message | What to do |
| --- | --- |
| "*address* could not be loaded" | Check the address is right, and that the page opens in your web browser. |
| "*website* is already being monitored" | It's already on your list, perhaps written slightly differently (e.g. with or without `www.`). |
| "We can't send an email to *address*" | The address couldn't receive Inwebstigator's confirmation email. Check it for typos. |

Each new notification email gets a short confirmation email first, which can
take up to a minute.

---

## The Websites tab

Each website has its own card. Its header shows the website's name and when it
was last scanned. Click the card (or the arrow at its right) to open it.

### Scanning a website now

- **Run scan** checks that website straight away instead of waiting for its
  next scheduled scan. The dashboard refreshes when it's finished.
- **Run All Scans** (at the top of the list) checks every website straight
  away, and sends each person one email covering all the changes.
- **Cancel** stops a scan that's waiting or running. Nothing it found is saved.

Scans run one at a time. If you start several, they wait their turn
("Scan queued…").

### Critical pages

Opening a card lists its critical pages. The website's own address is marked
**Main website (automatic)** and is always watched.

- To add one, type its address in the box below the list and click **+**.
  It's checked straight away, so a page that doesn't exist isn't added.
- To remove one, click the **×** next to it, then **Delete**.

### Notification emails

- To add someone, type their email address below the list and click **+**.
  They're sent a confirmation email first.
- To stop someone getting emails about this website, click the **×** next to
  their address, then **Remove**.

### Scan settings

Click **Scan Settings** on an open card, change anything, then click **Save
Settings**.

| Setting | What it does |
| --- | --- |
| **Active** | Untick to stop scanning the whole website for new and removed pages. Its critical pages are still checked. |
| **Days between scans** | How often the website is checked. The default is 1 (daily). The shortest is half a day. |
| **Request delay** / **Concurrent requests** | How fast Inwebstigator reads the website. Best left as they are unless the team suggests a change. |

An inactive website's card says **Inactive**, and its scan button says **Scan
critical pages**. A website with more than 50,000 pages is made inactive
automatically and its card says it's **too large to scan**. Its critical pages
are still checked.

### Deleting a website

Click the bin icon on an open card. To make sure it isn't deleted by mistake,
you need to type **CONFIRM** before the **Delete** button works. Deleting a
website also deletes everything saved about it.

---

## The Updates tab

This shows what changed on each website since its last scan, with the most
recent changes first. If nothing changed, it says "No changes were detected in
the latest scan".

Each website with changes has a card showing when it changed and a summary,
e.g. "2 internal links · 1 critical page changed". Open it to see:

- **Internal links:** pages added to or removed from the rest of the website.
- **Critical pages:** a card for each critical page that changed. Open it for
  the details:

| What you'll see | What it means |
| --- | --- |
| **Content changed**, with *Before* and *After* side by side | Wording was edited. Words taken out are highlighted on the left and words put in on the right. |
| **Section changed** | Some writing moved under a different heading, or a heading was renamed. |
| **Text**, marked **Added** or **Removed** | Writing appeared on, or was taken off, the page. |
| **Links**, marked Added or Removed | A link on the page was added or taken away. |
| **Documents**, marked Added or Removed | A document such as a PDF was added or taken away. A replaced document shows as the old one removed and the new one added. |

Click any link or document to open it. A page's changes stay here until it
changes again. **Next scheduled check** (at the top) shows when Inwebstigator
next looks for websites that are due a scan.

---

## Emails you'll receive

Emails come from the Inwebstigator email account, not from a person.

| Subject | When it's sent |
| --- | --- |
| **Email address added to website monitoring** | When an address is added to a website, to confirm it can receive emails. |
| **Website monitoring started** | When a website you're emailed about has been added and its first scan is done. |
| **Website Update** | A scheduled scan or **Run All Scans** found changes, or a problem (e.g. a website couldn't be reached). You get one email covering all your websites. |
| **Manual Website Scan** | **Run scan** on a single website found changes. |
| **Health Check** | Nothing has changed for 7 days. It confirms Inwebstigator is still working. |

If emails don't arrive, check your **Spam/Junk** folder and mark them as "Not
spam".

---

## Keeping Inwebstigator running

**Scheduled scans only happen while Inwebstigator is running and the computer
is on.** Inwebstigator checks which websites are due a scan when it opens and
then every 12 hours. If it was closed for a while, it catches up next time it
opens.

- **Closing the window** (the **X**) keeps Inwebstigator running in the
  background, in the system tray. Scans keep happening.
- **To show it again,** click its icon in the system tray, or right-click it
  and choose **Open**.
- **To close it completely,** right-click the tray icon and choose **Quit**.
  Scans then stop until you open it again.
- **After restarting the computer,** open **Inwebstigator.exe** again.

When a website seems to be struggling (e.g. it's slow, busy or blocking too
many requests), Inwebstigator gives it a rest before trying again, and reads it
more slowly next time. This is automatic.

---

## If something goes wrong

| Problem | What to do |
| --- | --- |
| Inwebstigator shows an error or closes when opened | Contact the team. It's most likely an email setting on our side. |
| "Inwebstigator is already running" | It's open in the system tray. Click the tray icon to show it. |
| A scan says "Unable to complete scan" | Try again later. If it keeps happening, contact the team. |
| The dashboard looks out of date | Click **Refresh** at the top right. |
| A website's card says **Inactive** and you didn't change it | If it says **too large to scan**, that's expected. Otherwise the website kept blocking Inwebstigator, so it was paused. Contact the team. |
| No emails for over a week | Check Spam/Junk, make sure Inwebstigator is running (see the system tray), and that the website has your email on its card. |
| An email address won't add | Check it for typos. Inwebstigator only adds addresses that can receive its confirmation email. |

For anything else, send the team a screenshot of what you're seeing.

---

## Good to know

- Everything Inwebstigator saves (your websites and what it found) is kept on
  this computer, in `%LOCALAPPDATA%\inwebstigator`.
- Inwebstigator only reads public web pages. It doesn't log in to websites.
- Changes always show on the dashboard, even for websites with no notification
  emails.
