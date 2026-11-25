"""Built-in mail providers.

Ports and security modes are the ones each provider documents for IMAP and
SMTP. Proton is listed so setup can explain why a normal login cannot work.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """Connection preset and the sentence setup shows before asking for a secret."""

    key: str
    label: str
    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    auth: str
    intro: str


GMAIL = Provider(
    key="gmail",
    label="Gmail",
    imap_host="imap.gmail.com",
    imap_port=993,
    imap_security="ssl",
    smtp_host="smtp.gmail.com",
    smtp_port=465,
    smtp_security="ssl",
    auth="password",
    intro=(
        "Gmail rejects your normal account password for IMAP and SMTP.\n"
        "Turn on 2-Step Verification, then create an app password:\n"
        "  https://myaccount.google.com/apppasswords\n"
        "Google's own steps: https://support.google.com/accounts/answer/185833\n"
        "The app password is 16 letters. Spaces in it do not matter.\n"
        "SMTP on port 587 with STARTTLS also works; this preset uses port 465 with SSL.\n"
        "If you would rather use OAuth, pass --oauth-client-id from a Google Cloud\n"
        "client you registered yourself. An app password is the usual path."
    ),
)

OUTLOOK = Provider(
    key="outlook",
    label="Outlook, Hotmail, or Live",
    imap_host="outlook.office365.com",
    imap_port=993,
    imap_security="ssl",
    smtp_host="smtp-mail.outlook.com",
    smtp_port=587,
    smtp_security="starttls",
    auth="xoauth2",
    intro=(
        "Personal Outlook, Hotmail, and Live accounts sign in with Microsoft\n"
        "OAuth (XOAUTH2), not with the account password.\n"
        "A browser on any device opens https://www.microsoft.com/devicelogin\n"
        "and you type the code mailcli prints.\n"
        "Work and school mailboxes use smtp.office365.com on port 587 with\n"
        "STARTTLS instead of smtp-mail.outlook.com, and the tenant is often\n"
        "\"common\" rather than \"consumers\". An admin may have to allow the\n"
        "application id. Override it with oauth_client_id after registering a\n"
        "free public client in Microsoft Entra:\n"
        "  https://learn.microsoft.com/entra/identity-platform/quickstart-register-app\n"
        "Enable public client flows, then request these scopes:\n"
        "  https://outlook.office.com/IMAP.AccessAsUser.All\n"
        "  https://outlook.office.com/SMTP.Send\n"
        "  offline_access"
    ),
)

YAHOO = Provider(
    key="yahoo",
    label="Yahoo",
    imap_host="imap.mail.yahoo.com",
    imap_port=993,
    imap_security="ssl",
    smtp_host="smtp.mail.yahoo.com",
    smtp_port=465,
    smtp_security="ssl",
    auth="password",
    intro=(
        "Yahoo rejects your normal account password for IMAP and SMTP.\n"
        "Turn on two-step verification, then create an app password:\n"
        "  https://login.yahoo.com/account/security/app-passwords\n"
        "Yahoo's steps: https://help.yahoo.com/kb/SLN15241"
    ),
)

ICLOUD = Provider(
    key="icloud",
    label="iCloud",
    imap_host="imap.mail.me.com",
    imap_port=993,
    imap_security="ssl",
    smtp_host="smtp.mail.me.com",
    smtp_port=587,
    smtp_security="starttls",
    auth="password",
    intro=(
        "iCloud rejects your Apple ID password for IMAP and SMTP.\n"
        "Two-factor authentication has to be on for the Apple ID.\n"
        "Create an app-specific password under Sign-In and Security at\n"
        "  https://account.apple.com\n"
        "Apple's steps: https://support.apple.com/en-us/102654\n"
        "The mailbox login is the full Apple ID email address."
    ),
)

PROTON = Provider(
    key="proton",
    label="Proton",
    imap_host="",
    imap_port=0,
    imap_security="starttls",
    smtp_host="",
    smtp_port=0,
    smtp_security="starttls",
    auth="password",
    intro="",
)

CUSTOM = Provider(
    key="custom",
    label="Custom server",
    imap_host="",
    imap_port=993,
    imap_security="ssl",
    smtp_host="",
    smtp_port=587,
    smtp_security="starttls",
    auth="password",
    intro="Enter the IMAP and SMTP servers from your provider.",
)

PROVIDERS: dict[str, Provider] = {
    item.key: item for item in (GMAIL, OUTLOOK, YAHOO, ICLOUD, PROTON, CUSTOM)
}

MENU: tuple[Provider, ...] = (GMAIL, OUTLOOK, YAHOO, ICLOUD, PROTON, CUSTOM)

PROTON_EXPLANATION = """\
Proton Mail does not offer IMAP or SMTP for a normal account login.
The desktop program Proton Mail Bridge is the official way to get those
protocols, and it requires a paid Proton plan. mailcli is not using Bridge.

A direct login would have to speak Proton's private web API: an SRP
password proof, an optional authenticator code, a human-verification page,
and OpenPGP decryption of every message with keys derived from the account
password. That API is not a stable public contract. It changes without
notice, and a script is often stopped until a person completes verification
in a browser. Unofficial clients also cannot be checked against a real
mailbox from this project.

mailcli does not include that path. Use Proton's web app or official apps.
If Bridge is already running on this computer, choose Custom and point the
account at 127.0.0.1, IMAP port 1143 and SMTP port 1025, security starttls,
with tls_verify = false. The password is the Bridge mailbox password, not
the proton.me password.
"""


def by_menu_choice(text: str) -> Provider:
    """Resolve a menu number or a provider key."""
    cleaned = text.strip().lower()
    if cleaned.isdigit():
        index = int(cleaned)
        if 1 <= index <= len(MENU):
            return MENU[index - 1]
    aliases = {
        "outlook": "outlook",
        "hotmail": "outlook",
        "live": "outlook",
        "microsoft": "outlook",
        "apple": "icloud",
        "me.com": "icloud",
        "protonmail": "proton",
        "proton.me": "proton",
    }
    key = aliases.get(cleaned, cleaned)
    if key in PROVIDERS:
        return PROVIDERS[key]
    raise KeyError(cleaned)


def describe_endpoints(provider: Provider) -> str:
    """One block of host, port, and security lines."""
    if provider.key == "proton":
        return PROTON_EXPLANATION.rstrip()
    return "\n".join(
        [
            f"{provider.label}",
            f"  IMAP  {provider.imap_host}:{provider.imap_port}  {provider.imap_security.upper()}",
            f"  SMTP  {provider.smtp_host}:{provider.smtp_port}  {provider.smtp_security.upper()}",
            provider.intro,
        ]
    ).rstrip()
