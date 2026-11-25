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


def test_html_parser_lists_links_and_skips_scripts() -> None:
    rendered = html_to_text(
        "<html><style>body{color:red}</style><body>"
        "<p>See the <a href='https://example.com/notes'>notes</a>.</p>"
        "<script>alert(1)</script></body></html>"
    )
    assert "See the notes" in rendered
    assert "[1]" in rendered
    assert "alert" not in rendered
    assert "color:red" not in rendered
    assert "Links:" not in rendered


def test_marketing_html_is_compact() -> None:
    from pathlib import Path

    from mailcli.mimeutil import format_links, render_html

    html = Path(__file__).parent.joinpath("fixtures", "marketing.html").read_text(encoding="utf-8")
    rendered = render_html(html)
    lines = rendered.text.splitlines()
    assert lines
    assert all(line.strip() == line and line.strip() for line in lines if line != "")
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            assert blank_run <= 1
        else:
            blank_run = 0
    assert "Preview the sale" not in rendered.text
    assert "Hidden offer code" not in rendered.text
    assert "Black Friday" not in rendered.text
    assert "spacer" not in rendered.text.lower()
    assert "|" not in rendered.text or "Calendars" in rendered.text
    assert "shutterfly.com or the app" in rendered.text
    assert "Holiday Cards [1]" in rendered.text
    assert "https://" not in rendered.text
    assert rendered.links[0].startswith("https://click.example.com/track")
    assert rendered.links.count("mailto:deals@example.com") == 1
    assert "#" not in rendered.links
    short = format_links(rendered.links)
    assert "..." in short
    assert "https%3A%2F%2Fwww.example.com%2Fholiday-cards" not in short
    full = format_links(rendered.links, full=True)
    assert "https%3A%2F%2Fwww.example.com%2Fholiday-cards" in full
    assert len(lines) < 15


def test_plain_stub_falls_back_to_html() -> None:
    raw = (
        b"From: Shop <shop@example.com>\r\n"
        b"To: you@example.com\r\n"
        b"Subject: Sale\r\n"
        b"Date: Fri, 20 Nov 2026 07:55:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=b\r\n"
        b"\r\n"
        b"--b\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"View this email in your browser.\r\n"
        b"--b\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><p>Save 40% on photo books this weekend.</p></body></html>\r\n"
        b"--b--\r\n"
    )
    parsed = parse_message(raw)
    assert parsed.body_source == "html"
    assert "Save 40%" in parsed.body
    forced = parse_message(raw, prefer_html=False)
    assert "Save 40%" in forced.body
    plain = (
        b"From: A <a@example.com>\r\n"
        b"To: you@example.com\r\n"
        b"Subject: Both\r\n"
        b"Date: Fri, 20 Nov 2026 07:55:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=b\r\n"
        b"\r\n"
        b"--b\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"This is the real plain text of the message, long enough to keep.\r\n"
        b"--b\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><p>HTML version only.</p></body></html>\r\n"
        b"--b--\r\n"
    )
    kept = parse_message(plain)
    assert kept.body_source == "text"
    assert "real plain text" in kept.body
    html = parse_message(plain, prefer_html=True)
    assert html.body_source == "html"
    assert "HTML version only." in html.body


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
