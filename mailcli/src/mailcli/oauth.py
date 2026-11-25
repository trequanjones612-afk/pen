"""OAuth 2.0 device-code flow for mailbox access.

Microsoft personal accounts are the supported interactive path. The same
request shape also talks to Google's device endpoint when the user supplies
their own client id. Tokens are returned to the caller and are not logged.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from mailcli.errors import MailcliError

# Mozilla publishes this id for Thunderbird so administrators can allow it.
# It is a public client (no secret) and requests IMAP, SMTP, and offline_access.
# https://blog.thunderbird.net/2023/01/important-message-for-microsoft-office-365-enterprise-users/
THUNDERBIRD_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

MICROSOFT_SCOPES = (
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
    "offline_access",
)
GOOGLE_SCOPE = "https://mail.google.com/"

UrlOpen = Callable[..., object]


def xoauth2_sasl(user: str, access_token: str) -> str:
    """Build the SASL XOAUTH2 initial response (still unencoded)."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def microsoft_urls(tenant: str) -> tuple[str, str]:
    """Device and token endpoints for one Microsoft tenant."""
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return f"{base}/devicecode", f"{base}/token"


def google_urls() -> tuple[str, str]:
    """Device and token endpoints for a user-supplied Google client."""
    return "https://oauth2.googleapis.com/device/code", "https://oauth2.googleapis.com/token"


def device_code_flow(
    *,
    client_id: str,
    scope: str,
    device_url: str,
    token_url: str,
    printer: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    urlopen: UrlOpen | None = None,
    timeout: float = 30.0,
) -> dict[str, object]:
    """Run RFC 8628 and return the token response.

    The caller stores ``access_token`` and ``refresh_token``. This function
    does not write them and does not include them in errors.
    """
    opener = urlopen or urllib.request.urlopen
    started = _post(
        opener,
        device_url,
        {"client_id": client_id, "scope": scope},
        timeout,
    )
    user_code = str(started.get("user_code", ""))
    verification = str(started.get("verification_uri") or "https://www.microsoft.com/devicelogin")
    device_code = str(started.get("device_code", ""))
    if not user_code or not device_code:
        raise MailcliError("The sign-in server did not return a device code.")
    message = str(started.get("message") or "").strip()
    printer(message or f"Open {verification} and enter the code {user_code}")
    printer(f"Code: {user_code}")
    printer(f"Page: {verification}")
    interval = float(started.get("interval") or 5)
    deadline = time.monotonic() + float(started.get("expires_in") or 900)
    while time.monotonic() < deadline:
        sleep(interval)
        try:
            token = _post(
                opener,
                token_url,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                },
                timeout,
            )
        except _Pending as pending:
            interval = float(pending.interval or interval)
            continue
        if token.get("access_token"):
            return token
        raise MailcliError("The sign-in server returned no access token.")
    raise MailcliError("The device code expired before the sign-in finished.")


def refresh_access_token(
    *,
    client_id: str,
    refresh_token: str,
    token_url: str,
    scope: str,
    urlopen: UrlOpen | None = None,
    timeout: float = 30.0,
) -> dict[str, object]:
    """Exchange a refresh token. The refresh token is not included in errors."""
    opener = urlopen or urllib.request.urlopen
    try:
        token = _post(
            opener,
            token_url,
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "scope": scope,
            },
            timeout,
        )
    except _Pending as exc:
        raise MailcliError("The token server is not ready to refresh this sign-in.") from exc
    if not token.get("access_token"):
        raise MailcliError("The token server did not refresh the sign-in. Run mailcli setup again.")
    return token


class _Pending(Exception):
    def __init__(self, interval: float | None) -> None:
        self.interval = interval


def _post(opener: UrlOpen, url: str, fields: dict[str, str], timeout: float) -> dict[str, object]:
    data = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        response = opener(request, timeout=timeout)
        raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        parsed = _json(detail)
        error = str(parsed.get("error", "")) if parsed else ""
        if error in {"authorization_pending", "slow_down"}:
            interval = parsed.get("interval") if parsed else None
            raise _Pending(float(interval) if isinstance(interval, (int, float)) else None) from exc
        raise MailcliError(f"Sign-in was rejected ({error or exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise MailcliError("Could not reach the sign-in server.") from exc
    parsed = _json(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))
    if not parsed:
        raise MailcliError("The sign-in server returned a response that was not JSON.")
    error = str(parsed.get("error", ""))
    if error in {"authorization_pending", "slow_down"}:
        interval = parsed.get("interval")
        raise _Pending(float(interval) if isinstance(interval, (int, float)) else None)
    if error:
        raise MailcliError(f"Sign-in was rejected ({error}).")
    return parsed


def _json(text: str) -> dict[str, object] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict):
        return value
    return None
