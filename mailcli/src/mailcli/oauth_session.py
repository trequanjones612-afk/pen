"""Load or refresh the access token for an OAuth account.

Refresh tokens and access tokens stay in the OS credential store.
"""

from __future__ import annotations

import time

from mailcli.config import Settings
from mailcli.errors import MailcliError
from mailcli.oauth import (
    GOOGLE_SCOPE,
    MICROSOFT_SCOPES,
    THUNDERBIRD_CLIENT_ID,
    google_urls,
    microsoft_urls,
    refresh_access_token,
)
from mailcli.secrets import get_secret, set_secret


def obtain_access_token(settings: Settings) -> str:
    """Return a usable access token, refreshing it when the stored one is old."""
    cached = get_secret(settings.account, "access")
    expires = get_secret(settings.account, "access_expires")
    if cached and expires and expires.isdigit() and int(expires) > int(time.time()) + 30:
        return cached
    refresh = get_secret(settings.account, "refresh")
    if not refresh:
        raise MailcliError(
            f"Account {settings.account} uses OAuth and has no saved sign-in. "
            "Run python -m mailcli setup."
        )
    client_id = settings.oauth_client_id or THUNDERBIRD_CLIENT_ID
    if settings.provider == "gmail":
        _device, token_url = google_urls()
        scope = GOOGLE_SCOPE
    else:
        tenant = settings.oauth_tenant or "consumers"
        _device, token_url = microsoft_urls(tenant)
        scope = " ".join(MICROSOFT_SCOPES)
    token = refresh_access_token(
        client_id=client_id,
        refresh_token=refresh,
        token_url=token_url,
        scope=scope,
    )
    access = str(token["access_token"])
    new_refresh = str(token.get("refresh_token") or refresh)
    expires_in = int(token.get("expires_in") or 3600)
    set_secret(settings.account, "access", access)
    set_secret(settings.account, "refresh", new_refresh)
    set_secret(settings.account, "access_expires", str(int(time.time()) + expires_in - 60))
    return access
