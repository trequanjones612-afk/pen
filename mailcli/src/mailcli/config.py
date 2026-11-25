"""Load mailcli settings from a TOML or INI file plus the environment.

The password is never read from the file. ``MAILCLI_PASSWORD`` supplies it,
and an interactive terminal is prompted with getpass when the variable is unset.
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

    host: str = "127.0.0.1"
    imap_port: int = 1143
    smtp_port: int = 1025
    user: str = ""
    tls_verify: bool = True
    tls_cert: str | None = None
    folder: str = "INBOX"
    timeout: float = 30.0
    security: str = "starttls"
    insecure_localhost: bool = False
    password: str = ""
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

    if not settings.user:
        where = path if config_path or path.is_file() else config_directory() / "config"
        raise MailcliError(
            "No mail account is configured. Set user in the config file "
            f"({where}), or pass --user. The example file is config.example "
            "in the mailcli directory."
        )
    if not settings.host:
        raise MailcliError("The mail host is empty.")

    settings.password = password if password is not None else read_password()
    return settings


def read_password() -> str:
    """Return the mailbox password from the environment or a hidden prompt.

    Nothing is written to disk. A non-interactive run with no environment
    variable fails instead of blocking.
    """
    value = os.environ.get("MAILCLI_PASSWORD")
    if value is None:
        if not sys.stdin.isatty():
            raise MailcliError(
                "MAILCLI_PASSWORD is not set. Export your Proton Mail Bridge "
                "password in the environment. mailcli will prompt with getpass "
                "when it is run from an interactive terminal."
            )
        value = getpass("Bridge password: ")
    if value == "":
        raise MailcliError("MAILCLI_PASSWORD is empty.")
    return value


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


def _parse_config(text: str) -> dict[str, object]:
    """Parse a flat TOML document or an INI file into a string-keyed mapping.

    Python 3.9 has no tomllib, so a small reader covers the subset mailcli
    writes: comments, one optional table, strings, booleans, and integers.
    """
    values: dict[str, object] = {}
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if "=" not in line:
            raise MailcliError(f"Config line {line_no} is not key = value")
        key, raw_value = line.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if not key:
            raise MailcliError(f"Config line {line_no} is missing a key")
        values[key] = _parse_value(raw_value.strip(), line_no)
    return values


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


def _bool_value(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False
    raise MailcliError(f"{label} must be true or false")
