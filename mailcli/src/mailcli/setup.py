"""Interactive account wizard.

Secrets go to the OS credential store. The config file receives hosts, ports,
the address, and which account is the default.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from mailcli.config import config_directory, default_config_path, read_document, write_document
from mailcli.errors import MailcliError
from mailcli.oauth import (
    GOOGLE_SCOPE,
    MICROSOFT_SCOPES,
    THUNDERBIRD_CLIENT_ID,
    device_code_flow,
    google_urls,
    microsoft_urls,
)
from mailcli.providers import MENU, PROTON_EXPLANATION, Provider, by_menu_choice, describe_endpoints
from mailcli.secrets import set_secret

Printer = Callable[[str], None]
Ask = Callable[[str], str]
PasswordAsk = Callable[[str], str]


def run_setup(
    *,
    config_path: Path | None = None,
    provider: str | None = None,
    user: str | None = None,
    account: str | None = None,
    oauth_client_id: str | None = None,
    oauth_tenant: str | None = None,
    make_default: bool | None = None,
    ask: Ask | None = None,
    password_ask: PasswordAsk | None = None,
    printer: Printer | None = None,
    device_flow: Callable[..., dict[str, object]] = device_code_flow,
    verify: bool = False,
) -> int:
    """Create or replace one account and return a process status."""
    say = printer or print
    prompt = ask or input
    hidden = password_ask or _hidden
    path = config_path or default_config_path()
    try:
        chosen = _choose_provider(provider, prompt, say)
        if chosen.key == "proton":
            say(PROTON_EXPLANATION.rstrip())
            say("")
            say("No Proton account was saved.")
            return 0
        say("")
        say(describe_endpoints(chosen))
        say("")
        address = (user or prompt("Email address: ")).strip()
        if "@" not in address or address.startswith("@") or address.endswith("@"):
            raise MailcliError("Enter the full email address, such as you@gmail.com.")
        name = (account or prompt(f"Account name [{_suggest_name(address)}]: ")).strip()
        if not name:
            name = _suggest_name(address)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise MailcliError("The account name may contain letters, numbers, dots, underscores, and hyphens.")
        fields = _fields_for(chosen, address, prompt, oauth_client_id, oauth_tenant)
        secret_kind, secret_value, extra = _collect_secret(chosen, fields, hidden, say, device_flow, oauth_client_id)
        fields.update(extra)
        accounts, default_name = _existing(path)
        if make_default is None:
            make_default = (not accounts) or _yes(prompt, "Make this the default account?", default_yes=not accounts)
        accounts[name] = fields
        if make_default or not default_name:
            default_name = name
        if verify:
            say("Checking the login before saving...")
            try:
                from mailcli.imap_client import verify_login

                verify_login(_preview_settings(name, fields, secret_kind, secret_value, extra))
            except MailcliError as exc:
                say(f"mailcli: {exc}")
                say("Nothing was saved.")
                return 1
            say("Login OK.")
        if secret_kind:
            set_secret(name, secret_kind, secret_value)
            if secret_kind == "refresh" and extra.get("_access_token"):
                set_secret(name, "access", str(extra["_access_token"]))
                set_secret(name, "access_expires", str(extra.get("_access_expires", "0")))
        path.parent.mkdir(parents=True, exist_ok=True)
        write_document(path, accounts, default_name)
        say("")
        say(f"Saved account {name} ({chosen.label}) to {path}")
        say("The password or token is in the OS credential store, not in that file.")
        if default_name == name:
            say(f"{name} is the default account.")
        return 0
    except MailcliError as exc:
        say(f"mailcli: {exc}")
        return 1
    except KeyboardInterrupt:
        say("")
        return 130
    except EOFError:
        say("")
        return 1


def _choose_provider(provider: str | None, prompt: Ask, say: Printer) -> Provider:
    if provider:
        try:
            return by_menu_choice(provider)
        except KeyError as exc:
            raise MailcliError(
                "Unknown provider. Choose gmail, outlook, yahoo, icloud, proton, or custom."
            ) from exc
    say("Choose a mail provider. mailcli will fill in the servers.")
    say("")
    for index, item in enumerate(MENU, 1):
        say(f"  {index}  {item.label}")
    say("")
    while True:
        try:
            return by_menu_choice(prompt("Provider number: "))
        except KeyError:
            say("Enter a number from the list, or a name such as gmail.")


def _fields_for(
    provider: Provider,
    address: str,
    prompt: Ask,
    oauth_client_id: str | None,
    oauth_tenant: str | None,
) -> dict[str, object]:
    if provider.key == "custom":
        imap_host = prompt("IMAP host: ").strip()
        imap_port = _port(prompt("IMAP port [993]: "), 993)
        imap_security = _security(prompt("IMAP security (ssl/starttls) [ssl]: "), "ssl")
        smtp_host = prompt("SMTP host: ").strip() or imap_host
        smtp_port = _port(prompt("SMTP port [587]: "), 587)
        smtp_security = _security(prompt("SMTP security (ssl/starttls) [starttls]: "), "starttls")
        auth = "password"
        client_id = ""
        tenant = ""
    else:
        imap_host = provider.imap_host
        imap_port = provider.imap_port
        imap_security = provider.imap_security
        smtp_host = provider.smtp_host
        smtp_port = provider.smtp_port
        smtp_security = provider.smtp_security
        auth = "xoauth2" if provider.key == "outlook" or oauth_client_id else provider.auth
        client_id = oauth_client_id or (THUNDERBIRD_CLIENT_ID if provider.key == "outlook" else "")
        tenant = oauth_tenant or ("consumers" if provider.key == "outlook" else "")
    if not imap_host:
        raise MailcliError("The IMAP host is empty.")
    return {
        "provider": provider.key,
        "user": address,
        "imap_host": imap_host,
        "imap_port": imap_port,
        "imap_security": imap_security,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_security": smtp_security,
        "auth": auth,
        "oauth_client_id": client_id,
        "oauth_tenant": tenant,
        "folder": "INBOX",
        "tls_verify": True,
    }


def _collect_secret(
    provider: Provider,
    fields: dict[str, object],
    hidden: PasswordAsk,
    say: Printer,
    device_flow: Callable[..., dict[str, object]],
    oauth_client_id: str | None,
) -> tuple[str, str, dict[str, object]]:
    if fields["auth"] == "xoauth2":
        client_id = str(fields.get("oauth_client_id") or "")
        if provider.key == "gmail":
            if not client_id:
                client_id = hidden("Google OAuth client id: ").strip()
            if not client_id:
                raise MailcliError("A Google OAuth client id is required for that sign-in.")
            fields["oauth_client_id"] = client_id
            device_url, token_url = google_urls()
            scope = GOOGLE_SCOPE
        else:
            tenant = str(fields.get("oauth_tenant") or "consumers")
            device_url, token_url = microsoft_urls(tenant)
            scope = " ".join(MICROSOFT_SCOPES)
            say("Sign in with the device code. This can take a minute.")
        token = device_flow(
            client_id=str(fields["oauth_client_id"]),
            scope=scope,
            device_url=device_url,
            token_url=token_url,
            printer=say,
        )
        refresh = str(token.get("refresh_token") or "")
        access = str(token.get("access_token") or "")
        if not refresh or not access:
            raise MailcliError("Sign-in finished without a refresh token. Run setup again.")
        expires_in = int(token.get("expires_in") or 3600)
        import time

        extra = {
            "_access_token": access,
            "_access_expires": str(int(time.time()) + expires_in - 60),
        }
        return "refresh", refresh, extra
    label = "App password: " if provider.key in {"gmail", "yahoo", "icloud"} else "Password: "
    secret = hidden(label)
    if not secret:
        raise MailcliError("The password was empty.")
    return "password", secret, {}


def _preview_settings(name, fields, secret_kind, secret_value, extra):
    from mailcli.config import Settings

    return Settings(
        account=name,
        provider=str(fields.get("provider") or "custom"),
        host=str(fields.get("imap_host") or ""),
        imap_port=int(fields.get("imap_port") or 993),
        smtp_host=str(fields.get("smtp_host") or fields.get("imap_host") or ""),
        smtp_port=int(fields.get("smtp_port") or 587),
        smtp_security=str(fields.get("smtp_security") or ""),
        user=str(fields.get("user") or ""),
        security=str(fields.get("imap_security") or "ssl"),
        auth=str(fields.get("auth") or "password"),
        tls_verify=bool(fields.get("tls_verify", True)),
        folder=str(fields.get("folder") or "INBOX"),
        oauth_client_id=str(fields.get("oauth_client_id") or ""),
        oauth_tenant=str(fields.get("oauth_tenant") or ""),
        password=secret_value if secret_kind == "password" else "",
        password_source="prompt",
        access_token=str(extra.get("_access_token") or ""),
        timeout=30,
    )


def _existing(path: Path) -> tuple[dict[str, dict[str, object]], str]:
    if not path.is_file():
        return {}, ""
    document = read_document(path)
    accounts = document.get("accounts")
    default_name = str(document.get("default_account") or "")
    if isinstance(accounts, dict) and accounts:
        cleaned = {name: dict(values) for name, values in accounts.items() if isinstance(values, dict)}
        return cleaned, default_name
    if document.get("user"):
        legacy = {key: value for key, value in document.items() if key not in {"accounts", "default_account"}}
        return {"default": legacy}, default_name or "default"
    return {}, default_name


def _suggest_name(address: str) -> str:
    local = address.split("@", 1)[0]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", local)
    return cleaned or "mail"


def _port(text: str, default: int) -> int:
    cleaned = text.strip()
    if not cleaned:
        return default
    if not cleaned.isdigit():
        raise MailcliError(f"{cleaned} is not a port number.")
    value = int(cleaned)
    if value < 1 or value > 65535:
        raise MailcliError("Ports are between 1 and 65535.")
    return value


def _security(text: str, default: str) -> str:
    cleaned = text.strip().lower() or default
    if cleaned not in {"ssl", "starttls"}:
        raise MailcliError("Security must be ssl or starttls.")
    return cleaned


def _yes(prompt: Ask, question: str, *, default_yes: bool) -> bool:
    suffix = " [Y/n]: " if default_yes else " [y/N]: "
    answer = prompt(question + suffix).strip().lower()
    if not answer:
        return default_yes
    return answer in {"y", "yes"}


def _hidden(prompt: str) -> str:
    import getpass

    return getpass.getpass(prompt)


def default_setup_path() -> Path:
    """Path setup writes when the user did not pass ``--config``."""
    path = default_config_path()
    if path.is_file():
        return path
    return config_directory() / "config"
