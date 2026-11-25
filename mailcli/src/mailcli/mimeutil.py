"""Decode RFC 5322 messages into text a terminal can print.

Headers may use RFC 2047 encoded words. Bodies may be nested multiparts with
their own charsets. Attachments are listed, not dumped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    links: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    html_only: bool = False
    body_source: str = "text"


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


def parse_message(raw: bytes, *, prefer_html: bool = False) -> ParsedMessage:
    """Parse a full RFC 5322 message, including nested multiparts.

    ``text/plain`` wins unless it is a short stub and an HTML part has the
    real content, or ``prefer_html`` is set.
    """
    message = BytesParser(policy=policy.default).parsebytes(raw)
    sender = decode_words(message.get("From"))
    to = _joined_header(message, "To")
    cc = _joined_header(message, "Cc")
    subject = decode_words(message.get("Subject"))
    date_raw = decode_words(message.get("Date"))
    plain, html_parts, attachments = _walk(message)
    html_source = "\n".join(part for part in html_parts if part.strip())
    rendered = render_html(html_source) if html_source else HtmlText("", [])
    plain_body = "\n\n".join(part.strip() for part in plain if part.strip())
    use_html = bool(html_source) and (prefer_html or not plain_body or _plain_is_stub(plain_body, rendered.text))
    if use_html:
        body = rendered.text
        source = "html"
    else:
        body = plain_body
        source = "text"
    return ParsedMessage(
        sender=sender,
        to=to,
        cc=cc,
        subject=subject,
        date_raw=date_raw,
        date=parse_date(message.get("Date")),
        body=body,
        html=html_source,
        links=list(rendered.links),
        attachments=attachments,
        html_only=use_html and not plain_body,
        body_source=source,
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


@dataclass
class HtmlText:
    """Readable text plus the links referenced by ``[n]`` markers."""

    text: str
    links: list[str]


_INVISIBLE = dict.fromkeys(
    map(ord, "\u034f\u200b\u200c\u200d\u00ad\ufeff\u2060\u180e\u2061\u2062\u2063\u2064"),
    None,
)
_UNICODE_SPACES = dict.fromkeys(
    map(ord, "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"),
    " ",
)
_SKIP_TAGS = {"script", "style", "head", "title", "noscript"}
_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "section"}
_VOID_TAGS = {"br", "img", "hr", "meta", "link", "area", "base", "col", "embed", "source", "wbr"}
_SPACER_WORDS = {"spacer", "pixel", "blank", "image", "img", "logo spacer", "none"}


class _HTMLText(HTMLParser):
    """Turn marketing HTML into lines of text and a short link table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._link_ids: dict[str, int] = {}
        self._skip = 0
        self._anchor: int | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if self._skip:
            if name not in _VOID_TAGS:
                self._skip += 1
            return
        if name in _SKIP_TAGS or _is_hidden(name, attributes):
            if name not in _VOID_TAGS:
                self._skip = 1
            return
        if name == "img":
            self._image(attributes)
            return
        if name == "br":
            self._emit("\n")
            return
        if name == "tr":
            self._close_cell()
            self._row = []
            return
        if name in {"td", "th"}:
            self._close_cell()
            self._cell = []
            return
        if name in _BLOCK_TAGS:
            self._emit("\n")
        if name == "li":
            self._emit("- ")
        if name == "a":
            href = attributes.get("href", "").strip()
            number = self._remember(href)
            self._anchor = number

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if self._skip:
            if name not in _VOID_TAGS:
                self._skip -= 1
            return
        if name == "a" and self._anchor:
            self._emit(f" [{self._anchor}]")
            self._anchor = None
            return
        if name in {"td", "th"}:
            self._close_cell()
            return
        if name == "tr":
            self._close_row()
            return
        if name in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        self._emit(data)

    def result(self) -> HtmlText:
        self._close_row()
        text = _cleanup("".join(self.parts))
        return HtmlText(text=text, links=list(self.links))

    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        else:
            self.parts.append(text)

    def _close_cell(self) -> None:
        if self._cell is None:
            return
        cleaned = _cleanup("".join(self._cell)).replace("\n", " ")
        cleaned = re.sub(r" {2,}", " ", cleaned).strip()
        if self._row is not None and cleaned and not _is_separator(cleaned):
            self._row.append(cleaned)
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is None:
            return
        if self._row:
            self.parts.append("\n" + " | ".join(self._row) + "\n")
        self._row = None

    def _remember(self, href: str) -> int | None:
        target = href.strip()
        if not target or target == "#" or target.lower().startswith("javascript:"):
            return None
        key = target.lower()
        existing = self._link_ids.get(key)
        if existing:
            return existing
        self.links.append(target)
        number = len(self.links)
        self._link_ids[key] = number
        return number

    def _image(self, attributes: dict[str, str]) -> None:
        alt = _normalize_spaces(attributes.get("alt", "")).strip()
        src = attributes.get("src", "").lower()
        if _is_spacer_image(alt, attributes.get("width", ""), attributes.get("height", ""), src):
            return
        if _meaningful_alt(alt):
            self._emit(f"[{alt}]")


