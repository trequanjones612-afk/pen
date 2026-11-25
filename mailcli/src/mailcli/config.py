"""Load mailcli settings from a TOML or INI file plus the environment.

The password is never read from the file. ``MAILCLI_PASSWORD`` overrides the
OS credential store. An interactive terminal is prompted with getpass when
neither one is set.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from mailcli.errors import MailcliError

_PASSWORD_KEY = re.compile(r"(?im)^\s*(password|passwd|pass)\s*=")


@dataclass
class Settings:
    """Connection settings. ``password`` is kept only in memory."""

    account: str = "default"
    provider: str = "custom"
    host: str = "127.0.0.1"
    imap_port: int = 1143
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_security: str = ""
    user: str = ""
    tls_verify: bool = True
    tls_cert: str | None = None
    folder: str = "INBOX"
    timeout: float = 30.0
    security: str = "starttls"
    auth: str = "password"
    oauth_client_id: str = ""
    oauth_tenant: str = ""
    insecure_localhost: bool = False
    password: str = ""
    password_source: str = ""
    access_token: str = ""
    config_path: Path | None = None


def config_root_text() -> str:
    """Return the config directory as a string.

    Kept as a string so the Windows branch can be checked on other systems.
    ``pathlib.Path`` refuses to build a Windows path when ``os.name`` is not
    ``nt``.
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "mailcli")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(root, "mailcli")


def config_directory() -> Path:
    """Return the platform config directory, without creating it.

    Windows uses ``%APPDATA%\\mailcli``. Everywhere else uses
    ``$XDG_CONFIG_HOME/mailcli`` or ``~/.config/mailcli``.
    """
    return Path(config_root_text())


