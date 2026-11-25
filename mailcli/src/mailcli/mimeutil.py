"""Decode RFC 5322 messages into text a terminal can print.

Headers may use RFC 2047 encoded words. Bodies may be nested multiparts with
their own charsets. Attachments are listed, not dumped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email import policy
from html.parser import HTMLParser
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime


@dataclass
class Attachment:
    """A non-body MIME part."""

    filename: str
    content_type: str
    size: int


@dataclass
class ParsedMessage:
    """The fields ``read`` prints."""

    sender: str
    to: str
    cc: str
    subject: str
    date_raw: str
    date: datetime | None
    body: str
    html: str
    attachments: list[Attachment]
    html_only: bool


def decode_words(value: str | None) -> str:
    """Decode RFC 2047 encoded words. Unknown charsets are replaced, not raised."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        parts: list[str] = []
        for data, charset in decode_header(value):
            if isinstance(data, bytes):
                parts.append(data.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(data)
        return "".join(parts)


def parse_date(value: str | None) -> datetime | None:
    """Parse an RFC 2822 date. Return None when the header is missing or broken."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def parse_message(raw: bytes) -> ParsedMessage:
    """Parse a full RFC 5322 message, including nested multiparts."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    sender = decode_words(message.get("From"))
    to = _joined_header(message, "To")
    cc = _joined_header(message, "Cc")
    subject = decode_words(message.get("Subject"))
    date_raw = decode_words(message.get("Date"))
    plain, html, attachments = _walk(message)
    html_only = not plain and bool(html)
    html_source = "\n".join(part for part in html if part.strip())
    if plain:
        body = "\n\n".join(part.strip() for part in plain if part.strip())
    elif html:
        body = "\n\n".join(html_to_text(part) for part in html if part.strip())
    else:
        body = ""
    return ParsedMessage(
        sender=sender,
        to=to,
        cc=cc,
        subject=subject,
        date_raw=date_raw,
        date=parse_date(message.get("Date")),
        body=body,
        html=html_source,
        attachments=attachments,
        html_only=html_only,
    )


def header_bytes(raw: bytes) -> bytes:
    """Return the header block of a raw message, including the separating blank line."""
    separator = raw.find(b"\r\n\r\n")
    if separator == -1:
        separator = raw.find(b"\n\n")
        if separator == -1:
            return raw + (b"\r\n" if raw.endswith(b"\n") else b"")
        return raw[: separator + 2]
    return raw[: separator + 4]


class _HTMLText(HTMLParser):
    """Turn HTML into text and remember links. Script and style are skipped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in {"script", "style"}:
            self._skip += 1
            return
        if name == "br":
            self.parts.append("\n")
        elif name in {"p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "li"}:
            self.parts.append("\n")
        if name == "li":
            self.parts.append("- ")
        if name == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if name == "a" and self._href:
            link = self._href.strip()
            if link and link not in self.links:
                self.links.append(link)
            self._href = None
        if name == "p":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """Turn an HTML part into readable text and list links at the end."""
    parser = _HTMLText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return _html_fallback(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if parser.links:
        lines = ["", "Links:"]
        lines.extend(f"  {index}. {link}" for index, link in enumerate(parser.links, 1))
        text = (text + "\n" + "\n".join(lines)).strip()
    return text


def _html_fallback(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def format_size(size: int) -> str:
    """Format a byte count for the attachment list."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _joined_header(message: Message, name: str) -> str:
    values = message.get_all(name, [])
    decoded = [decode_words(value) for value in values if value]
    return ", ".join(decoded)


def _walk(message: Message) -> tuple[list[str], list[str], list[Attachment]]:
    plain: list[str] = []
    html: list[str] = []
    attachments: list[Attachment] = []
    for part in _parts(message):
        content_type = part.get_content_type()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if filename:
            filename = decode_words(filename)
        is_body = (
            disposition != "attachment"
            and content_type in {"text/plain", "text/html"}
            and not (filename and disposition == "attachment")
        )
        if is_body and content_type == "text/plain":
            plain.append(_text(part))
            continue
        if is_body and content_type == "text/html":
            html.append(_text(part))
            continue
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            Attachment(
                filename=filename or "unnamed",
                content_type=content_type,
                size=len(payload),
            )
        )
    return plain, html, attachments


def _parts(message: Message) -> list[Message]:
    if message.get_content_maintype() == "multipart":
        found: list[Message] = []
        payload = message.get_payload()
        if isinstance(payload, list):
            for child in payload:
                if isinstance(child, Message):
                    found.extend(_parts(child))
        return found
    return [message]


def _text(part: Message) -> str:
    if isinstance(part, EmailMessage):
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, KeyError, ValueError):
            content = None
        if isinstance(content, str):
            return content
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")
