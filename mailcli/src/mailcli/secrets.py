"""Passwords and OAuth tokens in the OS credential store.

The config file never receives these values. ``MAILCLI_PASSWORD`` still
overrides a stored password for the account that is about to connect.
"""

from __future__ import annotations

import threading

from mailcli.errors import MailcliError

SERVICE = "mailcli"


def _backend():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise MailcliError(
            "The keyring package is not installed, so mailcli cannot use the "
            "OS credential store. Reinstall mailcli, or set MAILCLI_PASSWORD "
            "for this command."
        ) from exc
    return keyring, KeyringError


def get_secret(account: str, kind: str) -> str | None:
    """Return a stored secret, or None when the store has no entry.

    A missing or locked credential backend is treated as an empty store so a
    command that has ``MAILCLI_PASSWORD`` still runs.
    """
    try:
        keyring, keyring_error = _backend()
    except MailcliError:
        return None
    try:
        value = _call(keyring.get_password, SERVICE, _username(account, kind), keyring_error=keyring_error)
    except MailcliError:
        return None
    except keyring_error:
        return None
    if value is None or value == "":
        return None
    return value


def set_secret(account: str, kind: str, value: str) -> None:
    """Store a secret. Failure is reported and nothing is written to the config."""
    if not value:
        raise MailcliError("Refusing to store an empty secret.")
    keyring, keyring_error = _backend()
    try:
        _call(keyring.set_password, SERVICE, _username(account, kind), value, keyring_error=keyring_error)
    except keyring_error as exc:
        raise MailcliError(
            "Could not save the secret in the OS credential store "
            "(Windows Credential Manager, or the system keyring). "
            "The config file was not given a copy. "
            f"({exc.__class__.__name__})"
        ) from exc


def delete_secret(account: str, kind: str) -> None:
    """Remove one secret. A missing entry is not an error."""
    try:
        keyring, keyring_error = _backend()
    except MailcliError:
        return
    try:
        keyring.delete_password(SERVICE, _username(account, kind))
    except keyring_error:
        return


def _username(account: str, kind: str) -> str:
    return f"{account}:{kind}"


def _call(func, *args, keyring_error: type[Exception]):
    """Run a keyring call on a daemon thread so a locked store cannot hang the CLI."""
    box: dict[str, object] = {}

    def work() -> None:
        try:
            box["value"] = func(*args)
        except keyring_error as exc:
            box["error"] = exc
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(8)
    if thread.is_alive():
        raise MailcliError(
            "The OS credential store did not respond. On Windows this is "
            "Credential Manager. On Linux a Secret Service daemon has to be "
            "unlocked. MAILCLI_PASSWORD still overrides a stored password."
        )
    if "error" in box:
        error = box["error"]
        if isinstance(error, keyring_error):
            raise error
        raise MailcliError(f"The OS credential store failed. ({error.__class__.__name__})")
    return box.get("value")
