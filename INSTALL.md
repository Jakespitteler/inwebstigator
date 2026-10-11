# Inwebstigator: Getting Started

Inwebstigator watches websites for you. It checks the websites you choose
regularly (daily by default) and emails you when something changes.

This is an early test version. If anything is confusing or doesn't work,
please let the team know.


---

## Setting up

### 1. Install and open Inwebstigator

1. Double-click the **Inwebstigator installer** the team sent you.
2. Windows may warn that it **"protected your PC"**, because the program is
   new. Click **More info**, then **Run anyway**.
3. Follow the installer's steps. You can tick **Create a desktop shortcut** if
   you want one.
4. On the last page, leave **Launch Inwebstigator** ticked and click
   **Finish**.

The Inwebstigator window opens after a moment. There is no account to create
or log in to. You can open it again later from the Start menu.

### 2. Add a website

The first time you open Inwebstigator, the **Add a website** form is already
showing. After that, click **+ Add website** at the top of the Websites tab.
The form has four steps:

1. **Website:** enter the website's address, e.g. `www.example.edu.au`. Its
   main page is always watched. Under **Critical Pages**, add any other pages
   you want watched in more detail (text changes, documents etc.).
2. **Emails:** add the email addresses that should get this website's reports.
3. **Scan settings:** how often the website is checked (**Days between scans**,
   1 means daily), and how fast it is read. These are already set to sensible
   values.
4. **Review and add:** check the details and click **Add Website**.

When you click **Add Website**, Inwebstigator checks the website and its
critical pages can be loaded, and sends each email address a **Website
monitoring started** email. If a page can't be loaded or an email can't be
delivered, the website isn't added and you're told which one to check.

### 3. The first scan

Adding a website scans it straight away. This can take several minutes for a
large website. You can add another website while it runs, and **Cancel Scan**
cancels adding the website.

**The first scan saves each page as a starting point.** It doesn't report
anything as changed, and after that you're only told about what actually
changes.

Setup is finished.

---

## Everyday use

**Keep Inwebstigator running.** Scans only happen while Inwebstigator is
running and the computer is on. Closing the window doesn't stop it: it keeps
running in the system tray (the icons by the clock, sometimes behind the
**^** arrow). Click the tray icon and choose **Open** to show the window
again, or **Quit** to stop Inwebstigator completely. If it was stopped,
it catches up on any missed scans shortly after you next open it.

**After restarting the computer,** there is nothing to do: Inwebstigator
starts by itself when you sign in to Windows, and catches up on any scans
missed while the computer was off. (If you unticked "Start Inwebstigator when
I sign in to Windows" when installing, open it from the Start menu instead.)

**Emails you'll receive** (they come from the Inwebstigator email account, not
from a person):

- **Website monitoring started:** a website was added and you'll get its
  reports.
- **Email address added to website monitoring:** your address was added to a
  website that was already being watched.
- **Website update:** something changed. The email shows what's changed. It's
  also sent if a website couldn't be scanned properly, e.g. it couldn't be
  reached. If the email can't be sent at the time, it is sent with the next
  scan. A long list of new or removed pages is cut short in the email; the
  **Updates** tab lists them all.
- **Manual scan:** the report from a **Run Scan Now**, sent if it found
  anything.
- **Health check:** sent about once a week, so you know Inwebstigator is still
  running. It lists your websites and says if any of them need attention.

The emails should go to your regular inbox but if they don't arrive, check
your **Spam/Junk** folder and mark them as "Not spam".

---

## The dashboard

The window has two tabs: **Websites** and **Updates**.

### Updates

This tab shows the last 7 scans of each website, newest first. Click a scan to
see what it found. A scan that found nothing says **No changes**, and a scan
whose email hasn't been sent yet says **Email not sent yet**.

**Internal links** are pages that were added to or removed from the website.
**Critical pages** shows the changes found on each of your critical pages:

| What you'll see | What it means |
| --- | --- |
| **Content changed**, with *Before* and *After* side by side | Wording was edited. Words taken out are in **red** on the left, and words put in are in **green** on the right. |
| **Text**, marked Added or Removed | Writing appeared on, or was taken off, the page. |
| **Links**, marked Added or Removed | A link on the page was added or taken away. Links in the website's menus aren't included, as they are the same on every page. |
| **Documents**, marked Added or Removed | A document such as a PDF was added or taken away. A replaced document usually shows as the old one removed and the new one added. |
| **Section changed** | Some writing moved to a different heading, or a heading was renamed. |
| **Unreachable** | The page couldn't be loaded on two scans in a row. |

Click any link or document to open it.

### Websites

Each website has its own box. Click it to open or close it. In the box you can:

- **Run Scan Now** to check the website straight away instead of waiting for
  its next scan. For an inactive website the button says **Scan Critical
  Pages Now**, as only its critical pages are checked.
- open the website or any of its critical pages by clicking the address.
- see how many pages Inwebstigator found on the whole website
  (**Internal pages**).
- **Add Critical Page**, or **Delete** one. The website's main page is always
  watched and can't be deleted.
- **Add Email** to send this website's reports to another address, or
  **Delete** one.
- **Delete Website** to stop watching it. This also removes its saved
  history, so you'll be asked to type `CONFIRM` first.

**Run All Scans** at the top scans every website straight away.

### Scan Settings

Each website has its own settings, under **Scan Settings** in its box. They
are already set to sensible values, so you don't need to change them. Click
**Save Settings** after changing anything. Changes apply from the next scan,
with no need to restart Inwebstigator.

| Setting | What it does |
| --- | --- |
| **Crawler Active** | Untick to stop crawling the whole website for new and removed pages. Its critical pages are still checked for changes. |
| **Request delay** / **Concurrent requests** | How fast Inwebstigator reads the website. Best left as they are unless the team suggests a change. If a website asks Inwebstigator to slow down, these are lowered automatically. |
| **Days between scans** | How often the website is checked. The default is 1 (daily). |

A website with more pages than Inwebstigator will scan (50,000) is made inactive
automatically, and its box says it is too large to scan. Its critical pages are
still checked.

---

## If something goes wrong

Inwebstigator keeps a log of what it does and any errors in
`%LOCALAPPDATA%\inwebstigator\logs\inwebstigator.log`. To open it, press
**Windows key + R**, paste that address and press **Enter**. The team may ask
you to send them this file.

| Problem | What to do |
| --- | --- |
| Inwebstigator shows an error or closes when opened | Contact the team and send them the log file. It's most likely an email setting on our side. |
| "Inwebstigator is already running" | It's open in the system tray. Click the tray icon and choose **Open**. |
| "We can't send an email to …" | Check the email address is spelt correctly. |
| A website can't be added | Check the address is correct and that the website isn't already on your list. |
| A scan says **Could not connect** or **Rate limited** | The website was down or asked Inwebstigator to slow down. It's tried again on the next scan. |
| A scan says **Most pages missing** | Most of the website's pages couldn't be found, which is usually a problem with the website rather than real changes. If it keeps happening for 3 scans, the pages are treated as really removed. |
| No emails for over a week | Check Spam/Junk, and make sure Inwebstigator is running (look for its icon in the system tray). |
| Slow to open | It's catching up on a missed scan. Give it a few minutes. |

For anything else, send the team a screenshot of what you're seeing and the
log file.

---

## Good to know about this test version

- Scans only happen while Inwebstigator is running and the computer is on.
- Uninstalling Inwebstigator also deletes its saved websites, history and
  logs.
- Websites are scanned one at a time, so when several are due it can take a
  while for all of them to finish.
