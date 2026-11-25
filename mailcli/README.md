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

`inbox` fetches only the newest messages, 20 by default, even when the folder has tens of thousands. `--page 2` or `--offset 20` moves to the next older slice. `read 1` opens the first row of that same window. `*` in the `U` column means unread. Listing does not mark mail as read. `read` does. `search` asks the server (Gmail's `X-GM-RAW` when the server offers it) and fetches only the capped hits.

`python -m mailcli test-login` checks the saved sign-in and prints the server's error if it fails. `setup --verify` does that check before anything is saved.

`read` prints the headers and a plain-text body. Charsets and RFC 2047 encoded words are decoded. An HTML-only message is rendered as text. Attachments are listed and are not written out.

With no subcommand, mailcli opens a prompt loop (no curses):

```text
1        open that row
b        back to the list
n / p    next or previous message
r        refresh
s TEXT   search
f        list folders
g FOLDER switch folder
c        compose (end the body with a line that is only a period)
q        quit
```

## Tests

The tests start an in-process IMAP server and SMTP server on `127.0.0.1`. OAuth is tested against a local token endpoint, and the credential store is replaced with a fake. No real mailbox is contacted.

```powershell
py -3 -m pip install -e ".\mailcli[dev]"
cd mailcli
py -3 -m pytest
```

## Demo

`docs/demo.txt` is a transcript of `setup` choosing Gmail (no real password is stored in the transcript) and of `inbox` / `read` / `send` against a local GreenMail server configured as a custom account. `docs/inbox.png` shows that setup screen and the inbox.
