# Inwebstigator: Getting Started

Inwebstigator watches websites for you. It checks the websites you choose
every day and emails you when something changes.

This is an early test version. If anything is confusing or doesn't work,
please let the team know.


---

## Setting up

### 1. Unzip and open Inwebstigator

1. Extract the **Inwebstigator** zip file the team sent you
2. Open the extracted folder and double-click **Inwebstigator.exe**.
3. Windows may warn that it **"protected your PC"**, because the program is
   new. Click **More info**, then **Run anyway**.

The login page opens after a moment. If it doesn't, go to
**127.0.0.1:8000/login** in your web browser.

### 2. Create your account

1. Click **Sign Up**.
2. Enter your email address and choose a password. **Reports are sent to this
   email address.**
3. Log in with your new email and password.

> Please use a new password that you don't use anywhere else. This test
> version doesn't store passwords securely yet.

### 3. Add a website

1. On the dashboard, go to **Add Website**.
2. Enter the website's address, e.g. `https://www.example.edu.au/`.
3. Under **Critical Pages**, enter the pages you want monitored in more detail (text changes, documents etc.). Use **+ Add
   another critical page** for each extra page.
4. Click **Add Website**.


### 4. Run the first check

Under your website in **Monitored Websites**, open **Scan Settings** and click
**Run Scan Now**. It can take several minutes for a large site, and the page refreshes when it's done.

**The first scan lists everything on your critical pages as "Added"**, and
you'll get an email saying so. This is the initial run. In all subsequent reports it only shows what actually changed.

Setup is finished.

---

## Everyday use

**Keep Inwebstigator open and stay logged in.** In this test version, the
daily checks only happen while the program is open, you're logged in, and the
computer is on. You can minimise the window. If it was closed, it catches up
when you next open it, which can make startup take a few minutes.

**After restarting the computer,** open **Inwebstigator.exe** again and log in.

**Emails you'll receive** (they come from the Inwebstigator email account, not
from a person):

- **Website Update:** something changed. The email shows what's changed. It's also sent
  if a website couldn't be reached.
- **No changes have been found since the last notification:** a weekly note
  confirming Inwebstigator is still working, sent when nothing has changed.

The emails should go to your regular inbox but if they don't arrive, check your **Spam/Junk** folder and mark them as "Not spam".

---

## The dashboard

### Daily Changes

This section shows the most recent changes found on your critical pages, grouped by page.

| What you'll see | What it means |
| --- | --- |
| **Content changed**, with *Before* and *After* side by side | Wording was edited. Words taken out are in **red** on the left, and words put in are in **green** on the right. |
| Text marked **Added** | New writing appeared on the page. |
| Text marked **Removed** | Writing was taken off the page. |
| **Links**, marked Added or Removed | A link on the page was added or taken away. |
| **Documents**, marked Added or Removed | A document such as a PDF was added or taken away. A replaced document usually shows as the old one removed and the new one added. |
| **Section changed** | Some writing moved to a different heading, or a heading was renamed. |

Click any link or document to open it. The changes stay on the dashboard until
newer changes replace them, and every change is also emailed to you.

### Monitored Websites

Each website has its own box, where you can:

- open the website or any of its critical pages by clicking the address.
- see how many critical pages are being watched, and how many pages
  Inwebstigator found on the whole site (**internal pages**);
- **add a critical page** using the Add Critical Page box, or **Delete** one;
- **Delete Website** to stop watching it. This also removes its saved history.

### Scan Settings

These are already set to sensible values, so you don't need to change them.
Click **Save Settings** after changing anything.

| Setting | What it does |
| --- | --- |
| **Active** | Untick to pause checking this website without deleting it. |
| **Days between scans** | How often your websites are checked. The default is 1 (daily) |
| **Days between no-change notifications** | How often you get the "no changes" email when nothing changes. The default is 7 (weekly) |
| **Request delay** / **Concurrent requests** | How fast Inwebstigator reads a website. Best left as they are unless the team suggests a change. |

The two "Days between" settings apply to all your websites. **After changing
them, or after adding a new website, close Inwebstigator and open it again**
so the daily schedule picks up the change.

**Run Scan Now** checks the website straight away, instead of waiting for the
next daily check.

### Log Out

This is at the top right. Remember that daily checks need you to be logged in,
so please log back in afterwards.

---

## If something goes wrong

| Problem | What to do |
| --- | --- |
| Inwebstigator shows an error or closes when opened | Contact the team. It's most likely an email setting on our side. |
| **Run Scan Now** says "Unable to complete scan" | Contact the team. |
| "Incorrect email or password" | If you've forgotten your password, contact the team. |
| "Unable to create account" | That email may already have an account. Try logging in instead. |
| "Unable to add website" | Check the address starts with `https://`, and that the website isn't already on your list. |
| Everything was listed as "Added" | This is normal for a website's first scan. |
| No emails for over a week | Check Spam/Junk, and make sure Inwebstigator is open and you're logged in. |
| Slow to open | It's catching up on a missed check. Give it a few minutes. |

For anything else, send the team a screenshot of what you're seeing.

---

## Good to know about this test version

- Daily checks only happen while Inwebstigator is open and you're logged in.
- Only one person can be logged in at a time.
- Passwords aren't stored securely yet.
