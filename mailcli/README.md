# mailcli

mailcli is an original terminal email client. It is not a port of NeoMutt, Mutt, or any other mail client. IMAP and SMTP use the Python standard library (`imaplib`, `smtplib`, `email`, `ssl`). OAuth uses `urllib`. Secrets use the `keyring` package, which talks to Windows Credential Manager. It runs in Windows PowerShell 5.1 and later, with no WSL or MSYS2.

`rich` is optional. When it is installed and stdout is a terminal, `inbox` and `search` draw a table. Otherwise mailcli prints aligned columns.

![mailcli setup and inbox](docs/inbox.png)

## Requirements

- Python 3.9 or newer. On Windows, install it from [python.org](https://www.python.org/downloads/) and enable “Add python.exe to PATH”.
- An email account on Gmail, Outlook, Hotmail, Live, Yahoo, iCloud, or any other IMAP/SMTP server.

## Install (PowerShell)

From the repository root:

```powershell
py -3 -m pip install -e .\mailcli
py -3 -m pip install -e ".\mailcli[rich]"
```

`python` works in place of `py -3` when the installer put it on `PATH`. The install creates a `mailcli` command. If PowerShell says it cannot find `mailcli`, the Scripts directory is not on `PATH`. Use the module form, which does not need that directory:

```powershell
python -m mailcli setup
python -m mailcli inbox
```

Or add the Scripts folder for your Python version. The `3xx` segment is the version `py -3` printed, such as `Python313`:

```powershell
$scripts = Join-Path $env:APPDATA "Python\Python313\Scripts"
$env:Path = "$scripts;$env:Path"
```

To keep that for new terminals, add the same folder under Settings, System, About, Advanced system settings, Environment Variables, Path.

On Linux and macOS the install is `pip install -e ./mailcli`. The config file is `~/.config/mailcli/config` (or `$XDG_CONFIG_HOME/mailcli/config`).

## Setup

`python -m mailcli` with no config file starts the same wizard as `python -m mailcli setup`.

```text
  1  Gmail
  2  Outlook, Hotmail, or Live
  3  Yahoo
  4  iCloud
  5  Proton
  6  Custom server
```

The wizard writes hosts, ports, and the address into the config file. The password or refresh token goes to the OS credential store. `MAILCLI_PASSWORD` overrides a stored password for a single command and is never written to the config file.

More than one account is normal. The first one is the default. Later commands can name another:

```powershell
python -m mailcli --account work inbox
python -m mailcli accounts
```

### Gmail

IMAP `imap.gmail.com` port 993 SSL. SMTP `smtp.gmail.com` port 465 SSL (port 587 STARTTLS also works if you change the account).

Gmail will not accept the normal account password. Turn on 2-Step Verification, then create an app password at <https://myaccount.google.com/apppasswords>. Google's steps are at <https://support.google.com/accounts/answer/185833>.

An optional OAuth path exists for a client id you register yourself: `python -m mailcli setup --provider gmail --user you@gmail.com --oauth-client-id YOUR_CLIENT_ID`. The app password is the path the wizard uses otherwise.

### Outlook, Hotmail, and Live

Personal accounts use OAuth (XOAUTH2), not the account password. IMAP is `outlook.office365.com` port 993 SSL. SMTP is `smtp-mail.outlook.com` port 587 STARTTLS. Work and school mail often uses `smtp.office365.com` on that same port, and `oauth_tenant = "common"` instead of `consumers`.

mailcli prints a code. Open <https://www.microsoft.com/devicelogin> and enter it. The default application id is Thunderbird's public client id `9e5f94bc-e8a4-4e73-b8be-63364c29d753`, which Mozilla publishes for IMAP and SMTP access. A tenant that blocks unknown applications needs an admin to allow that id, or you register a free public client in Microsoft Entra and set `oauth_client_id`: <https://learn.microsoft.com/entra/identity-platform/quickstart-register-app>. Enable public client flows. The scopes are:

- `https://outlook.office.com/IMAP.AccessAsUser.All`
- `https://outlook.office.com/SMTP.Send`
- `offline_access`

### Yahoo

IMAP `imap.mail.yahoo.com` port 993 SSL. SMTP `smtp.mail.yahoo.com` port 465 SSL. Turn on two-step verification, then create an app password at <https://login.yahoo.com/account/security/app-passwords>. Yahoo's steps: <https://help.yahoo.com/kb/SLN15241>.

### iCloud

IMAP `imap.mail.me.com` port 993 SSL. SMTP `smtp.mail.me.com` port 587 STARTTLS. Two-factor authentication must be on. Create an app-specific password at <https://account.apple.com> under Sign-In and Security. Apple's steps: <https://support.apple.com/en-us/102654>. The login is the full Apple ID address.

### Proton

Proton Mail does not offer IMAP or SMTP for a normal login. Bridge is the official protocol adapter, and it requires a paid plan. A direct client would have to implement Proton's private web API (SRP, optional two-factor, human verification, and OpenPGP decryption). That API is not a stable public contract, and mailcli does not include it. Use Proton's web app or official apps. If Bridge is already running, choose Custom and use `127.0.0.1`, IMAP port 1143, SMTP port 1025, STARTTLS, and `tls_verify = false`.

### Custom

Any other IMAP and SMTP server. `tls_verify = false` is refused unless the host is `127.0.0.1`, `localhost`, or `::1`. A PEM file in `tls_cert` stays verified.

## Shell

The usual way to read mail is the shell. It logs in once and keeps the connection open.

```powershell
python -m mailcli
python -m mailcli shell
```

```text
mailcli 0.2.0. Type help, or a command. Ctrl+D quits.
mailcli [home INBOX]> inbox
mailcli [home INBOX]> read 1
mailcli [home INBOX]> search invoice
mailcli [home INBOX]> 1
mailcli [home INBOX]> next
mailcli [home INBOX]> compose
mailcli [home INBOX]> quit
```

`inbox 100` lists 100 rows. `next` and `prev` turn the page. A bare number opens that row of the list on screen. After `search`, the number refers to the search results. `compose` and `reply 3` ask you to confirm before anything is sent. `open 3` writes the HTML part to a temporary file and opens it in the browser. On Windows, `pyreadline3` adds arrow-key history and Tab completion when it is installed (`pip install pyreadline3`); without it the same commands still work.

`inbox --limit 100` followed by `read 71` uses the row you just saw. The row number is saved as a UID under `%LOCALAPPDATA%\mailcli` (or `%APPDATA%\mailcli` if local app data is unset), not as an IMAP sequence number.

## Commands

```powershell
python -m mailcli setup
python -m mailcli test-login
python -m mailcli inbox
python -m mailcli inbox --page 2
python -m mailcli read 1
python -m mailcli folders
python -m mailcli search "invoice"
python -m mailcli send --to friend@example.com --subject "Hello" --body "Hi from mailcli"
Get-Content .\note.txt | python -m mailcli send --to friend@example.com --subject "Notes"
```

`inbox` fetches only a recent slice, 20 rows by default, even when the folder has tens of thousands. Dates are the Date header, shown in local time, newest first. `--page 2` moves to older mail. `read 71` after `inbox --limit 100` opens row 71 of that listing by UID. `*` in the `U` column means unread. Listing does not mark mail as read. `read` does. `search` asks the server (Gmail's `X-GM-RAW` when the server offers it) and fetches only the capped hits. A later `read` uses those hits.

`python -m mailcli test-login` checks the saved sign-in and prints the server's error if it fails. `setup --verify` does that check before anything is saved.

`read` prints the headers and a plain-text body. When a message has both parts, the plain part is used unless it is only a stub; `read --html` or `view html` in the shell shows the HTML part instead. Marketing HTML is flattened: hidden preheaders, scripts, and spacer images are dropped, and links stay inline as `Holiday Cards [1]`. `links 1` or `read 1 --links` prints those URLs, shortened unless you add `--full`. `open` still opens the original HTML. A body taller than the terminal pauses like `more` (space for the next page, q to stop). Set `pager = false` in the config to print it all at once.

## Tests

The tests start an in-process IMAP server and SMTP server on `127.0.0.1`. OAuth is tested against a local token endpoint, and the credential store is replaced with a fake. No real mailbox is contacted.

```powershell
py -3 -m pip install -e ".\mailcli[dev]"
cd mailcli
py -3 -m pytest
```

## Demo

`docs/demo.txt` is a transcript of the shell against a local GreenMail server: the banner, `inbox`, `read 1`, and `search`. `docs/inbox.png` is that session. No real mailbox is used.
