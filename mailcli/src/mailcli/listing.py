"""Remember the last list so ``read N`` opens that row's UID.

The file lives in the cache directory, not next to passwords. It stores
account, folder, and the UIDs in display order. Row numbers are never IMAP
sequence numbers.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from mailcli.config import config_directory


@dataclass
class Listing:
    """One displayed page, inbox or search."""

    account: str
    folder: str
    limit: int
    offset: int
    mode: str
    query: str
    exists: int
    uids: list[str]


def cache_directory() -> Path:
    """Cache directory. Windows prefers ``%LOCALAPPDATA%\\mailcli``."""
    override = os.environ.get("MAILCLI_CACHE_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "mailcli"
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "mailcli"


def listing_path() -> Path:
    return cache_directory() / "listing.json"


def history_path() -> Path:
    return cache_directory() / "history"


def save_listing(listing: Listing) -> None:
    path = listing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(listing)), encoding="utf-8")


def load_listing() -> Listing | None:
    path = listing_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        uids = [str(item) for item in raw.get("uids", [])]
        return Listing(
            account=str(raw.get("account", "")),
            folder=str(raw.get("folder", "INBOX")),
            limit=int(raw.get("limit", 0)),
            offset=int(raw.get("offset", 0)),
            mode=str(raw.get("mode", "inbox")),
            query=str(raw.get("query", "")),
            exists=int(raw.get("exists", 0)),
            uids=uids,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def same_account(listing: Listing, account: str, folder: str) -> bool:
    return listing.account == account and listing.folder == folder


def fallback_cache_dir() -> Path:
    """Used only if a caller wants the config directory as a last resort."""
    return config_directory()
