"""IMAP inbox, fetch, search, and folder listing over STARTTLS.

Listing uses ``BODY.PEEK`` so opening the table does not mark mail as read.
``read`` sets ``\\Seen`` afterwards.
"""

from __future__ import annotations

import imaplib
import re
import socket
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mailcli.config import Settings
from mailcli.errors import MailcliError
from mailcli.mimeutil import ParsedMessage, header_bytes, parse_date, parse_message
from mailcli.oauth import xoauth2_sasl
from mailcli.mutf7 import decode as decode_mailbox
from mailcli.mutf7 import encode as encode_mailbox
from mailcli.tlsutil import build_ssl_context

_UID_RE = re.compile(br"\bUID (\d+)")
_FLAGS_RE = re.compile(br"FLAGS \(([^)]*)\)")
_INTERNALDATE_RE = re.compile(br'INTERNALDATE "([^"]+)"')
DEFAULT_LIMIT = 20
HEADER_ITEMS = "(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"

_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


@dataclass
class Summary:
    """One message in folder order, newest first, numbered from 1."""

    index: int
    uid: str
    when: datetime | None
    sender: str
    subject: str
    unread: bool


class ImapSession:
    """One STARTTLS IMAP connection."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._imap: imaplib.IMAP4 | None = None

    def __enter__(self) -> "ImapSession":
        self._imap = _connect(self.settings)
        return self

    def connect(self) -> None:
        if self._imap is not None:
            _close(self._imap)
        self._imap = _connect(self.settings)

    def ensure(self) -> None:
        """Keep one IMAP connection, and open a new one if the server dropped it."""
        if self._imap is None:
            self.connect()
            return
        try:
            self._imap.noop()
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, TimeoutError, socket.timeout):
            _close(self._imap)
            self._imap = None
            self.connect()

    def close(self) -> None:
        if self._imap is not None:
            _close(self._imap)
            self._imap = None

    def __exit__(self, exc_type, exc, tb) -> None:
        imap = self._imap
        self._imap = None
        if imap is None:
            return
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            try:
                imap.shutdown()
            except OSError:
                pass

    def _require(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise MailcliError("IMAP is not connected.")
        return self._imap

    def folders(self) -> list[str]:
        imap = self._require()
        try:
            typ, data = imap.list()
        except imaplib.IMAP4.error as exc:
            raise _imap_error(exc) from exc
        if typ != "OK":
            raise MailcliError("The server refused to list folders.")
        names: list[str] = []
        for item in data or []:
            if not item:
                continue
            name = _mailbox_from_list(item if isinstance(item, bytes) else bytes(item))
            if name and name not in names:
                names.append(name)
        return names

    def summaries(self, folder: str, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> tuple[list[Summary], int]:
        """Return one window of messages and the folder's EXISTS count.

        The window is the newest ``limit`` messages after skipping ``offset``
        newer ones. Only that sequence range is fetched.
        """
        self.ensure()
        limit, offset = _bounds(limit, offset)
        imap = self._require()
        exists = _select(imap, folder)
        if exists <= 0 or offset >= exists:
            if exists <= 0:
                _status(f"{folder} is empty.")
            else:
                _status(f"No messages in that range. {folder} has {exists:,} messages.")
            return [], exists
        start, end = _recent_span(exists, limit, offset)
        shown = min(limit, max(0, exists - offset))
        if offset:
            _status(f"Fetching {shown:,} of {exists:,}, starting after the newest {offset:,}...")
        else:
            _status(f"Fetching newest {shown:,} of {exists:,}...")
        pairs = _sequence_fetch(imap, f"{start}:{end}", HEADER_ITEMS)
        ordered = _summaries_from_pairs(pairs, index_base=1)
        page = ordered[offset : offset + limit]
        for position, item in enumerate(page, 1):
            item.index = position
        return page, exists

    def read(
        self,
        folder: str,
        number: int,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        prefer_html: bool = False,
    ) -> tuple[Summary, ParsedMessage, int]:
        """Open row ``number`` in the same window ``inbox`` would print."""
        if number < 1:
            raise MailcliError("Message numbers start at 1.")
        summaries, exists = self.summaries(folder, limit=limit, offset=offset)
        if not summaries:
            if exists <= 0:
                raise MailcliError(f"{folder} is empty.")
            raise MailcliError(
                f"No message {number}. That range is outside {folder}, which has {exists:,} messages."
            )
        if number > len(summaries):
            raise MailcliError(
                f"No message {number} in this window of {len(summaries)}. "
                f"{folder} has {exists:,} messages. Use --page or --offset to see older mail."
            )
        summary = summaries[number - 1]
        imap = self._require()
        _status(f"Fetching message {number}...")
        pairs = _uid_fetch(imap, summary.uid, "(UID FLAGS BODY.PEEK[])")
        raw = b""
        for _meta, literal in pairs:
            if literal:
                raw = literal
                break
        if not raw:
            raise MailcliError(f"The server returned no body for message {number}.")
        parsed = parse_message(raw, prefer_html=prefer_html)
        try:
            imap.uid("STORE", summary.uid, "+FLAGS", "(\\Seen)")
        except imaplib.IMAP4.error as exc:
            raise _imap_error(exc, self.settings) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise _timeout(self.settings) from exc
        summary.unread = False
        return summary, parsed, exists

    def read_uid(
        self,
        folder: str,
        uid: str,
        *,
        index: int,
        exists: int,
        prefer_html: bool = False,
    ) -> tuple[Summary, ParsedMessage, int]:
        """Fetch one message by UID. The row number comes from the saved listing."""
        self.ensure()
        imap = self._require()
        _select(imap, folder)
        _status(f"Fetching message {index}...")
        pairs = _uid_fetch(imap, uid, "(UID FLAGS BODY.PEEK[])")
        raw = b""
        for _meta, literal in pairs:
            if literal:
                raw = literal
                break
        if not raw:
            raise MailcliError(f"The server returned no body for message {index}.")
        parsed = parse_message(raw, prefer_html=prefer_html)
        try:
            imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        except imaplib.IMAP4.error as exc:
            raise _imap_error(exc, self.settings) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise _timeout(self.settings) from exc
        summary = Summary(
            index=index,
            uid=uid,
            when=parsed.date,
            sender=parsed.sender,
            subject=parsed.subject,
            unread=False,
        )
        return summary, parsed, exists

    def search(self, folder: str, text: str, *, limit: int = DEFAULT_LIMIT) -> tuple[list[Summary], int]:
        """Server-side search. Only the capped hits are fetched, not the folder."""
        query = text.strip()
        if not query:
            raise MailcliError("Give the words to search for.")
        self.ensure()
        limit, _offset = _bounds(limit, 0)
        imap = self._require()
        exists = _select(imap, folder)
        _status(f"Searching {folder}...")
        uids = _search_uids(imap, query, gmail=_gmail(imap, self.settings))
        if not uids:
            return [], exists
        chosen = sorted(uids, key=int, reverse=True)[:limit]
        _status(f"Fetching {len(chosen):,} of {len(uids):,} search hits...")
        pairs = _uid_fetch(imap, ",".join(chosen), HEADER_ITEMS)
        return _summaries_from_pairs(pairs, index_base=1), exists


def verify_login(settings: Settings) -> None:
    """Log in and log out. Raises MailcliError with the server text on failure."""
    imap = _connect(settings)
    _close(imap)


def _connect(settings: Settings) -> imaplib.IMAP4:
    _status(f"Connecting to {settings.host}:{settings.imap_port}...")
    context = build_ssl_context(
        settings.host,
        tls_verify=settings.tls_verify,
        tls_cert=settings.tls_cert,
        insecure_localhost=settings.insecure_localhost,
    )
    try:
        if settings.security == "ssl":
            imap = imaplib.IMAP4_SSL(
                settings.host,
                settings.imap_port,
                ssl_context=context,
                timeout=settings.timeout,
            )
        else:
            imap = imaplib.IMAP4(settings.host, settings.imap_port, timeout=settings.timeout)
    except ConnectionRefusedError as exc:
        raise MailcliError(
            f"Could not connect to IMAP at {settings.host}:{settings.imap_port}. "
            "Check the host and port from mailcli setup."
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise _timeout(settings) from exc
    except OSError as exc:
        raise MailcliError(
            f"Could not connect to IMAP at {settings.host}:{settings.imap_port}: {exc}"
        ) from exc
    try:
        if settings.security != "ssl":
            if "STARTTLS" not in imap.capabilities:
                raise MailcliError(
                    f"The IMAP server at {settings.host}:{settings.imap_port} does not offer STARTTLS."
                )
            imap.starttls(ssl_context=context)
        _apply_timeout(imap, settings)
        typ, data = _authenticate(imap, settings)
        if typ != "OK":
            raise _login_failure(settings, _bytes_text(data))
    except MailcliError:
        _close(imap)
        raise
    except ssl.SSLError as exc:
        _close(imap)
        raise MailcliError(
            f"TLS handshake with IMAP {settings.host}:{settings.imap_port} failed. "
            "For a local server with a self-signed certificate, set tls_verify = false "
            "only when the host is loopback, or set tls_cert to the PEM file. "
            f"({exc.__class__.__name__})"
        ) from exc
    except imaplib.IMAP4.error as exc:
        _close(imap)
        raise _login_failure(settings, _server_text(exc)) from exc
    except (TimeoutError, socket.timeout) as exc:
        _close(imap)
        raise _timeout(settings) from exc
    except OSError as exc:
        _close(imap)
        raise MailcliError(f"The IMAP connection to {settings.host} failed: {exc}") from exc
    return imap


def _authenticate(imap: imaplib.IMAP4, settings: Settings):
    if settings.auth == "xoauth2":
        if not settings.access_token:
            raise MailcliError(
                "This account uses OAuth, but there is no access token. "
                "Run python -m mailcli setup."
            )
        user = settings.user
        token = settings.access_token

        def mechanism(_challenge: bytes, _user: str = user, _token: str = token) -> str:
            return xoauth2_sasl(_user, _token)

        return imap.authenticate("XOAUTH2", mechanism)
    return imap.login(settings.user, settings.password)


def _close(imap: imaplib.IMAP4) -> None:
    try:
        imap.logout()
    except (imaplib.IMAP4.error, OSError):
        try:
            imap.shutdown()
        except OSError:
            pass


def _select(imap: imaplib.IMAP4, folder: str) -> int:
    encoded = encode_mailbox(folder)
    try:
        typ, data = imap.select(_quote(encoded))
    except imaplib.IMAP4.error as exc:
        raise _imap_error(exc) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise MailcliError("Timed out waiting for the server to open the folder.") from exc
    if typ != "OK":
        raise MailcliError(f"Could not open folder {folder}.")
    if not data or data[0] in (None, b"", ""):
        return 0
    try:
        return int(data[0])
    except (TypeError, ValueError):
        return 0


def _sequence_fetch(imap: imaplib.IMAP4, sequence_set: str, query: str) -> list[tuple[bytes, bytes]]:
    try:
        typ, data = imap.fetch(sequence_set, query)
    except imaplib.IMAP4.error as exc:
        raise _imap_error(exc) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise MailcliError("Timed out while fetching message headers.") from exc
    if typ != "OK":
        return []
    return _fetch_pairs(data)


def _bounds(limit: int, offset: int) -> tuple[int, int]:
    if limit <= 0:
        raise MailcliError("--limit must be a positive number.")
    if offset < 0:
        raise MailcliError("--offset cannot be negative.")
    return limit, offset


def _sequence_window(exists: int, limit: int, offset: int) -> tuple[int, int]:
    end = exists - offset
    start = max(1, end - limit + 1)
    return start, end


def _recent_span(exists: int, limit: int, offset: int) -> tuple[int, int]:
    """Arrival-order slice a bit larger than the page, so date sorting has room.

    Gmail does not implement IMAP SORT, so the extra messages are ordered on
    the client. The slice stays at the recent end of the folder.
    """
    extra = min(limit, 30)
    span = min(exists, offset + limit + extra)
    end = exists
    start = max(1, end - span + 1)
    return start, end


def _summaries_from_pairs(pairs: list[tuple[bytes, bytes]], *, index_base: int) -> list[Summary]:
    collected: list[tuple[datetime, int, int, Summary]] = []
    seen_uids: set[str] = set()
    for meta, literal in pairs:
        uid_match = _UID_RE.search(meta)
        if not uid_match:
            continue
        uid = uid_match.group(1).decode("ascii")
        if uid in seen_uids:
            continue
        seen_uids.add(uid)
        sequence = _sequence_number(meta)
        flags = _flags(meta)
        header = literal or b""
        if header and not header.endswith(b"\r\n"):
            header += b"\r\n\r\n"
        parsed = parse_message(header or header_bytes(b""))
        when = parsed.date or _internaldate(meta)
        summary = Summary(
            index=sequence if index_base == 0 else 0,
            uid=uid,
            when=when,
            sender=parsed.sender,
            subject=parsed.subject,
            unread=b"\\Seen" not in flags,
        )
        collected.append((_aware(when), int(uid), sequence, summary))
    collected.sort(key=lambda item: (item[0], item[1]), reverse=True)
    ordered: list[Summary] = []
    for position, (_when, _uid, _sequence, summary) in enumerate(collected, index_base or 1):
        if index_base:
            summary.index = position
        ordered.append(summary)
    return ordered


def _sequence_number(meta: bytes) -> int:
    match = re.match(br"(\d+)\s+FETCH", meta)
    if not match:
        return 0
    return int(match.group(1))


def _gmail(imap: imaplib.IMAP4, settings: Settings) -> bool:
    if settings.provider == "gmail":
        return True
    return "X-GM-EXT-1" in {item.upper() for item in imap.capabilities}


def _uid_fetch(imap: imaplib.IMAP4, uid_set: str, query: str) -> list[tuple[bytes, bytes]]:
    try:
        typ, data = imap.uid("FETCH", uid_set, query)
    except imaplib.IMAP4.error as exc:
        message = _safe(exc).lower()
        if "no" in message and ("message" in message or "empty" in message or uid_set == "1:*"):
            return []
        raise _imap_error(exc) from exc
    if typ != "OK":
        return []
    return _fetch_pairs(data)


def _search_uids(imap: imaplib.IMAP4, query: str, *, gmail: bool) -> list[str]:
    if gmail:
        try:
            return _run_search(imap, "X-GM-RAW", query)
        except MailcliError as exc:
            if "rejected" not in str(exc).lower():
                raise
    return _run_search(imap, "TEXT", query)


def _run_search(imap: imaplib.IMAP4, key: str, query: str) -> list[str]:
    try:
        query.encode("ascii")
        typ, data = imap.uid("SEARCH", key, _quote(query))
    except UnicodeEncodeError:
        encoded = query.encode("utf-8")
        imap.literal = encoded
        try:
            typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", key)
        finally:
            imap.literal = None
    except imaplib.IMAP4.error as exc:
        raise MailcliError(f"The server rejected the search: {_server_text(exc)}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise MailcliError("Timed out while searching the folder.") from exc
    if typ != "OK":
        raise MailcliError("The server rejected the search.")
    blob = b""
    if data and data[0]:
        first = data[0]
        blob = first if isinstance(first, bytes) else bytes(first)
    return [part.decode("ascii", "replace") for part in blob.split() if part]


def _fetch_pairs(data: list | None) -> list[tuple[bytes, bytes]]:
    pairs: list[tuple[bytes, bytes]] = []
    if not data or data == [None]:
        return pairs
    for item in data:
        if item is None:
            continue
        if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
            literal = item[1] if len(item) > 1 and item[1] is not None else b""
            pairs.append((bytes(item[0]), bytes(literal)))
            continue
        if isinstance(item, (bytes, bytearray)):
            text = bytes(item).strip()
            if not text or text in {b")", b"(", b"NIL"}:
                continue
            if b"UID" in text or text[:1].isdigit():
                pairs.append((text, b""))
    return pairs


def _aware(when: datetime | None) -> datetime:
    """Sort key. A missing zone is UTC, which is what ``Date: ... -0000`` means."""
    if when is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when


def _flags(meta: bytes) -> set[bytes]:
    match = _FLAGS_RE.search(meta)
    if not match:
        return set()
    return {flag for flag in match.group(1).split() if flag}


def _internaldate(meta: bytes) -> datetime | None:
    match = _INTERNALDATE_RE.search(meta)
    if not match:
        return None
    return _parse_internaldate(match.group(1).decode("ascii", "replace"))


def _parse_internaldate(value: str) -> datetime | None:
    """Parse IMAP INTERNALDATE, which uses ``25-Nov-2026 09:14:00 +0000``."""
    try:
        day_text, month_text, rest = value.strip().split("-", 2)
        year_text, time_text, zone = rest.split(" ")
        hour_text, minute_text, second_text = time_text.split(":")
        sign = 1 if zone.startswith("+") else -1
        offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5])) * sign
        return datetime(
            int(year_text),
            _MONTHS[month_text],
            int(day_text),
            int(hour_text),
            int(minute_text),
            int(second_text),
            tzinfo=timezone(offset),
        )
    except (KeyError, ValueError, IndexError):
        return parse_date(value.replace("-", " ", 2))


def _mailbox_from_list(line: bytes) -> str:
    text = line.decode("utf-8", "replace")
    match = re.search(r'"((?:\\.|[^"\\])*)"\s*$', text)
    if match:
        raw = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
        return decode_mailbox(raw)
    parts = text.split()
    if not parts:
        return ""
    return decode_mailbox(parts[-1].strip('"'))


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _safe(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc.args, tuple):
        text = " ".join(str(part) for part in exc.args)
    return text


def _imap_error(exc: imaplib.IMAP4.error, settings: Settings | None = None) -> MailcliError:
    text = _server_text(exc)
    lowered = text.lower()
    if any(word in lowered for word in ("auth", "login", "credential", "password", "invalid", "alert")):
        if settings is not None:
            return _login_failure(settings, text)
        return MailcliError(f"Login failed: {text}")
    if isinstance(exc, imaplib.IMAP4.abort) or "socket" in lowered or "eof" in lowered:
        return MailcliError("The IMAP server closed the connection.")
    return MailcliError(f"The IMAP server rejected a command: {text}")


def _login_failure(settings: Settings, server_text: str) -> MailcliError:
    """Include the server text and a hint. Name MAILCLI_PASSWORD only if it was sent."""
    detail = _scrub(server_text, settings).strip() or "the server rejected the login."
    pieces = [f"Login failed: {detail}"]
    hint = _provider_hint(settings.provider, detail)
    if hint:
        pieces.append(hint)
    if settings.password_source == "env":
        pieces.append("The password sent was MAILCLI_PASSWORD.")
    elif settings.password_source == "store":
        pieces.append(f"The password sent was the one stored for account {settings.account}.")
    return MailcliError(" ".join(pieces))


def _provider_hint(provider: str, server_text: str) -> str:
    lowered = server_text.lower()
    if provider == "gmail" or "application-specific password" in lowered:
        return (
            "Gmail does not accept the normal account password. "
            "Turn on 2-Step Verification, then create an app password at "
            "https://myaccount.google.com/apppasswords."
        )
    if provider == "yahoo":
        return (
            "Yahoo needs an app password from "
            "https://login.yahoo.com/account/security/app-passwords "
            "after two-step verification is on."
        )
    if provider == "icloud":
        return (
            "iCloud needs an app-specific password from https://account.apple.com. "
            "Two-factor authentication must be on."
        )
    if provider == "outlook":
        return "Outlook personal accounts sign in with the device code from mailcli setup, not the account password."
    if "invalid credentials" in lowered or "authenticationfailed" in lowered:
        return "Check the password saved for this account."
    return ""


def _scrub(text: str, settings: Settings) -> str:
    for secret in (settings.password, settings.access_token):
        if secret:
            text = text.replace(secret, "***")
    return text


def _timeout(settings: Settings) -> MailcliError:
    seconds = int(settings.timeout) if settings.timeout else 30
    return MailcliError(
        f"Timed out after {seconds} seconds waiting for IMAP at {settings.host}:{settings.imap_port}. "
        "The server did not answer."
    )


def _apply_timeout(imap: imaplib.IMAP4, settings: Settings) -> None:
    sock = getattr(imap, "sock", None)
    if sock is not None and settings.timeout:
        sock.settimeout(settings.timeout)


def _status(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _server_text(exc: BaseException) -> str:
    parts: list[str] = []
    for part in exc.args:
        parts.append(_bytes_text(part))
    text = " ".join(part for part in parts if part).strip()
    if text.startswith("b'") or text.startswith('b"'):
        text = text[2:-1]
    return text


def _bytes_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (list, tuple)):
        return " ".join(_bytes_text(part) for part in value if part is not None)
    return str(value)
