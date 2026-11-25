"""In-process IMAP and SMTP servers for mailcli tests.

They speak the small subset the client uses: STARTTLS, authentication, folder
list, select, UID fetch, UID search, UID store, and SMTP submission. Messages
accepted by SMTP are delivered to the shared INBOX.
"""

from __future__ import annotations

import base64
import hmac
import re
import socket
import socketserver
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

_LITERAL = re.compile(r"^\{(\d+)\}$")


@dataclass
class StoredMessage:
    uid: int
    raw: bytes
    flags: set[str]
    internaldate: datetime


@dataclass
class Mailbox:
    """Folders shared by the test IMAP and SMTP servers."""

    folders: dict[str, list[StoredMessage]] = field(default_factory=lambda: {"INBOX": [], "Sent": []})
    _next_uid: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)
    virtual_count: dict[str, int] = field(default_factory=dict)
    fetched: int = 0
    fetch_sets: list[str] = field(default_factory=list)
    last_search: str = ""
    gmail: bool = False
    auth_failure: str = ""

    def add(self, folder: str, raw: bytes, flags: set[str] | None = None) -> int:
        when = _internaldate(raw)
        with self._lock:
            uid = self._next_uid
            self._next_uid += 1
            self.folders.setdefault(folder, []).append(
                StoredMessage(uid=uid, raw=raw, flags=set(flags or set()), internaldate=when)
            )
            return uid

    def names(self) -> list[str]:
        with self._lock:
            names = list(self.folders)
        names.sort(key=lambda name: (name.upper() != "INBOX", name.upper()))
        return names

    def messages(self, folder: str) -> list[StoredMessage]:
        key = _canonical(folder, self.folders)
        with self._lock:
            return list(self.folders.get(key, []))

    def total(self, folder: str) -> int:
        key = _canonical(folder, self.folders)
        with self._lock:
            return self.virtual_count.get(key, 0) + len(self.folders.get(key, []))

    def message_by_uid(self, folder: str, uid: int) -> tuple[int, StoredMessage] | None:
        key = _canonical(folder, self.folders)
        with self._lock:
            virtual = self.virtual_count.get(key, 0)
            if 1 <= uid <= virtual:
                return uid, _virtual_message(uid)
            for offset, message in enumerate(self.folders.get(key, []), start=1):
                if message.uid == uid:
                    return virtual + offset, message
        return None

    def message_by_seq(self, folder: str, seq: int) -> StoredMessage | None:
        key = _canonical(folder, self.folders)
        with self._lock:
            virtual = self.virtual_count.get(key, 0)
            real = self.folders.get(key, [])
            if seq < 1 or seq > virtual + len(real):
                return None
            if seq <= virtual:
                return _virtual_message(seq)
            return real[seq - virtual - 1]

    def note_fetch(self, sequence_set: str, count: int) -> None:
        with self._lock:
            self.fetch_sets.append(sequence_set)
            self.fetched += count

    def resolve(self, folder: str) -> str | None:
        with self._lock:
            return _canonical(folder, self.folders) if _exists(folder, self.folders) else None

    @property
    def next_uid(self) -> int:
        with self._lock:
            return self._next_uid

    def update_flags(self, folder: str, uid: int, action: str, flags: list[str]) -> StoredMessage | None:
        key = _canonical(folder, self.folders)
        with self._lock:
            for message in self.folders.get(key, []):
                if message.uid != uid:
                    continue
                if action == "+FLAGS":
                    message.flags.update(flags)
                elif action == "-FLAGS":
                    message.flags.difference_update(flags)
                else:
                    message.flags = set(flags)
                return message
        return None


class _Service(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, handler, mailbox: Mailbox, certfile: Path, keyfile: Path, user: str, password: str):
        super().__init__(("127.0.0.1", 0), handler)
        self.mailbox = mailbox
        self.certfile = str(certfile)
        self.keyfile = str(keyfile)
        self.user = user
        self.password = password
        self.access_token = ""


class RunningServer:
    """A background test server bound to an ephemeral loopback port."""

    def __init__(self, httpd: _Service) -> None:
        self.httpd = httpd
        self.thread = threading.Thread(target=httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> None:
        self.thread.start()
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.02)
        raise RuntimeError("test mail server did not accept connections")

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def start_pair(
    mailbox: Mailbox,
    certfile: Path,
    keyfile: Path,
    user: str,
    password: str,
) -> tuple[RunningServer, RunningServer]:
    """Start IMAP and SMTP on random ports and return them already listening."""
    imap = RunningServer(_Service(_ImapHandler, mailbox, certfile, keyfile, user, password))
    smtp = RunningServer(_Service(_SmtpHandler, mailbox, certfile, keyfile, user, password))
    imap.start()
    smtp.start()
    return imap, smtp


