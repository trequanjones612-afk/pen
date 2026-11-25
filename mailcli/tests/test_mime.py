"""MIME decoding, mailbox-name encoding, and the plain table."""

from __future__ import annotations

from datetime import datetime, timezone

from mailcli.mimeutil import html_to_text, parse_message
from mailcli.mutf7 import decode, encode
from mailcli.render import Row, render_message, render_table


def test_encoded_words_charset_and_attachment() -> None:
    raw = (
        b"From: Lena <lena@example.com>\r\n"
        b"To: you@example.com\r\n"
        b"Subject: =?iso-8859-1?Q?Caf=E9_notes?=\r\n"
        b"Date: Fri, 20 Nov 2026 07:55:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=bound\r\n"
        b"\r\n"
        b"--bound\r\n"
        b"Content-Type: text/plain; charset=iso-8859-1\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n"
        b"\r\n"
        b"A caf=E9 in the lobby.\r\n"
        b"--bound\r\n"
        b"Content-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"YWI=\r\n"
        b"--bound--\r\n"
    )
    parsed = parse_message(raw)
    assert parsed.subject == "Café notes"
    assert "café" in parsed.body
    assert parsed.attachments[0].filename == "résumé.pdf"
    assert parsed.attachments[0].content_type == "application/pdf"
    assert parsed.attachments[0].size == 2
    rendered = render_message("Message 1 of 1  INBOX", parsed)
    assert "résumé.pdf" in rendered
    assert "application/pdf" in rendered


def test_html_only_message() -> None:
    raw = (
        b"From: News <news@example.com>\r\n"
        b"To: you@example.com\r\n"
        b"Subject: Outage\r\n"
        b"Date: Fri, 20 Nov 2026 07:55:00 +0000\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><p>Power is back.</p><p>The relay is quiet.</p></body></html>\r\n"
    )
    parsed = parse_message(raw)
    assert parsed.html_only
    assert "Power is back." in parsed.body
    assert "relay is quiet." in parsed.body
    assert "<p>" not in parsed.body
    assert "HTML-only" in render_message("Message 1 of 1  INBOX", parsed)
    assert "Power is back." in html_to_text("<p>Power is back.</p>")


def test_modified_utf7_roundtrip() -> None:
    assert encode("INBOX") == "INBOX"
    assert encode("Sent") == "Sent"
    for name in ("Boîte", "Entwürfe", "A & B"):
        assert decode(encode(name)) == name


def test_plain_table_marks_unread() -> None:
    when = datetime(2026, 11, 25, 9, 14, tzinfo=timezone.utc)
    text = render_table(
        [
            Row(1, when, "Maya Chen", "Project kickoff notes", True),
            Row(2, when, "Helena Vogt", "Re: design review", False),
        ],
        title="INBOX  2 messages  newest first",
        plain=True,
        empty="INBOX is empty.",
    )
    assert "Project kickoff notes" in text
    assert "1  *  2026-11-25 09:14" in text
    helena = next(line for line in text.splitlines() if "design review" in line)
    assert "*" not in helena