def default_config_path() -> Path:
    """Return the config file to read, preferring one that already exists."""
    directory = config_directory()
    for name in ("config.toml", "config.ini", "config"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return directory / "config"


def load_settings(
    *,
    config_path: str | None = None,
    account: str | None = None,
    host: str | None = None,
    imap_port: int | None = None,
    smtp_port: int | None = None,
    user: str | None = None,
    folder: str | None = None,
    tls_cert: str | None = None,
    security: str | None = None,
    insecure_localhost: bool = False,
    password: str | None = None,
) -> Settings:
    """Merge defaults, the config file, the environment, and CLI overrides.

    Later sources win. The password argument is accepted for tests; the CLI
    does not expose a password flag.
    """
    path = Path(config_path).expanduser() if config_path else default_config_path()
    values: dict[str, object] = {}
    if config_path:
        if not path.is_file():
            raise MailcliError(f"Config file not found: {path}")
        values = _read_config_file(path)
    elif path.is_file():
        values = _read_config_file(path)

    settings = Settings(config_path=path if path.is_file() else None)
    values = _select_account(values, account, settings)
    _apply_mapping(settings, values)

    env_host = os.environ.get("MAILCLI_HOST")
    env_user = os.environ.get("MAILCLI_USER")
    env_imap = os.environ.get("MAILCLI_IMAP_PORT")
    env_smtp = os.environ.get("MAILCLI_SMTP_PORT")
    env_cert = os.environ.get("MAILCLI_TLS_CERT")
    env_verify = os.environ.get("MAILCLI_TLS_VERIFY")
    env_folder = os.environ.get("MAILCLI_FOLDER")
    if env_host:
        settings.host = env_host.strip()
    if env_user:
        settings.user = env_user.strip()
    if env_imap:
        settings.imap_port = _port(env_imap, "MAILCLI_IMAP_PORT")
    if env_smtp:
        settings.smtp_port = _port(env_smtp, "MAILCLI_SMTP_PORT")
    if env_cert:
        settings.tls_cert = env_cert
    if env_verify:
        settings.tls_verify = _bool_value(env_verify, "MAILCLI_TLS_VERIFY")
    if env_folder:
        settings.folder = env_folder

    if host:
        settings.host = host.strip()
    if imap_port is not None:
        settings.imap_port = _port(imap_port, "imap port")
    if smtp_port is not None:
        settings.smtp_port = _port(smtp_port, "smtp port")
    if user:
        settings.user = user.strip()
    if folder:
        settings.folder = folder
    if tls_cert:
        settings.tls_cert = tls_cert
    if security:
        settings.security = _security(security)
    settings.insecure_localhost = insecure_localhost

    if settings.tls_cert and settings.config_path is not None:
        cert_path = Path(settings.tls_cert).expanduser()
        if not cert_path.is_absolute():
            settings.tls_cert = str((settings.config_path.parent / cert_path).resolve())
        else:
            settings.tls_cert = str(cert_path)

    if not settings.smtp_host:
        settings.smtp_host = settings.host
    if not settings.smtp_security:
        settings.smtp_security = settings.security

    if not settings.user:
        where = path if config_path or path.is_file() else config_directory() / "config"
        raise MailcliError(
            "No mail account is configured. Run python -m mailcli setup, "
            f"or set user in {where}."
        )
    if not settings.host:
        raise MailcliError("The mail host is empty.")

    if password is not None:
        settings.password = password
        settings.password_source = "argument"
    elif settings.auth == "xoauth2":
        from mailcli.oauth_session import obtain_access_token

        settings.access_token = obtain_access_token(settings)
        settings.password_source = "oauth"
    else:
        settings.password, settings.password_source = read_password(settings.account)
    return settings


def read_password(account: str = "default") -> tuple[str, str]:
    """Return the password and where it came from: env, store, or prompt.

    Nothing is written to the config file. MAILCLI_PASSWORD is mentioned in
    errors only when that variable is set.
    """
    if "MAILCLI_PASSWORD" in os.environ:
        value = os.environ.get("MAILCLI_PASSWORD") or ""
        if value == "":
            raise MailcliError("MAILCLI_PASSWORD is empty.")
        return value, "env"
    from mailcli.secrets import get_secret

    stored = get_secret(account, "password")
    if stored:
        return stored, "store"
    if not sys.stdin.isatty():
        raise MailcliError(
            f"No stored password found in the credential store for account {account}. "
            "Run python -m mailcli setup from a terminal to save one."
        )
    prompted = getpass("Password or app password: ")
    if prompted == "":
        raise MailcliError("The password was empty.")
    return prompted, "prompt"


def _read_config_file(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise MailcliError(f"Could not read {path}: {exc}") from exc
    if _PASSWORD_KEY.search(text):
        raise MailcliError(
            f"{path} contains a password field. Remove it. "
            "Set MAILCLI_PASSWORD in the environment, or run mailcli in a "
            "terminal and type the password when prompted. Passwords are not "
            "read from disk."
        )
    try:
        return _parse_config(text)
    except MailcliError:
        raise
    except Exception as exc:
        raise MailcliError(f"Could not parse {path}: {exc}") from exc


def read_document(path: Path) -> dict[str, object]:
    """Parse a config file. Account tables stay nested under ``accounts``."""
    return _read_config_file(path)


def write_document(path: Path, accounts: dict[str, dict[str, object]], default_account: str) -> None:
    """Rewrite the config. Password and token keys are skipped."""
    secret_keys = {"password", "passwd", "pass", "refresh_token", "access_token", "token"}
    lines = [
        "# mailcli accounts. Secrets live in the OS credential store.",
        f'default_account = "{_escape(default_account)}"',
        "",
    ]
    for name in accounts:
        lines.append(f"[accounts.{name}]")
        for key, value in accounts[name].items():
            if key.startswith("_") or key.lower() in secret_keys:
                continue
            if value == "":
                continue
            lines.append(f"{key} = {_format_value(value)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def list_account_names(path: Path | None = None) -> tuple[list[str], str]:
    """Return account names and the default name. A legacy file has one name."""
    target = path or default_config_path()
    if not target.is_file():
        return [], ""
    document = read_document(target)
    accounts = document.get("accounts")
    default_name = str(document.get("default_account") or "")
    if isinstance(accounts, dict) and accounts:
        names = list(accounts)
        if not default_name and len(names) == 1:
            default_name = names[0]
        return names, default_name
    if document.get("user"):
        return ["default"], "default"
    return [], default_name


def _select_account(values: dict[str, object], account: str | None, settings: Settings) -> dict[str, object]:
    accounts = values.get("accounts")
    default_name = str(values.get("default_account") or "")
    flat = {key: item for key, item in values.items() if key not in {"accounts", "default_account"}}
    if not isinstance(accounts, dict) or not accounts:
        settings.account = account or "default"
        return flat
    name = account or default_name
    if not name:
        if len(accounts) == 1:
            name = next(iter(accounts))
        else:
            raise MailcliError(
                "Several accounts are configured. Pass --account with one of: "
                + ", ".join(str(item) for item in accounts)
            )
    selected = accounts.get(name)
    if not isinstance(selected, dict):
        known = ", ".join(str(item) for item in accounts)
        raise MailcliError(f"No account named {name}. Configured accounts: {known}")
    settings.account = str(name)
    merged = dict(flat)
    merged.update(selected)
    if "imap_host" in merged:
        merged["host"] = merged.pop("imap_host")
    if "imap_security" in merged:
        merged["security"] = merged.pop("imap_security")
    return merged


def _parse_config(text: str) -> dict[str, object]:
    """Parse TOML or INI, including ``[accounts.name]`` tables.

    Python 3.9 has no tomllib, so a small reader covers comments, account
    tables, strings, booleans, and integers.
    """
    values: dict[str, object] = {}
    accounts: dict[str, dict[str, object]] = {}
    current: dict[str, object] | None = None
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip()
            lowered = header.lower()
            if lowered.startswith("accounts."):
                name = header.split(".", 1)[1].strip()
                if not name:
                    raise MailcliError(f"Config line {line_no} has an empty account name")
                current = accounts.setdefault(name, {})
            else:
                current = None
            continue
        if "=" not in line:
            raise MailcliError(f"Config line {line_no} is not key = value")
        key, raw_value = line.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if not key:
            raise MailcliError(f"Config line {line_no} is missing a key")
        parsed = _parse_value(raw_value.strip(), line_no)
        if current is None:
            values[key] = parsed
        else:
            current[key] = parsed
    if accounts:
        values["accounts"] = accounts
    return values


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{_escape(str(value))}"'


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _parse_value(raw: str, line_no: int) -> object:
    if not raw:
        raise MailcliError(f"Config line {line_no} has an empty value")
    if raw[0] in {'"', "'"}:
        return _parse_string(raw, line_no)
    lowered = raw.lower()
    if lowered in {"true", "false", "yes", "no", "on", "off"}:
        return lowered in {"true", "yes", "on"}
    if re.fullmatch(r"[+-]?\d+", raw):
        return int(raw)
    return raw


def _parse_string(raw: str, line_no: int) -> str:
    quote = raw[0]
    if len(raw) < 2 or raw[-1] != quote:
        raise MailcliError(f"Config line {line_no} has an unterminated string")
    body = raw[1:-1]
    pieces: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and quote == '"':
            index += 1
            if index >= len(body):
                raise MailcliError(f"Config line {line_no} has a trailing escape")
            escaped = body[index]
            pieces.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}.get(escaped, escaped))
        else:
            pieces.append(char)
        index += 1
    return "".join(pieces)


def _apply_mapping(settings: Settings, values: dict[str, object]) -> None:
    known = {
        "host",
        "imap_port",
        "smtp_port",
        "user",
        "username",
        "tls_verify",
        "tls_cert",
        "cert",
        "folder",
        "timeout",
        "security",
        "smtp_host",
        "smtp_security",
        "auth",
        "provider",
        "oauth_client_id",
        "oauth_tenant",
    }
    unknown = sorted(set(values) - known)
    if unknown:
        raise MailcliError(
            "Unknown config key"
            + ("s: " if len(unknown) != 1 else ": ")
            + ", ".join(unknown)
        )
    if "host" in values:
        settings.host = str(values["host"]).strip()
    if "imap_port" in values:
        settings.imap_port = _port(values["imap_port"], "imap_port")
    if "smtp_port" in values:
        settings.smtp_port = _port(values["smtp_port"], "smtp_port")
    if "user" in values:
        settings.user = str(values["user"]).strip()
    elif "username" in values:
        settings.user = str(values["username"]).strip()
    if "tls_verify" in values:
        settings.tls_verify = _bool_value(values["tls_verify"], "tls_verify")
    if "tls_cert" in values:
        settings.tls_cert = str(values["tls_cert"]) or None
    elif "cert" in values:
        settings.tls_cert = str(values["cert"]) or None
    if "folder" in values:
        settings.folder = str(values["folder"]).strip() or "INBOX"
    if "security" in values:
        settings.security = _security(values["security"])
    if "smtp_host" in values:
        settings.smtp_host = str(values["smtp_host"]).strip()
    if "smtp_security" in values:
        settings.smtp_security = _security(values["smtp_security"])
    if "auth" in values:
        settings.auth = _auth(values["auth"])
    if "provider" in values:
        settings.provider = str(values["provider"]).strip().lower() or "custom"
    if "oauth_client_id" in values:
        settings.oauth_client_id = str(values["oauth_client_id"]).strip()
    if "oauth_tenant" in values:
        settings.oauth_tenant = str(values["oauth_tenant"]).strip()
    if "timeout" in values:
        try:
            settings.timeout = float(values["timeout"])
        except (TypeError, ValueError) as exc:
            raise MailcliError("timeout must be a number of seconds") from exc
        if settings.timeout <= 0:
            raise MailcliError("timeout must be greater than zero")


def _port(value: object, label: str) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise MailcliError(f"{label} must be an integer port") from exc
    if port < 1 or port > 65535:
        raise MailcliError(f"{label} must be between 1 and 65535")
    return port


def _security(value: object) -> str:
    text = str(value).strip().lower()
    if text in {"starttls", "ssl"}:
        return text
    raise MailcliError("security must be starttls or ssl")


def _auth(value: object) -> str:
    text = str(value).strip().lower()
    if text in {"password", "xoauth2"}:
        return text
    raise MailcliError("auth must be password or xoauth2")


def _bool_value(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False
    raise MailcliError(f"{label} must be true or false")
