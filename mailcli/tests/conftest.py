"""Fixtures: a STARTTLS IMAP and SMTP pair with a few seeded messages."""

from __future__ import annotations

import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakeserver import Mailbox, start_pair, write_certificate  # noqa: E402

USER = "demo@localhost"
PASSWORD = "test-mailbox-password"


@pytest.fixture
def mail_env(tmp_path, monkeypatch):
    cert, key = write_certificate(tmp_path)
    mailbox = Mailbox()
    seed_mailbox(mailbox)
    imap, smtp = start_pair(mailbox, cert, key, USER, PASSWORD)
    config = tmp_path / "config"
    config.write_text(
        "\n".join(
            [
                'host = "127.0.0.1"',
                f"imap_port = {imap.port}",
                f"smtp_port = {smtp.port}",
                f'user = "{USER}"',
                "tls_verify = false",
                'folder = "INBOX"',
                "timeout = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAILCLI_PASSWORD", PASSWORD)
    try:
        yield {
            "config": config,
            "mailbox": mailbox,
            "cert": cert,
            "user": USER,
            "password": PASSWORD,
            "imap_port": imap.port,
            "smtp_port": smtp.port,
        }
    finally:
        smtp.stop()
        imap.stop()


def seed_mailbox(mailbox: Mailbox) -> None:
    """Six messages. Maya is the newest and has an attachment. Helena is read."""
    mailbox.add("INBOX", _message(
        sender="Noah Ibrahim <noah.ibrahim@example.com>",
        to=USER,
        subject="Welcome to the lab list",
        date="Fri, 20 Nov 2026 07:55:00 +0000",
        html=(
            "<html><body><p>Welcome to the lab list.</p>"
            "<p>This address is for scheduling and outages only.</p></body></html>"
        ),
    ))
    mailbox.add(
        "INBOX",
        _message(
            sender="Helena Vogt <helena.vogt@example.com>",
            to=USER,
            subject="Re: design review",
            date="Sat, 21 Nov 2026 15:18:00 +0000",
            body=(
                "Thanks for the review notes. I moved the legend under the plot "
                "and increased the contrast on the unread marker."
            ),
        ),
        flags={"\\Seen"},
    )
    mailbox.add("INBOX", _message(
        sender="Sam Okonkwo <sam.okonkwo@example.com>",
        to=USER,
        subject="Build failed on main — auth tests",
        date="Sun, 22 Nov 2026 08:27:00 +0000",
        body="The overnight build failed because the auth fixture expired before the café case ran.",
    ))
    mailbox.add("INBOX", _message(
        sender="Priya Shah <priya.shah@example.com>",
        to=USER,
        subject="Saturday ridge hike",
        date="Mon, 23 Nov 2026 11:05:00 +0000",
        body="Anyone up for the ridge trail on Saturday? Leave the south lot at 8:15.",
    ))
    mailbox.add("INBOX", _message(
        sender="Luis Ortega <luis.ortega@example.com>",
        to=USER,
        subject="Question about invoice 1842",
        date="Tue, 24 Nov 2026 16:42:00 +0000",
        body="Invoice 1842 still shows the spare pump as back-ordered. Should that line move to December?",
    ))
    mailbox.add("INBOX", _message(
        sender="Maya Chen <maya.chen@example.com>",
        to=USER,
        subject="Project kickoff notes",
        date="Wed, 25 Nov 2026 09:14:00 +0000",
        body=(
            "Hi team,\n\n"
            "The kickoff for the harbor telemetry project is Thursday at 10:00 in the east lab.\n\n"
            "Please read the attached agenda before we meet.\n"
        ),
        attachment=("agenda.txt", "Harbor telemetry kickoff\n1. Goals for the quarter\n"),
    ))


def _message(
    *,
    sender: str,
    to: str,
    subject: str,
    date: str,
    body: str = "",
    html: str | None = None,
    attachment: tuple[str, str] | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = date
    if html is not None:
        message.set_content(html, subtype="html")
    else:
        message.set_content(body)
    if attachment is not None:
        filename, content = attachment
        message.add_attachment(
            content.encode("utf-8"),
            maintype="text",
            subtype="plain",
            filename=filename,
        )
    return bytes(message)


def config_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
