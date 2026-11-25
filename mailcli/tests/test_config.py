"""Config files, password handling, and the loopback TLS rule."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mailcli.config import config_directory, config_root_text, load_settings
from mailcli.errors import MailcliError
from mailcli.tlsutil import build_ssl_context, is_loopback


def test_toml_and_ini_load(tmp_path: Path) -> None:
    toml_path = tmp_path / "config"
    toml_path.write_text(
        '\n'.join(
            [
                "# sample",
                'host = "127.0.0.1"',
                "imap_port = 1143",
                "smtp_port = 1025",
                'user = "you@example.com"',
                "tls_verify = false",
                'folder = "INBOX"',
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path=str(toml_path), password="from-env")
    assert settings.host == "127.0.0.1"
    assert settings.imap_port == 1143
    assert settings.smtp_port == 1025
    assert settings.user == "you@example.com"
    assert settings.tls_verify is False
    assert settings.password == "from-env"

    ini_path = tmp_path / "config.ini"
    ini_path.write_text(
        "\n".join(
            [
                "[mail]",
                "host = 127.0.0.1",
                "imap_port = 2143",
                "smtp_port = 2025",
                "user = other@example.com",
                "tls_verify = true",
            ]
        ),
        encoding="utf-8",
    )
    ini_settings = load_settings(config_path=str(ini_path), password="other-secret")
    assert ini_settings.user == "other@example.com"
    assert ini_settings.imap_port == 2143
    assert ini_settings.tls_verify is True


def test_password_in_config_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "config"
    path.write_text(
        'host = "127.0.0.1"\nuser = "you@example.com"\npassword = "nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(MailcliError, match="password"):
        load_settings(config_path=str(path), password="ignored")


def test_missing_user(tmp_path: Path) -> None:
    path = tmp_path / "config"
    path.write_text('host = "127.0.0.1"\n', encoding="utf-8")
    with pytest.raises(MailcliError, match="No mail account"):
        load_settings(config_path=str(path), password="x")


def test_cli_overrides(tmp_path: Path) -> None:
    path = tmp_path / "config"
    path.write_text(
        'host = "127.0.0.1"\nimap_port = 1143\nsmtp_port = 1025\nuser = "you@example.com"\n',
        encoding="utf-8",
    )
    settings = load_settings(
        config_path=str(path),
        host="localhost",
        imap_port=3143,
        user="override@example.com",
        password="x",
    )
    assert settings.host == "localhost"
    assert settings.imap_port == 3143
    assert settings.user == "override@example.com"


def test_windows_and_xdg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mailcli.config.os.name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert config_root_text() == os.path.join(str(tmp_path / "Roaming"), "mailcli")

    monkeypatch.setattr("mailcli.config.os.name", "posix")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_directory() == tmp_path / "xdg" / "mailcli"


def test_password_prompt_requires_a_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILCLI_PASSWORD", raising=False)
    monkeypatch.setattr("mailcli.config.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("mailcli.config.default_config_path", lambda: tmp_path / "missing")
    with pytest.raises(MailcliError, match="No stored password found in the credential store for account default"):
        try:
            load_settings(user="you@example.com")
        except MailcliError as exc:
            assert "MAILCLI_PASSWORD" not in str(exc)
            raise


def test_relative_cert_path_is_next_to_the_config(tmp_path: Path) -> None:
    cert = tmp_path / "bridge.pem"
    cert.write_text("not-read-yet\n", encoding="utf-8")
    path = tmp_path / "config"
    path.write_text(
        'host = "127.0.0.1"\nuser = "you@example.com"\ntls_cert = "bridge.pem"\n',
        encoding="utf-8",
    )
    settings = load_settings(config_path=str(path), password="x")
    assert settings.tls_cert == str(cert.resolve())


def test_security_is_starttls_or_ssl(tmp_path: Path) -> None:
    path = tmp_path / "config"
    path.write_text(
        'host = "127.0.0.1"\nuser = "you@example.com"\nsecurity = "ssl"\n',
        encoding="utf-8",
    )
    assert load_settings(config_path=str(path), password="x").security == "ssl"
    path.write_text(
        'host = "127.0.0.1"\nuser = "you@example.com"\nsecurity = "clear"\n',
        encoding="utf-8",
    )
    with pytest.raises(MailcliError, match="security"):
        load_settings(config_path=str(path), password="x")


def test_insecure_tls_is_loopback_only() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("LocalHost")
    assert is_loopback("::1")
    assert not is_loopback("imap.example.com")
    with pytest.raises(MailcliError, match="Refusing to disable TLS"):
        build_ssl_context(
            "imap.example.com",
            tls_verify=False,
            tls_cert=None,
            insecure_localhost=False,
        )
    with pytest.raises(MailcliError, match="Refusing to disable TLS"):
        build_ssl_context(
            "mail.example.com",
            tls_verify=True,
            tls_cert=None,
            insecure_localhost=True,
        )
    context = build_ssl_context(
        "127.0.0.1",
        tls_verify=False,
        tls_cert=None,
        insecure_localhost=False,
    )
    import ssl

    assert context.verify_mode == ssl.CERT_NONE


def test_env_does_not_override_an_explicit_password_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mailcli.config.default_config_path", lambda: tmp_path / "missing")
    monkeypatch.setenv("MAILCLI_USER", "env@example.com")
    monkeypatch.setenv("MAILCLI_HOST", "localhost")
    settings = load_settings(password="kept")
    assert settings.user == "env@example.com"
    assert settings.host == "localhost"
    assert settings.password == "kept"
    assert "MAILCLI_PASSWORD" not in os.environ or settings.password != os.environ.get("password")