def write_certificate(directory: Path) -> tuple[Path, Path]:
    """Create a one-day self-signed certificate for 127.0.0.1."""
    import subprocess

    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "2",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


class _Buffered:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buf = b""

    def replace(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buf = b""

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def sendline(self, text: str) -> None:
        self.send(text.encode("utf-8") + b"\r\n")

    def readline(self) -> bytes | None:
        while b"\r\n" not in self.buf:
            try:
                chunk = self.sock.recv(8192)
            except (TimeoutError, socket.timeout, ssl.SSLError, OSError):
                return None
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

    def readexactly(self, size: int) -> bytes:
        while len(self.buf) < size:
            chunk = self.sock.recv(max(8192, size - len(self.buf)))
            if not chunk:
                raise ConnectionError("eof")
            self.buf += chunk
        data = self.buf[:size]
        self.buf = self.buf[size:]
        return data


class _ImapHandler(socketserver.BaseRequestHandler):
    server: _Service

    def handle(self) -> None:
        self.request.settimeout(15)
        try:
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        conn = _Buffered(self.request)
        selected: str | None = None
        tls = False
        try:
            conn.sendline("* OK test IMAP ready")
            while True:
                line = conn.readline()
                if line is None:
                    return
                tokens = _tokenize(line.decode("utf-8", "replace"))
                tokens = _literals(conn, tokens)
                if len(tokens) < 2:
                    tag = tokens[0] if tokens else "*"
                    conn.sendline(f"{tag} BAD empty command")
                    continue
                tag, command = tokens[0], tokens[1].upper()
                args = tokens[2:]
                if command == "CAPABILITY":
                    caps = "IMAP4rev1 AUTH=PLAIN AUTH=XOAUTH2"
                    if self.server.mailbox.gmail:
                        caps += " X-GM-EXT-1"
                    if not tls:
                        caps += " STARTTLS"
                    conn.sendline(f"* CAPABILITY {caps}")
                    conn.sendline(f"{tag} OK CAPABILITY completed")
                elif command == "STARTTLS":
                    if tls:
                        conn.sendline(f"{tag} BAD TLS already active")
                        continue
                    conn.sendline(f"{tag} OK begin TLS")
                    wrapped = _server_context(self.server).wrap_socket(conn.sock, server_side=True)
                    conn.replace(wrapped)
                    tls = True
                elif command == "LOGIN":
                    if not tls:
                        conn.sendline(f"{tag} NO STARTTLS required")
                        continue
                    user = args[0] if args else ""
                    password = args[1] if len(args) > 1 else ""
                    if _secret_equal(user, self.server.user) and _secret_equal(password, self.server.password):
                        conn.sendline(f"{tag} OK LOGIN completed")
                    else:
                        failure = self.server.mailbox.auth_failure or "NO [AUTHENTICATIONFAILED] Authentication failed"
                        conn.sendline(f"{tag} {failure}")
                elif command == "AUTHENTICATE":
                    if not tls:
                        conn.sendline(f"{tag} NO STARTTLS required")
                        continue
                    mechanism = (args[0] if args else "").upper()
                    if mechanism != "XOAUTH2":
                        conn.sendline(f"{tag} NO unsupported mechanism")
                        continue
                    conn.sendline("+")
                    blob = conn.readline()
                    if blob is None:
                        return
                    if _xoauth_matches(blob.decode("ascii", "replace"), self.server):
                        conn.sendline(f"{tag} OK AUTHENTICATE completed")
                    else:
                        conn.sendline(f"{tag} NO [AUTHENTICATIONFAILED] Authentication failed")
                elif command == "LIST":
                    for name in self.server.mailbox.names():
                        conn.sendline(f'* LIST (\\HasNoChildren) "/" "{name}"')
                    conn.sendline(f"{tag} OK LIST completed")
                elif command == "SELECT":
                    folder = args[0] if args else "INBOX"
                    resolved = self.server.mailbox.resolve(folder)
                    if resolved is None:
                        conn.sendline(f"{tag} NO no such mailbox")
                        continue
                    selected = resolved
                    count = self.server.mailbox.total(resolved)
                    conn.sendline("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
                    conn.sendline(f"* {count} EXISTS")
                    conn.sendline("* 0 RECENT")
                    conn.sendline("* OK [UIDVALIDITY 1] UIDs valid")
                    conn.sendline(f"* OK [UIDNEXT {self.server.mailbox.next_uid}] Predicted next UID")
                    conn.sendline(f"{tag} OK [READ-WRITE] SELECT completed")
                elif command == "FETCH":
                    if selected is None:
                        conn.sendline(f"{tag} NO select a mailbox first")
                        continue
                    seq_set = args[0] if args else "1:*"
                    spec = args[-1] if len(args) > 1 else ""
                    total = self.server.mailbox.total(selected)
                    sequences = _sequence_numbers(seq_set, total)
                    sent = 0
                    for seq in sequences:
                        message = self.server.mailbox.message_by_seq(selected, seq)
                        if message is None:
                            continue
                        _send_fetch(conn, seq, message, spec)
                        sent += 1
                    self.server.mailbox.note_fetch(seq_set, sent)
                    conn.sendline(f"{tag} OK FETCH completed")
                elif command == "UID":
                    if selected is None:
                        conn.sendline(f"{tag} NO select a mailbox first")
                        continue
                    _uid_command(conn, self.server.mailbox, selected, tag, args)
                elif command == "NOOP":
                    conn.sendline(f"{tag} OK NOOP completed")
                elif command == "LOGOUT":
                    conn.sendline("* BYE")
                    conn.sendline(f"{tag} OK LOGOUT completed")
                    return
                elif command == "CHECK":
                    conn.sendline(f"{tag} OK CHECK completed")
                else:
                    conn.sendline(f"{tag} BAD unsupported command")
        except (ConnectionError, OSError, ssl.SSLError, TimeoutError):
            return


class _SmtpHandler(socketserver.BaseRequestHandler):
    server: _Service

    def handle(self) -> None:
        self.request.settimeout(15)
        try:
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        conn = _Buffered(self.request)
        tls = False
        authed = False
        try:
            conn.sendline("220 localhost ESMTP mailcli-test")
            while True:
                line = conn.readline()
                if line is None:
                    return
                text = line.decode("utf-8", "replace")
                upper = text.upper()
                if upper.startswith("EHLO") or upper.startswith("HELO"):
                    conn.sendline("250-localhost")
                    if not tls:
                        conn.sendline("250-STARTTLS")
                    conn.sendline("250 AUTH XOAUTH2 PLAIN LOGIN")
                elif upper.startswith("STARTTLS"):
                    if tls:
                        conn.sendline("454 TLS already active")
                        continue
                    conn.sendline("220 Ready to start TLS")
                    wrapped = _server_context(self.server).wrap_socket(conn.sock, server_side=True)
                    conn.replace(wrapped)
                    tls = True
                    authed = False
                elif upper.startswith("AUTH"):
                    if not tls:
                        conn.sendline("530 Must issue a STARTTLS command first")
                        continue
                    authed = _smtp_auth(conn, self.server, text)
                elif upper.startswith("MAIL FROM"):
                    if not authed:
                        conn.sendline("530 Authentication required")
                        continue
                    conn.sendline("250 OK")
                elif upper.startswith("RCPT TO"):
                    if not authed:
                        conn.sendline("530 Authentication required")
                        continue
                    conn.sendline("250 OK")
                elif upper == "DATA":
                    if not authed:
                        conn.sendline("530 Authentication required")
                        continue
                    conn.sendline("354 End data with <CR><LF>.<CR><LF>")
                    lines: list[bytes] = []
                    while True:
                        data_line = conn.readline()
                        if data_line is None:
                            return
                        if data_line == b".":
                            break
                        if data_line.startswith(b".."):
                            data_line = data_line[1:]
                        lines.append(data_line)
                    raw = b"\r\n".join(lines) + b"\r\n"
                    self.server.mailbox.add("INBOX", raw)
                    conn.sendline("250 OK queued")
                elif upper.startswith("RSET"):
                    conn.sendline("250 OK")
                elif upper.startswith("NOOP"):
                    conn.sendline("250 OK")
                elif upper.startswith("QUIT"):
                    conn.sendline("221 Bye")
                    return
                else:
                    conn.sendline("500 Command unrecognized")
        except (ConnectionError, OSError, ssl.SSLError, TimeoutError):
            return


def _uid_command(conn: _Buffered, mailbox: Mailbox, folder: str, tag: str, args: list[str]) -> None:
    if not args:
        conn.sendline(f"{tag} BAD missing UID command")
        return
    kind = args[0].upper()
    rest = args[1:]
    messages = mailbox.messages(folder)
    if kind == "FETCH":
        spec = rest[-1] if rest else ""
        uid_set = rest[0] if rest else "1:*"
        sent = 0
        if mailbox.virtual_count.get(folder, 0):
            for uid in _uid_numbers(uid_set):
                found = mailbox.message_by_uid(folder, uid)
                if found is None:
                    continue
                seq, message = found
                _send_fetch(conn, seq, message, spec)
                sent += 1
        else:
            chosen = _select_uids(messages, uid_set)
            for index, message in enumerate(messages, 1):
                if message.uid not in chosen:
                    continue
                _send_fetch(conn, index, message, spec)
                sent += 1
        mailbox.note_fetch(uid_set, sent)
        conn.sendline(f"{tag} OK FETCH completed")
    elif kind == "SEARCH":
        mailbox.last_search = _search_key(rest)
        matched = _search(messages, rest)
        joined = " ".join(str(uid) for uid in matched)
        conn.sendline(f"* SEARCH {joined}".rstrip())
        conn.sendline(f"{tag} OK SEARCH completed")
    elif kind == "STORE":
        if len(rest) < 3:
            conn.sendline(f"{tag} BAD missing STORE arguments")
            return
        uid_set, action, flag_token = rest[0], rest[1].upper(), rest[2]
        flags = [part for part in flag_token.strip("()").split() if part]
        for index, message in enumerate(messages, 1):
            if str(message.uid) != uid_set and message.uid not in _select_uids(messages, uid_set):
                continue
            updated = mailbox.update_flags(folder, message.uid, action, flags)
            if updated is None:
                continue
            rendered = " ".join(sorted(updated.flags))
            conn.sendline(f"* {index} FETCH (UID {message.uid} FLAGS ({rendered}))")
        conn.sendline(f"{tag} OK STORE completed")
    else:
        conn.sendline(f"{tag} BAD unsupported UID command")


def _send_fetch(conn: _Buffered, seq: int, message: StoredMessage, spec: str) -> None:
    flags = " ".join(sorted(message.flags))
    stamp = _format_internaldate(message.internaldate)
    prefix = f'UID {message.uid} FLAGS ({flags}) INTERNALDATE "{stamp}"'
    if "HEADER.FIELDS" in spec.upper():
        fields = _header_names(spec)
        blob = _header_block(message.raw, fields)
        section = "BODY[HEADER.FIELDS (" + " ".join(fields) + ")]"
        _literal(conn, seq, f"{prefix} {section}", blob)
    elif "BODY.PEEK[]" in spec.upper() or "BODY[]" in spec.upper() or "RFC822" in spec.upper():
        _literal(conn, seq, f"{prefix} BODY[]", message.raw)
    else:
        conn.sendline(f"* {seq} FETCH ({prefix})")


def _literal(conn: _Buffered, seq: int, prefix: str, blob: bytes) -> None:
    head = f"* {seq} FETCH ({prefix} {{{len(blob)}}}\r\n".encode("utf-8")
    conn.send(head)
    conn.send(blob)
    conn.send(b")\r\n")


def _search(messages: list[StoredMessage], criteria: list[str]) -> list[int]:
    items = list(criteria)
    if items and items[0].upper() == "CHARSET" and len(items) >= 2:
        items = items[2:]
    text: str | None = None
    unseen_only = False
    index = 0
    while index < len(items):
        key = items[index].upper()
        if key in {"TEXT", "X-GM-RAW"} and index + 1 < len(items):
            text = items[index + 1]
            index += 2
            continue
        if key == "UNSEEN":
            unseen_only = True
        index += 1
    found: list[int] = []
    for message in messages:
        if unseen_only and "\\Seen" in message.flags:
            continue
        if text is not None and not _contains(message.raw, text):
            continue
        found.append(message.uid)
    return found


def _contains(raw: bytes, needle: str) -> bool:
    """Match TEXT the way a mailbox does: decoded headers and body, not just wire bytes."""
    folded = needle.casefold()
    if folded in raw.decode("utf-8", "replace").casefold():
        return True
    try:
        from mailcli.mimeutil import parse_message

        parsed = parse_message(raw)
    except Exception:
        return False
    blob = "\n".join([parsed.sender, parsed.to, parsed.cc, parsed.subject, parsed.body])
    return folded in blob.casefold()


def _search_key(criteria: list[str]) -> str:
    items = list(criteria)
    if items and items[0].upper() == "CHARSET" and len(items) >= 2:
        items = items[2:]
    for item in items:
        if item.upper() in {"TEXT", "X-GM-RAW"}:
            return item.upper()
    return ""


def _uid_numbers(spec: str) -> list[int]:
    numbers: list[int] = []
    for part in spec.split(","):
        if not part or part == "*":
            continue
        if ":" in part:
            start_text, end_text = part.split(":", 1)
            if start_text == "*" or end_text == "*":
                continue
            start, end = int(start_text), int(end_text)
            if start > end:
                start, end = end, start
            numbers.extend(range(start, end + 1))
        else:
            numbers.append(int(part))
    return numbers


def _sequence_numbers(spec: str, total: int) -> list[int]:
    if total <= 0:
        return []
    numbers: list[int] = []
    for part in spec.split(","):
        if not part:
            continue
        if ":" in part:
            start_text, end_text = part.split(":", 1)
            start = 1 if start_text == "*" else int(start_text)
            end = total if end_text == "*" else int(end_text)
            if start > end:
                start, end = end, start
            start = max(1, start)
            end = min(total, end)
            numbers.extend(range(start, end + 1))
        else:
            seq = int(part)
            if 1 <= seq <= total:
                numbers.append(seq)
    return numbers


def _virtual_message(seq: int) -> StoredMessage:
    """A tiny header-only message so a huge folder does not have to be stored."""
    when = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=seq)
    raw = (
        f"From: Person {seq} <p{seq}@example.com>\r\n"
        f"To: demo@localhost\r\n"
        f"Subject: Virtual message {seq}\r\n"
        f"Date: {when.strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
        f"\r\n"
        f"Body {seq}\r\n"
    ).encode("ascii")
    return StoredMessage(uid=seq, raw=raw, flags=set(), internaldate=when)


def _select_uids(messages: list[StoredMessage], spec: str) -> set[int]:
    if spec in {"1:*", "*", "1:*"}:
        return {message.uid for message in messages}
    wanted: set[int] = set()
    known = {message.uid for message in messages}
    for part in spec.split(","):
        if not part:
            continue
        if ":" in part:
            start_text, end_text = part.split(":", 1)
            start = min(known) if start_text == "*" else int(start_text)
            end = max(known) if end_text == "*" else int(end_text)
            if start > end:
                start, end = end, start
            wanted.update(uid for uid in known if start <= uid <= end)
        else:
            wanted.add(int(part))
    return wanted


def _header_names(spec: str) -> list[str]:
    match = re.search(r"HEADER\.FIELDS\s*\(([^)]*)\)", spec, re.IGNORECASE)
    if not match:
        return ["FROM", "SUBJECT", "DATE"]
    return match.group(1).split() or ["FROM", "SUBJECT", "DATE"]


def _header_block(raw: bytes, fields: list[str]) -> bytes:
    header = raw.split(b"\r\n\r\n", 1)[0]
    if b"\r\n\r\n" not in raw and b"\n\n" in raw:
        header = raw.split(b"\n\n", 1)[0]
    lines = header.split(b"\r\n") if b"\r\n" in header else header.split(b"\n")
    wanted = {name.lower() for name in fields}
    kept: list[bytes] = []
    current: list[bytes] = []
    keep = False

    def flush() -> None:
        if keep and current:
            kept.extend(current)

    for line in lines:
        if line.startswith((b" ", b"\t")):
            current.append(line)
            continue
        flush()
        current = [line]
        name = line.split(b":", 1)[0].decode("ascii", "replace").lower() if b":" in line else ""
        keep = name in wanted
    flush()
    return b"\r\n".join(kept) + b"\r\n\r\n"


def _smtp_auth(conn: _Buffered, server: _Service, text: str) -> bool:
    parts = text.split(" ")
    mechanism = parts[1].upper() if len(parts) > 1 else ""
    if mechanism == "XOAUTH2":
        if len(parts) < 3:
            conn.sendline("334 ")
            extra = conn.readline()
            blob = extra.decode("ascii", "replace") if extra else ""
        else:
            blob = parts[2]
        if _xoauth_matches(blob, server):
            conn.sendline("235 Authentication successful")
            return True
        conn.sendline("535 Authentication failed")
        return False
    if mechanism == "PLAIN":
        if len(parts) >= 3:
            blob = parts[2]
        else:
            conn.sendline("334 ")
            extra = conn.readline()
            blob = extra.decode("ascii", "replace") if extra else ""
        return _check_plain(conn, server, blob)
    if mechanism == "LOGIN":
        conn.sendline("334 VXNlcm5hbWU6")
        user_line = conn.readline()
        conn.sendline("334 UGFzc3dvcmQ6")
        pass_line = conn.readline()
        if user_line is None or pass_line is None:
            return False
        try:
            user = base64.b64decode(user_line).decode("utf-8")
            password = base64.b64decode(pass_line).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            conn.sendline("535 Authentication failed")
            return False
        if _secret_equal(user, server.user) and _secret_equal(password, server.password):
            conn.sendline("235 Authentication successful")
            return True
        conn.sendline("535 Authentication failed")
        return False
    conn.sendline("504 Unrecognized authentication type")
    return False


def _xoauth_matches(blob: str, server: _Service) -> bool:
    if not server.access_token:
        return False
    try:
        decoded = base64.b64decode(blob).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user = ""
    token = ""
    for part in decoded.split("\x01"):
        if part.startswith("user="):
            user = part[5:]
        elif part.lower().startswith("auth=bearer "):
            token = part.split(" ", 1)[1]
    return _secret_equal(user, server.user) and _secret_equal(token, server.access_token)


def _check_plain(conn: _Buffered, server: _Service, blob: str) -> bool:
    try:
        decoded = base64.b64decode(blob)
        fields = decoded.split(b"\0")
        if len(fields) >= 3:
            user = fields[1].decode("utf-8")
            password = fields[2].decode("utf-8")
        elif len(fields) == 2:
            user = fields[0].decode("utf-8")
            password = fields[1].decode("utf-8")
        else:
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        conn.sendline("535 Authentication failed")
        return False
    if _secret_equal(user, server.user) and _secret_equal(password, server.password):
        conn.sendline("235 Authentication successful")
        return True
    conn.sendline("535 Authentication failed")
    return False


def _literals(conn: _Buffered, tokens: list[str]) -> list[str]:
    resolved: list[str] = []
    for token in tokens:
        match = _LITERAL.fullmatch(token)
        if not match:
            resolved.append(token)
            continue
        conn.sendline("+ continue")
        data = conn.readexactly(int(match.group(1)))
        trailer = conn.readline()
        if trailer not in (b"", None):
            # imaplib writes the literal and then CRLF, so the trailer is empty.
            pass
        resolved.append(data.decode("utf-8", "replace"))
    return resolved


def _tokenize(line: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    length = len(line)
    while index < length:
        if line[index] in " \t":
            index += 1
            continue
        if line[index] == '"':
            index += 1
            chars: list[str] = []
            while index < length:
                if line[index] == "\\" and index + 1 < length:
                    chars.append(line[index + 1])
                    index += 2
                    continue
                if line[index] == '"':
                    index += 1
                    break
                chars.append(line[index])
                index += 1
            tokens.append("".join(chars))
            continue
        if line[index] == "(":
            depth = 0
            start = index
            while index < length:
                if line[index] == "(":
                    depth += 1
                elif line[index] == ")":
                    depth -= 1
                    index += 1
                    if depth == 0:
                        break
                    continue
                index += 1
            tokens.append(line[start:index])
            continue
        start = index
        while index < length and line[index] not in " \t":
            index += 1
        tokens.append(line[start:index])
    return tokens


def _server_context(server: _Service) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(server.certfile, server.keyfile)
    return context


def _secret_equal(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def _canonical(folder: str, folders: dict[str, list[StoredMessage]]) -> str:
    if folder.upper() == "INBOX":
        return "INBOX"
    for name in folders:
        if name == folder:
            return name
    return folder


def _exists(folder: str, folders: dict[str, list[StoredMessage]]) -> bool:
    if folder.upper() == "INBOX":
        return "INBOX" in folders
    return folder in folders


def _internaldate(raw: bytes) -> datetime:
    header = raw.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
    for line in header.splitlines():
        if line.lower().startswith("date:"):
            try:
                parsed = parsedate_to_datetime(line.split(":", 1)[1].strip())
            except (TypeError, ValueError, IndexError, OverflowError):
                break
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
    return datetime.now(timezone.utc)


def _format_internaldate(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return (
        f"{when.day:02d}-{months[when.month - 1]}-{when.year} "
        f"{when.hour:02d}:{when.minute:02d}:{when.second:02d} +0000"
    )
