"""IMAP inbox, fetch, search, and folder listing over STARTTLS.

Listing uses ``BODY.PEEK`` so opening the table does not mark mail as read.
``read`` sets ``\\Seen`` afterwards.
"""

from __future__ import annotations

import imaplib
import re
import ssl
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

    def summaries(self, folder: str) -> list[Summary]:
        imap = self._require()
        _select(imap, folder)
        pairs = _uid_fetch(
            imap,
            "1:*",
            "(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
        )
        collected: list[tuple[datetime, int, Summary]] = []
        seen_uids: set[str] = set()
        for meta, literal in pairs:
            uid_match = _UID_RE.search(meta)
            if not uid_match:
                continue
            uid = uid_match.group(1).decode("ascii")
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
            flags = _flags(meta)
            header = literal or header_bytes(b"")
            parsed = parse_message(header if header.endswith(b"\r\n") else header + b"\r\n\r\n")
            when = parsed.date or _internaldate(meta)
            summary = Summary(
                index=0,
                uid=uid,
                when=when,
                sender=parsed.sender,
                subject=parsed.subject,
                unread=b"\\Seen" not in flags,
            )
            collected.append((_aware(when), int(uid), summary))
        collected.sort(key=lambda item: (item[0], item[1]), reverse=True)
        ordered: list[Summary] = []
        for index, (_, _, summary) in enumerate(collected, 1):
            summary.index = index
            ordered.append(summary)
        return ordered

    def read(self, folder: str, number: int) -> tuple[Summary, ParsedMessage, int]:
        if number < 1:
            raise MailcliError("Message numbers start at 1.")
        summaries = self.summaries(folder)
        if not summaries:
            raise MailcliError(f"{folder} is empty.")
        if number > len(summaries):
            raise MailcliError(
                f"No message {number}. {folder} has {len(summaries)} message"
                + ("s." if len(summaries) != 1 else ".")
            )
        summary = summaries[number - 1]
        imap = self._require()
        pairs = _uid_fetch(imap, summary.uid, "(UID FLAGS BODY.PEEK[])")
        raw = b""
        for _meta, literal in pairs:
            if literal:
                raw = literal
                break
        if not raw:
            raise MailcliError(f"The server returned no body for message {number}.")
        parsed = parse_message(raw)
        try:
            imap.uid("STORE", summary.uid, "+FLAGS", "(\\Seen)")
        except imaplib.IMAP4.error as exc:
            raise _imap_error(exc) from exc
        summary.unread = False
        return summary, parsed, len(summaries)

    def search(self, folder: str, text: str) -> list[Summary]:
        query = text.strip()
        if not query:
            raise MailcliError("Give the words to search for.")
        imap = self._require()
        _select(imap, folder)
        matched = _search_uids(imap, query)
        return [item for item in self.summaries(folder) if item.uid in matched]


def _connect(settings: Settings) -> imaplib.IMAP4:
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
    except TimeoutError as exc:
        raise MailcliError(
            f"Timed out connecting to IMAP at {settings.host}:{settings.imap_port}."
        ) from exc
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
        typ, data = _authenticate(imap, settings)
        if typ != "OK":
            raise MailcliError("Login failed. Check the account and its saved sign-in.")
        # ``data`` can echo server text. Drop it so it cannot leak into a traceback we print.
        del data
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
        raise _login_or_imap(exc) from exc
    except (TimeoutError, OSError) as exc:
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


def _select(imap: imaplib.IMAP4, folder: str) -> None:
    encoded = encode_mailbox(folder)
    typ, _data = imap.select(_quote(encoded))
    if typ != "OK":
        raise MailcliError(f"Could not open folder {folder}.")


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


def _search_uids(imap: imaplib.IMAP4, query: str) -> set[str]:
    try:
        encoded = query.encode("ascii")
        typ, data = imap.uid("SEARCH", "TEXT", _quote(encoded.decode("ascii")))
    except UnicodeEncodeError:
        encoded = query.encode("utf-8")
        imap.literal = encoded
        try:
            typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", "TEXT")
        finally:
            imap.literal = None
    except imaplib.IMAP4.error as exc:
        raise _imap_error(exc) from exc
    if typ != "OK":
        raise MailcliError("The server rejected the search.")
    blob = b""
    if data and data[0]:
        first = data[0]
        blob = first if isinstance(first, bytes) else bytes(first)
    return {part.decode("ascii", "replace") for part in blob.split() if part}


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


def _login_or_imap(exc: imaplib.IMAP4.error) -> MailcliError:
    text = _safe(exc).lower()
    if any(word in text for word in ("auth", "login", "credential", "password", "invalid")):
        return MailcliError("Login failed. Check the account name and MAILCLI_PASSWORD.")
    return MailcliError("The IMAP server rejected a command.")


def _imap_error(exc: imaplib.IMAP4.error) -> MailcliError:
    text = _safe(exc).lower()
    if "auth" in text or "login" in text:
        return MailcliError("Login failed. Check the account name and MAILCLI_PASSWORD.")
    if isinstance(exc, imaplib.IMAP4.abort) or "socket" in text or "eof" in text:
        return MailcliError("The IMAP server closed the connection.")
    return MailcliError("The IMAP server rejected a command.")
