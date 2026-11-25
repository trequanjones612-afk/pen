"""Provider wizard, account selection, and the credential store."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from mailcli.cli import main
from mailcli.config import load_settings
from mailcli.oauth import THUNDERBIRD_CLIENT_ID, device_code_flow, xoauth2_sasl
from mailcli.setup import run_setup


def test_setup_command_accepts_user_after_the_subcommand(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("mailcli.setup.set_secret", lambda *_args: None)
    monkeypatch.setattr("mailcli.setup._hidden", lambda _prompt: "app-password")
    code = main(
        [
            "--config",
            str(tmp_path / "config"),
            "setup",
            "--provider",
            "yahoo",
            "--user",
            "you@yahoo.com",
            "--account-name",
            "yahoo",
        ]
    )
    assert code == 0
    text = (tmp_path / "config").read_text(encoding="utf-8")
    assert "imap.mail.yahoo.com" in text
    assert "you@yahoo.com" in text
    assert "app-password" not in text
    assert "Saved account yahoo" in capsys.readouterr().out


def test_gmail_setup_keeps_the_password_out_of_the_file(tmp_path, monkeypatch) -> None:
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr("mailcli.setup.set_secret", lambda account, kind, value: stored.__setitem__((account, kind), value))
    code = run_setup(
        config_path=tmp_path / "config",
        provider="gmail",
        user="you@gmail.com",
        account="home",
        password_ask=lambda _prompt: "abcd efgh ijkl mnop",
        printer=lambda _text: None,
    )
    assert code == 0
    text = (tmp_path / "config").read_text(encoding="utf-8")
    assert "imap.gmail.com" in text
    assert "imap_port = 993" in text
    assert "smtp.gmail.com" in text
    assert "smtp_port = 465" in text
    assert "ssl" in text
    assert "abcd" not in text
    assert "password" not in text.lower() or "do not" in text.lower() or "Secrets" in text
    assert stored[("home", "password")] == "abcd efgh ijkl mnop"
    settings = load_settings(config_path=str(tmp_path / "config"), password="ignored")
    assert settings.account == "home"
    assert settings.host == "imap.gmail.com"
    assert settings.smtp_host == "smtp.gmail.com"
    assert settings.security == "ssl"
    assert settings.smtp_security == "ssl"
    assert settings.auth == "password"


def test_outlook_setup_uses_device_code_and_stores_the_refresh_token(tmp_path, monkeypatch) -> None:
    stored: dict[tuple[str, str], str] = {}
    seen: dict[str, object] = {}

    def fake_flow(**kwargs):
        seen.update(kwargs)
        return {"access_token": "access-token", "refresh_token": "refresh-token", "expires_in": 120}

    monkeypatch.setattr("mailcli.setup.set_secret", lambda account, kind, value: stored.__setitem__((account, kind), value))
    code = run_setup(
        config_path=tmp_path / "config",
        provider="outlook",
        user="you@outlook.com",
        account="live",
        device_flow=fake_flow,
        printer=lambda _text: None,
    )
    assert code == 0
    text = (tmp_path / "config").read_text(encoding="utf-8")
    assert "outlook.office365.com" in text
    assert "smtp-mail.outlook.com" in text
    assert "starttls" in text
    assert THUNDERBIRD_CLIENT_ID in text
    assert 'oauth_tenant = "consumers"' in text
    assert "refresh-token" not in text
    assert "access-token" not in text
    assert stored[("live", "refresh")] == "refresh-token"
    assert "IMAP.AccessAsUser.All" in str(seen["scope"])
    assert "SMTP.Send" in str(seen["scope"])
    assert "offline_access" in str(seen["scope"])
    assert "/consumers/" in str(seen["device_url"])


def test_proton_setup_explains_and_saves_nothing(tmp_path) -> None:
    lines: list[str] = []
    code = run_setup(
        config_path=tmp_path / "config",
        provider="proton",
        printer=lines.append,
    )
    assert code == 0
    assert not (tmp_path / "config").exists()
    text = "\n".join(lines)
    assert "does not offer IMAP" in text
    assert "SRP" in text
    assert "No Proton account was saved." in text


def test_menu_choice_and_second_account(tmp_path, monkeypatch) -> None:
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr("mailcli.setup.set_secret", lambda account, kind, value: stored.__setitem__((account, kind), value))
    answers = iter(["1", "you@gmail.com", ""])
    assert (
        run_setup(
            config_path=tmp_path / "config",
            ask=lambda _prompt: next(answers),
            password_ask=lambda _prompt: "gmail-app",
            printer=lambda _text: None,
        )
        == 0
    )
    assert (
        run_setup(
            config_path=tmp_path / "config",
            provider="yahoo",
            user="you@yahoo.com",
            account="yahoo",
            make_default=False,
            password_ask=lambda _prompt: "yahoo-app",
            printer=lambda _text: None,
        )
        == 0
    )
    home = load_settings(config_path=str(tmp_path / "config"), password="x")
    other = load_settings(config_path=str(tmp_path / "config"), account="yahoo", password="x")
    assert home.account == "you"
    assert home.host == "imap.gmail.com"
    assert other.host == "imap.mail.yahoo.com"
    assert other.smtp_port == 465
    assert "gmail-app" not in (tmp_path / "config").read_text(encoding="utf-8")


def test_password_env_overrides_the_store(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config"
    path.write_text(
        'host = "127.0.0.1"\nuser = "you@example.com"\nimap_port = 1143\nsmtp_port = 1025\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAILCLI_PASSWORD", "from-env")
    monkeypatch.setattr("mailcli.secrets.get_secret", lambda *_args: "from-store")
    settings = load_settings(config_path=str(path))
    assert settings.password == "from-env"


def test_stored_password_is_used_when_env_is_absent(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config"
    path.write_text(
        'host = "127.0.0.1"\nuser = "you@example.com"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MAILCLI_PASSWORD", raising=False)
    monkeypatch.setattr("mailcli.secrets.get_secret", lambda _account, kind: "stored-secret" if kind == "password" else None)
    settings = load_settings(config_path=str(path))
    assert settings.password == "stored-secret"


def test_accounts_command(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("mailcli.setup.set_secret", lambda *_args: None)
    path = tmp_path / "config"
    assert (
        run_setup(
            config_path=path,
            provider="icloud",
            user="you@icloud.com",
            account="apple",
            password_ask=lambda _prompt: "apple-app",
            printer=lambda _text: None,
        )
        == 0
    )
    assert "imap.mail.me.com" in path.read_text(encoding="utf-8")
    assert main(["--config", str(path), "accounts"]) == 0
    output = capsys.readouterr().out
    assert "apple" in output
    assert "default" in output


def test_device_code_flow_against_a_local_token_server() -> None:
    state = {"polls": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("ascii")
            if self.path.endswith("/devicecode"):
                payload = {
                    "device_code": "device-1",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.microsoft.com/devicelogin",
                    "expires_in": 60,
                    "interval": 0,
                    "message": "Open the page and enter the code",
                }
                status = 200
            else:
                state["polls"] += 1
                if "refresh_token" in body:
                    payload = {"access_token": "refreshed", "expires_in": 30}
                    status = 200
                elif state["polls"] == 1:
                    payload = {"error": "authorization_pending"}
                    status = 400
                else:
                    payload = {"access_token": "fresh", "refresh_token": "keep", "expires_in": 30}
                    status = 200
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    lines: list[str] = []
    try:
        token = device_code_flow(
            client_id="client",
            scope="https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
            device_url=f"http://127.0.0.1:{port}/devicecode",
            token_url=f"http://127.0.0.1:{port}/token",
            printer=lines.append,
            sleep=lambda _seconds: None,
        )
    finally:
        server.shutdown()
        thread.join(timeout=3)
    assert token["access_token"] == "fresh"
    assert token["refresh_token"] == "keep"
    assert any("ABCD-EFGH" in line for line in lines)
    assert "www.microsoft.com/devicelogin" in "\n".join(lines)
    assert "keep" not in "\n".join(lines)
    sasl = xoauth2_sasl("you@outlook.com", "fresh")
    assert sasl == "user=you@outlook.com\x01auth=Bearer fresh\x01\x01"