def render_html(html: str) -> HtmlText:
    """Convert HTML to text with ``[n]`` markers and no link dump."""
    if not html.strip():
        return HtmlText("", [])
    parser = _HTMLText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return HtmlText(_cleanup(_html_fallback(html)), [])
    return parser.result()


def html_to_text(html: str) -> str:
    """Readable text for an HTML part. Links stay inline as ``[n]`` markers."""
    return render_html(html).text


def format_links(links: list[str], *, full: bool = False, width: int = 80) -> str:
    """Numbered link list. Long tracking URLs are shortened unless ``full``."""
    if not links:
        return "No links."
    lines = []
    for index, link in enumerate(links, 1):
        shown = link if full or len(link) <= width else link[: width - 3] + "..."
        lines.append(f"{index}. {shown}")
    return "\n".join(lines)


def _plain_is_stub(plain: str, html_text: str) -> bool:
    compact = " ".join(plain.split())
    html_compact = " ".join(html_text.split())
    if len(compact) < 40 and len(html_compact) > 80:
        return True
    lowered = compact.lower()
    phrases = (
        "view this email",
        "view in your browser",
        "view in browser",
        "having trouble viewing",
        "email is best viewed",
    )
    return len(compact) < 220 and any(phrase in lowered for phrase in phrases)


def _is_hidden(tag: str, attributes: dict[str, str]) -> bool:
    style = attributes.get("style", "").lower().replace(" ", "")
    classes = attributes.get("class", "").lower()
    if "display:none" in style or "mso-hide:all" in style or "mso-hide" in classes:
        return True
    if "preheader" in classes or "pre-header" in classes:
        return True
    if attributes.get("aria-hidden", "").lower() == "true" and tag in {"div", "span", "td"}:
        return True
    if attributes.get("hidden", "").lower() in {"", "hidden", "true"}:
        if "hidden" in attributes:
            return True
    return False


def _is_spacer_image(alt: str, width: str, height: str, src: str) -> bool:
    if "spacer" in src or "pixel" in src or src.endswith(".gif") and ("1" == width or "1" == height):
        return True
    if width.strip() in {"0", "1"} or height.strip() in {"0", "1"}:
        return True
    return alt.lower() in _SPACER_WORDS


def _meaningful_alt(alt: str) -> bool:
    if len(alt) < 2 or alt.lower() in _SPACER_WORDS:
        return False
    return any(character.isalpha() for character in alt)


def _normalize_spaces(text: str) -> str:
    return text.translate(_INVISIBLE).translate(_UNICODE_SPACES)


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"[\s\|\-_=*•·.]+", line))


def _cleanup(text: str) -> str:
    text = _normalize_spaces(text)
    raw_lines = text.splitlines()
    lines: list[str] = []
    for raw in raw_lines:
        line = re.sub(r"[ \t]{2,}", " ", raw).strip()
        if not line or _is_separator(line):
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if lines and lines[-1] and re.match(r"^(?:[a-z]|[,.;:!?)\]])", line):
            lines[-1] = f"{lines[-1]} {line}"
            continue
        lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    while lines and lines[0] == "":
        lines.pop(0)
    collapsed: list[str] = []
    for line in lines:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed)


def _html_fallback(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return text


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
