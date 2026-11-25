"""Command line entry point for the ``mailcli`` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mailcli import __version__
from mailcli.config import Settings, default_config_path, list_account_names, load_settings
from mailcli.errors import MailcliError
from mailcli.imap_client import ImapSession
from mailcli.interactive import run_interactive
from mailcli.render import Row, render_message, render_table
from mailcli.smtp_client import send_message


def main(argv: list[str] | None = None) -> int:
    """Run mailcli and return a process status code."""
    _prefer_utf8_stdio()
    try:
        args = _parser().parse_args(argv)
        if args.command == "setup":
            from mailcli.setup import run_setup

            return run_setup(
                config_path=Path(args.config).expanduser() if args.config else None,
                provider=args.provider,
                user=getattr(args, "setup_user", None) or args.user,
                account=args.account_name or args.account,
                oauth_client_id=args.oauth_client_id,
                oauth_tenant=args.oauth_tenant,
            )
        if args.command == "accounts":
            return _cmd_accounts(args)
        _offer_setup(args)
        if args.command is None:
            settings = _settings(args, folder=None)
            plain = _plain(args)
            return run_interactive(settings, plain=plain)
        settings = _settings(args, folder=getattr(args, "folder", None))
        plain = _plain(args)
        if args.command == "inbox":
            _cmd_inbox(settings, limit=args.limit, plain=plain)
        elif args.command == "read":
            _cmd_read(settings, args.number)
        elif args.command == "send":
            _cmd_send(settings, args)
        elif args.command == "folders":
            _cmd_folders(settings)
        elif args.command == "search":
            _cmd_search(settings, args.text, limit=args.limit, plain=plain)
        else:
            raise MailcliError(f"Unknown command {args.command}.")
        return 0
    except MailcliError as exc:
        print(f"mailcli: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailcli",
        description="Terminal email. Passwords and tokens stay in the OS credential store. MAILCLI_PASSWORD overrides a stored password.",
    )
    parser.add_argument("--version", action="version", version=f"mailcli {__version__}")
    parser.add_argument(
        "--config",
        help="config file (TOML or INI). Default: %%APPDATA%%\\mailcli\\config on Windows, ~/.config/mailcli/config elsewhere",
    )
    parser.add_argument("--account", help="account name from mailcli setup")
    parser.add_argument("--host", help="IMAP host (overrides the account)")
    parser.add_argument("--imap-port", type=int, help="IMAP port (default 1143)")
    parser.add_argument("--smtp-port", type=int, help="SMTP port (default 1025)")
    parser.add_argument("--user", help="mailbox login, usually your Proton address")
    parser.add_argument("--folder", help="mailbox to open (default from config, or INBOX)")
    parser.add_argument("--tls-cert", help="PEM file to trust, such as a certificate exported from Bridge")
    parser.add_argument(
        "--security",
        choices=("starttls", "ssl"),
        help="starttls (default, used by Proton Mail Bridge) or ssl for implicit TLS",
    )
    parser.add_argument(
        "--insecure-localhost",
        action="store_true",
        help="skip TLS verification; allowed only for 127.0.0.1, localhost, and ::1",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="print aligned columns even when rich is installed",
    )
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="choose a provider and save an account")
    setup.add_argument("--provider", help="gmail, outlook, yahoo, icloud, proton, or custom")
    setup.add_argument("--user", dest="setup_user", help="email address for the new account")
    setup.add_argument("--account-name", help="short name used with --account")
    setup.add_argument("--oauth-client-id", help="OAuth client id. Outlook has a default; Gmail requires yours")
    setup.add_argument("--oauth-tenant", help="Microsoft tenant, default consumers for personal accounts")

    sub.add_parser("accounts", help="list saved accounts")

    inbox = sub.add_parser("inbox", help="list messages, newest first")
    inbox.add_argument("--limit", type=int, default=25, help="how many rows to print (default 25)")
    inbox.add_argument("--folder", help="folder to list")

    read = sub.add_parser("read", help="show one message by its inbox number")
    read.add_argument("number", type=int, help="row number from inbox (1 is the newest)")
    read.add_argument("--folder", help="folder to read from")

    send = sub.add_parser("send", help="send a message through SMTP")
    send.add_argument("--to", action="append", required=True, help="recipient; repeat for several")
    send.add_argument("--subject", required=True)
    send.add_argument("--body", help="message body. Otherwise use --body-file or stdin")
    send.add_argument("--body-file", help="read the body from this file")

    sub.add_parser("folders", help="list mailbox folders")

    search = sub.add_parser("search", help="search the current folder")
    search.add_argument("text", help="words to find in the message")
    search.add_argument("--folder", help="folder to search")
    search.add_argument("--limit", type=int, default=50, help="how many rows to print (default 50)")
    return parser


def _settings(args: argparse.Namespace, folder: str | None) -> Settings:
    chosen = folder or args.folder
    return load_settings(
        config_path=args.config,
        account=args.account,
        host=args.host,
        imap_port=args.imap_port,
        smtp_port=args.smtp_port,
        user=args.user,
        folder=chosen,
        tls_cert=args.tls_cert,
        security=args.security,
        insecure_localhost=args.insecure_localhost,
    )


def _offer_setup(args: argparse.Namespace) -> None:
    """Start the wizard when this machine has no config yet."""
    if args.user or args.host:
        return
    path = Path(args.config).expanduser() if args.config else default_config_path()
    if path.is_file():
        return
    if sys.stdin.isatty() and sys.stdout.isatty():
        from mailcli.setup import run_setup

        print("No mail account is configured. Starting setup.")
        print("")
        code = run_setup(config_path=path)
        if code != 0:
            raise SystemExit(code)
        return
    raise MailcliError("No mail account is configured. Run: python -m mailcli setup")


def _cmd_accounts(args: argparse.Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else default_config_path()
    names, default_name = list_account_names(path)
    if not names:
        print("No accounts yet. Run: python -m mailcli setup")
        return 0
    print(f"Accounts in {path}:")
    for name in names:
        marker = " (default)" if name == default_name else ""
        print(f"  {name}{marker}")
    return 0


def _plain(args: argparse.Namespace) -> bool:
    if args.plain or not sys.stdout.isatty():
        return True
    return False


def _cmd_inbox(settings: Settings, *, limit: int, plain: bool) -> None:
    full = _load(settings)
    shown = _limited(full, limit)
    if len(full) > limit:
        title = f"{settings.folder}  showing {len(shown)} of {len(full)}  newest first"
    else:
        noun = "message" if len(shown) == 1 else "messages"
        title = f"{settings.folder}  {len(shown)} {noun}  newest first"
    print(
        render_table(
            [_as_row(item) for item in shown],
            title=title,
            plain=plain,
            empty=f"{settings.folder} is empty.",
        ),
        end="",
    )


def _cmd_read(settings: Settings, number: int) -> None:
    with ImapSession(settings) as session:
        summary, parsed, total = session.read(settings.folder, number)
    title = f"Message {summary.index} of {total}  {settings.folder}"
    print(render_message(title, parsed), end="")


def _cmd_send(settings: Settings, args: argparse.Namespace) -> None:
    body = _body(args)
    send_message(settings, list(args.to), args.subject, body)
    recipients = ", ".join(item.strip() for item in args.to)
    print(f"Sent to {recipients}.")
    print(f"Subject: {args.subject}")


def _cmd_folders(settings: Settings) -> None:
    with ImapSession(settings) as session:
        names = session.folders()
    if not names:
        print("No folders.")
        return
    print("Folders:")
    for name in names:
        print(f"  {name}")


def _cmd_search(settings: Settings, text: str, *, limit: int, plain: bool) -> None:
    with ImapSession(settings) as session:
        found = session.search(settings.folder, text)
    shown = _limited(found, limit)
    title = (
        f'Search "{text}" in {settings.folder}  {len(found)} match'
        + ("es" if len(found) != 1 else "")
    )
    print(
        render_table(
            [_as_row(item) for item in shown],
            title=title,
            plain=plain,
            empty=f'No messages in {settings.folder} matched "{text}".',
        ),
        end="",
    )


def _load(settings: Settings):
    with ImapSession(settings) as session:
        return session.summaries(settings.folder)


def _limited(rows, limit: int):
    if limit <= 0:
        raise MailcliError("--limit must be a positive number.")
    return list(rows)[:limit]


def _as_row(item) -> Row:
    return Row(item.index, item.when, item.sender, item.subject, item.unread)


def _body(args: argparse.Namespace) -> str:
    if args.body is not None and args.body_file is not None:
        raise MailcliError("Use either --body or --body-file, not both.")
    if args.body is not None:
        return args.body
    if args.body_file is not None:
        path = Path(args.body_file)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MailcliError(f"Could not read {path}: {exc}") from exc
    if sys.stdin.isatty():
        raise MailcliError("Provide --body, --body-file, or pipe the message body on stdin.")
    return sys.stdin.read()


def _prefer_utf8_stdio() -> None:
    if os_name_is_windows():
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is None:
                continue
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def os_name_is_windows() -> bool:
    import os

    return os.name == "nt"
