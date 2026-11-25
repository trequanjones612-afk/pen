"""End-to-end inbox, read, send, and search against the in-process servers."""

from __future__ import annotations

import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from mailcli.cli import main
from mailcli.errors import MailcliError
from mailcli.tlsutil import build_ssl_context


def test_xoauth2_inbox_and_send(mail_env, capsys, monkeypatch, tmp_path) -> None:
    token = "mailbox-access-token"
    mail_env["imap"].httpd.access_token = token
    mail_env["smtp"].httpd.access_token = token
    path = tmp_path / "oauth-config"
    path.write_text(
        "\n".join(
            [
                'default_account = "live"',
                "",
                "[accounts.live]",
                'provider = "outlook"',
                f'user = "{mail_env["user"]}"',
                'imap_host = "127.0.0.1"',
                f"imap_port = {mail_env['imap_port']}",
                'imap_security = "starttls"',
                'smtp_host = "127.0.0.1"',
                f"smtp_port = {mail_env['smtp_port']}",
                'smtp_security = "starttls"',
                'auth = "xoauth2"',
                'oauth_tenant = "consumers"',
                "tls_verify = false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    def fake_get(_account: str, kind: str) -> str | None:
        if kind == "access":
            return token
        if kind == "access_expires":
            return "9999999999"
        if kind == "refresh":
            return "refresh-token"
        return None

    monkeypatch.setattr("mailcli.oauth_session.get_secret", fake_get)
    monkeypatch.setattr("mailcli.oauth_session.set_secret", lambda *_args: None)
    assert main(["--config", str(path), "--plain", "inbox"]) == 0
    assert "Project kickoff" in capsys.readouterr().out
    assert (
        main(
            [
                "--config",
                str(path),
                "send",
                "--to",
                mail_env["user"],
                "--subject",
                "OAuth delivery",
                "--body",
                "Sent with XOAUTH2.",
            ]
        )
        == 0
    )
    assert token not in capsys.readouterr().out
    assert main(["--config", str(path), "--plain", "search", "XOAUTH2"]) == 0
    assert "OAuth delivery" in capsys.readouterr().out


def test_module_help() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [sys.executable, "-m", "mailcli", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "inbox" in result.stdout
    assert "MAILCLI_PASSWORD" in result.stdout


def test_inbox_read_search_and_folders(mail_env, capsys) -> None:
    config = str(mail_env["config"])
    assert main(["--config", config, "--plain", "inbox"]) == 0
    inbox = capsys.readouterr().out
    assert "Project kickoff notes" in inbox
    assert "Question about invoice 1842" in inbox
    assert "Build failed on main — auth tests" in inbox
    assert "1  *  2026-11-25 09:14" in inbox
    helena = next(line for line in inbox.splitlines() if "design review" in line)
    assert "*" not in helena
    assert "Welcome to the lab list" in inbox

    assert main(["--config", config, "--plain", "read", "1"]) == 0
    read = capsys.readouterr().out
    assert "Maya Chen <maya.chen@example.com>" in read
    assert "harbor telemetry project" in read
    assert "agenda.txt" in read
    assert "text/plain" in read

    assert main(["--config", config, "--plain", "inbox"]) == 0
    after = capsys.readouterr().out
    first = next(line for line in after.splitlines() if "Project kickoff" in line)
    assert "*" not in first

    assert main(["--config", config, "--plain", "read", "6"]) == 0
    html = capsys.readouterr().out
    assert "HTML-only" in html
    assert "scheduling and outages" in html
    assert "<p>" not in html

    assert main(["--config", config, "--plain", "search", "invoice"]) == 0
    found = capsys.readouterr().out
    assert "invoice 1842" in found
    assert "Project kickoff" not in found
    assert "2  " in found

    assert main(["--config", config, "--plain", "search", "café"]) == 0
    cafe = capsys.readouterr().out
    assert "auth tests" in cafe

    assert main(["--config", config, "--plain", "search", "no-such-phrase"]) == 0
    assert "No messages" in capsys.readouterr().out

    assert main(["--config", config, "folders"]) == 0
    folders = capsys.readouterr().out
    assert "INBOX" in folders
    assert "Sent" in folders

    assert main(["--config", config, "--plain", "inbox", "--limit", "2"]) == 0
    limited = capsys.readouterr().out
    assert "showing 2 of 6" in limited
    assert "Project kickoff" in limited
    assert "invoice 1842" in limited
    assert "ridge hike" not in limited

    assert main(["--config", config, "read", "9"]) == 1
    assert "No message 9" in capsys.readouterr().err


def test_send_lands_in_the_inbox(mail_env, capsys, tmp_path: Path) -> None:
    config = str(mail_env["config"])
    assert (
        main(
            [
                "--config",
                config,
                "send",
                "--to",
                mail_env["user"],
                "--subject",
                "Demo ping from mailcli",
                "--body",
                "The relay answered on the first try.",
            ]
        )
        == 0
    )
    sent = capsys.readouterr().out
    assert "Sent to demo@localhost." in sent
    assert mail_env["password"] not in sent

    body_file = tmp_path / "note.txt"
    body_file.write_text("Body from a file.\n", encoding="utf-8")
    assert (
        main(
            [
                "--config",
                config,
                "send",
                "--to",
                "desk@example.com",
                "--subject",
                "Notes attached in spirit",
                "--body-file",
                str(body_file),
            ]
        )
        == 0
    )

    assert main(["--config", config, "--plain", "inbox"]) == 0
    inbox = capsys.readouterr().out
    assert "Notes attached in spirit" in inbox
    assert "Demo ping from mailcli" in inbox
    number = _row_number(inbox, "Demo ping from mailcli")

    assert main(["--config", config, "read", number]) == 0
    opened = capsys.readouterr().out
    assert "The relay answered on the first try." in opened


def test_send_body_from_stdin(mail_env, capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO("piped body\n"))
    assert (
        main(
            [
                "--config",
                str(mail_env["config"]),
                "send",
                "--to",
                mail_env["user"],
                "--subject",
                "From stdin",
            ]
        )
        == 0
    )
    assert main(["--config", str(mail_env["config"]), "search", "piped"]) == 0
    assert "From stdin" in capsys.readouterr().out


def test_wrong_password_does_not_leak(mail_env, capsys, monkeypatch) -> None:
    secret = "s3cret-token-should-not-print"
    monkeypatch.setenv("MAILCLI_PASSWORD", secret)
    assert main(["--config", str(mail_env["config"]), "inbox"]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "Login failed" in captured.err


def test_trusted_certificate(mail_env, capsys) -> None:
    context = build_ssl_context(
        "127.0.0.1",
        tls_verify=True,
        tls_cert=str(mail_env["cert"]),
        insecure_localhost=False,
    )
    import ssl

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert (
        main(
            [
                "--config",
                str(mail_env["config"]),
                "--tls-cert",
                str(mail_env["cert"]),
                "--plain",
                "folders",
            ]
        )
        == 0
    )
    # The config file still says tls_verify = false, and a certificate wins,
    # so this also checks that pinning does not fall back to CERT_NONE.
    assert "INBOX" in capsys.readouterr().out


def test_insecure_flag_on_the_command_line(mail_env, capsys, tmp_path: Path) -> None:
    path = tmp_path / "strict"
    path.write_text(
        "\n".join(
            [
                'host = "127.0.0.1"',
                f"imap_port = {mail_env['imap_port']}",
                f"smtp_port = {mail_env['smtp_port']}",
                f"user = \"{mail_env['user']}\"",
                "tls_verify = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert main(["--config", str(path), "folders"]) == 1
    assert "TLS" in capsys.readouterr().err
    assert (
        main(["--config", str(path), "--insecure-localhost", "folders"]) == 0
    )
    assert "INBOX" in capsys.readouterr().out


def test_interactive_open_and_back(mail_env, capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO("1\nb\nq\n"))
    assert main(["--config", str(mail_env["config"]), "--plain"]) == 0
    out = capsys.readouterr().out
    assert "Project kickoff notes" in out
    assert "harbor telemetry project" in out
    assert "agenda.txt" in out


def _row_number(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line:
            return line.split()[0]
    raise AssertionError(needle)


def test_public_host_still_refused_with_a_live_config(mail_env) -> None:
    with pytest.raises(MailcliError, match="Refusing"):
        build_ssl_context(
            "imap.example.com",
            tls_verify=False,
            tls_cert=None,
            insecure_localhost=True,
        )
