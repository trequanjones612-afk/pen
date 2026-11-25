"""IMAP modified UTF-7 (RFC 3501) for mailbox names.

ASCII mailbox names, including INBOX, pass through unchanged. Other names are
shifted into UTF-16BE and a modified base64 alphabet between ``&`` and ``-``.
"""

from __future__ import annotations

import base64


def encode(text: str) -> str:
    """Encode a Unicode mailbox name as IMAP modified UTF-7."""
    pieces: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        raw = "".join(pending).encode("utf-16-be")
        token = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        pieces.append("&" + token + "-")
        pending.clear()

    for char in text:
        if char == "&":
            flush()
            pieces.append("&-")
        elif 0x20 <= ord(char) <= 0x7E:
            flush()
            pieces.append(char)
        else:
            pending.append(char)
    flush()
    return "".join(pieces)


def decode(text: str) -> str:
    """Decode an IMAP modified UTF-7 mailbox name."""
    pieces: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "&":
            pieces.append(text[index])
            index += 1
            continue
        index += 1
        if index < length and text[index] == "-":
            pieces.append("&")
            index += 1
            continue
        start = index
        while index < length and text[index] != "-":
            index += 1
        encoded = text[start:index].replace(",", "/")
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        try:
            raw = base64.b64decode(encoded + padding)
            pieces.append(raw.decode("utf-16-be"))
        except (ValueError, UnicodeDecodeError):
            pieces.append("&" + text[start:index])
        if index < length and text[index] == "-":
            index += 1
    return "".join(pieces)
