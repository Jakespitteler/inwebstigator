# The email account

For whoever looks after Inwebstigator: what to do if its emails stop, and how
to hand the email account over to someone else. For setting an account up from
scratch, see the README's [Email](../README.md#email).

**Contents:** [How the app uses the account](#how-the-app-uses-the-account) ·
[If the emails stop](#if-the-emails-stop) ·
[Changing the settings on the client's computer](#changing-the-settings-on-the-clients-computer) ·
[Handing the account over](#handing-the-account-over)

---

## How the app uses the account

Inwebstigator sends every email from one account, normally a Gmail account
made just for it:

- **Sending (SMTP):** reports, health checks and the emails sent when an
  address is added.
- **Reading the inbox (IMAP):** only to spot bounces, when an email to a new
  address comes back undelivered.

It logs in with an **App Password**, not the account's normal password. The
settings are in a `.env` file next to `inwebstigator.exe`:

| Where | `.env` is at |
| --- | --- |
| Installed app (the client's computer) | `%LOCALAPPDATA%\Programs\Inwebstigator\.env` |
| Development | The project's root folder |

---

## If the emails stop

### What it looks like

- **Recipients stop getting emails,** including the weekly health check.
- **On the Updates tab,** recent scans say **Email not sent yet**.
- **Run scan** says "The scan of *website* finished, but its report could not
  be emailed. It will be sent with the next scheduled scan."
- **Adding a website with emails, or adding an email,** fails.
- **The log** (`%LOCALAPPDATA%\inwebstigator\logs\inwebstigator.log`) has lines
  like `Failed to send 'Website update: …' to …`, followed by the error.

**Nothing is lost while the emails are down.** Each report is kept until it has
been emailed, and every scheduled scan (8am and 8pm) tries again. Once the
problem is fixed, all the waiting reports go out with the next one, one email
per recipient.

### Finding the cause

Search the log for the error under the `Failed to send` line:

| Error in the log | Cause | Fix |
| --- | --- | --- |
| `SMTPAuthenticationError` … `Username and Password not accepted` | The App Password no longer works. Google cancels every App Password when the account's password is changed, or when 2-Step Verification is turned off. | Turn 2-Step Verification back on if needed, create a new App Password, and [put it in `.env`](#changing-the-settings-on-the-clients-computer). |
| `SMTPAuthenticationError` mentioning sign-in or a web browser | Google has locked the account, e.g. after a sign-in it found suspicious. | Sign in to the account at gmail.com and follow Google's steps, then create a new App Password if asked. |
| `SMTPDataError` or `SMTPSenderRefused` mentioning a limit | The account has sent more email in a day than Gmail allows. | Wait a day. The reports go out with a later scan. |
| `SMTPConnectError`, `ConnectionRefusedError`, `TimeoutError` or `gaierror` | The computer can't reach the mail server: no internet, or a firewall blocking it. | Check the internet connection. Nothing else is needed; it's tried again at the next scan. |
| No error, but emails don't arrive | They're going to spam. | Ask recipients to check Spam/Junk and mark the emails as "Not spam". |
| `Could not open the inbox at …` (a warning) | Bounces can't be spotted, so a mistyped address could be added. Emails still send. | Check `IMAP_HOST`, or leave it blank for Gmail. |

### Checking it's fixed

1. Add your own address to a website's notification emails. An "Email address
   added to website monitoring" email should arrive within a minute.
2. Remove it again.

The waiting reports go out at the next scheduled scan. To send them sooner,
click **Run All Scans**, which also sends every waiting report.

---

## Changing the settings on the client's computer

The quickest way, without a new installer:

1. Open `%LOCALAPPDATA%\Programs\Inwebstigator\.env` in Notepad. To find it,
   press **Windows key + R**, paste `%LOCALAPPDATA%\Programs\Inwebstigator`
   and press **Enter**.
2. Change the line that needs changing, e.g. `EMAIL_PASSWORD=` followed by the
   new App Password (spaces are fine). Save the file.
3. Restart Inwebstigator: right-click its tray icon, choose **Quit**, then
   open it from the Start menu. Settings are only read when it starts.

Running a newer installer replaces this `.env` with the one built into the
installer, so make sure any new installer is built with the current settings.

---

## Handing the account over

Whoever owns the account controls the emails the client relies on. Ideally the
client's organisation owns it, so it doesn't depend on a student team. There
are two ways to hand it over.

### Keep the same account

Recipients keep getting emails from the same address, so nothing lands in spam.

1. **Change the account's password,** and give it to the new owner securely
   (in person or through a password manager, not by email).
2. **Change the recovery email and phone number** to the new owner's, and move
   2-Step Verification to their phone. Until then, the old owner can still get
   back in.
3. **Sign the old owner out everywhere:** Google Account → Security → Your
   devices.
4. **Create a new App Password.** Changing the password cancelled the old one,
   so the app stops sending until this is done.
5. **Put the new App Password in `.env`** on the client's computer (see
   [above](#changing-the-settings-on-the-clients-computer)), and in the `.env`
   used to build future installers.
6. **Delete old copies.** Anyone in the old team deletes their `.env` files and
   any installers they still have, as these hold the old password.

### Use a new account

Simpler, but the emails come from a new address.

1. Make the new account and its App Password (see the README's
   [Email](../README.md#email)).
2. Tell Jay Jay and the recipients the new address first, so they can mark it
   as "Not spam".
3. Change `EMAIL` and `EMAIL_PASSWORD` in `.env` on the client's computer and
   restart the app, or send them a new installer built with the new settings.
4. Check it works (see [Checking it's fixed](#checking-its-fixed)).
5. Once the new account is working, delete the old account, or at least remove
   its App Password (Google Account → Security → App passwords).
