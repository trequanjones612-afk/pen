"""Command line entry point for the ``mailcli`` console script."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mailcli import __version__
from mailcli.config import Settings, default_config_path, list_account_names, load_settings
from mailcli.errors import MailcliError
from mailcli.imap_client import DEFAULT_LIMIT, ImapSession, verify_login
from mailcli.listing import Listing, load_listing, save_listing, same_account
from mailcli.interactive import run_interactive
from mailcli.mimeutil import format_links
from mailcli.pager import display
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
                verify=args.verify,
            )
        if args.command == "accounts":
            return _cmd_accounts(args)
        _offer_setup(args)
        if args.command in (None, "shell"):
            settings = _settings(args, folder=None)
            plain = _plain(args)
            return run_interactive(settings, plain=plain)
        settings = _settings(args, folder=getattr(args, "folder", None))
        plain = _plain(args)
        if args.command == "test-login":
            _cmd_test_login(settings)
        elif args.command == "inbox":
            limit, offset = _window(args)
            _cmd_inbox(settings, limit=limit, offset=offset, plain=plain)
        elif args.command == "read":
            _cmd_read(settings, args)
        elif args.command == "send":
            _cmd_send(settings, args)
        elif args.command == "folders":
            _cmd_folders(settings)
        elif args.command == "search":
            limit, _offset = _window(args)
            _cmd_search(settings, args.text, limit=limit, plain=plain)
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
    setup.add_argument(
        "--verify",
        action="store_true",
        help="log in before saving, and save only when the server accepts it",
    )

    sub.add_parser("accounts", help="list saved accounts")
    sub.add_parser("shell", help="open the interactive prompt and keep the connection")
    sub.add_parser("test-login", help="log in with the saved account and report the server's answer")

    inbox = sub.add_parser("inbox", help="list the newest messages")
    _add_window(inbox)
    inbox.add_argument("--folder", help="folder to list")

    read = sub.add_parser("read", help="show one message by its row number in the last listing")
    read.add_argument("number", type=int, help="row number from the last inbox or search")
    _add_window(read, remembered=True)
    read.add_argument("--folder", help="folder to read from")
    read.add_argument("--html", action="store_true", help="show the HTML part even when a text part exists")
    read.add_argument("--links", action="store_true", help="also print the numbered links")
    read.add_argument("--full", action="store_true", help="with --links, do not shorten long URLs")

    send = sub.add_parser("send", help="send a message through SMTP")
    send.add_argument("--to", action="append", required=True, help="recipient; repeat for several")
    send.add_argument("--subject", required=True)
    send.add_argument("--body", help="message body. Otherwise use --body-file or stdin")
    send.add_argument("--body-file", help="read the body from this file")

    sub.add_parser("folders", help="list mailbox folders")

    search = sub.add_parser("search", help="search the current folder")
    search.add_argument("text", help="words to find in the message")
    search.add_argument("--folder", help="folder to search")
    _add_window(search)
    return parser


def _add_window(parser: argparse.ArgumentParser, *, remembered: bool = False) -> None:
    if remembered:
        parser.add_argument("--limit", type=int, default=None, help="fetch a new window of this size instead of the last listing")
        parser.add_argument("--page", type=int, default=None, help="page of a new window, starting at 1")
        parser.add_argument("--offset", type=int, default=None, help="skip this many newest messages in a new window")
        return
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="how many messages to fetch (default 20)")
    parser.add_argument("--page", type=int, default=1, help="page of results, starting at 1")
    parser.add_argument("--offset", type=int, default=None, help="skip this many newest messages")


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


def _cmd_test_login(settings: Settings) -> None:
    verify_login(settings)
    print(f"Login OK for {settings.user} at {settings.host}:{settings.imap_port}.")


def _cmd_inbox(settings: Settings, *, limit: int, offset: int, plain: bool) -> None:
    shown, exists = _load(settings, limit=limit, offset=offset)
    _remember(settings, shown, exists, limit=limit, offset=offset, mode="inbox", query="")
    if exists > len(shown):
        title = f"{settings.folder}  showing {len(shown)} of {exists:,}  newest first"
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


def _cmd_read(settings: Settings, args: argparse.Namespace) -> None:
    number = args.number
    explicit = args.limit is not None or args.page is not None or args.offset is not None
    cached = None if explicit else load_listing()
    if (
        cached is not None
        and same_account(cached, settings.account, settings.folder)
        and cached.uids
    ):
        if number < 1 or number > len(cached.uids):
            raise MailcliError(
                f"No message {number} in the last listing ({len(cached.uids)} rows). "
                "Run inbox again or pass --limit to choose a different window."
            )
        with ImapSession(settings) as session:
            summary, parsed, total = session.read_uid(
                settings.folder,
                cached.uids[number - 1],
                index=number,
                exists=cached.exists,
                prefer_html=args.html,
            )
        _show_message(settings, f"Message {number} of {total:,}  {settings.folder}", parsed, args)
        return
    limit, offset = _window(args, fallback=True)
    with ImapSession(settings) as session:
        summary, parsed, total = session.read(
            settings.folder, number, limit=limit, offset=offset, prefer_html=args.html
        )
    _show_message(settings, f"Message {summary.index} of {total:,}  {settings.folder}", parsed, args)


def _show_message(settings: Settings, title: str, parsed, args: argparse.Namespace) -> None:
    text = render_message(title, parsed)
    if args.links:
        text += "\n" + format_links(parsed.links, full=args.full) + "\n"
    display(text, enabled=settings.pager)


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
        found, exists = session.search(settings.folder, text, limit=limit)
    _remember(settings, found, exists, limit=limit, offset=0, mode="search", query=text)
    title = (
        f'Search "{text}" in {settings.folder}  {len(found)} match'
        + ("es" if len(found) != 1 else "")
    )
    print(
        render_table(
            [_as_row(item) for item in found],
            title=title,
            plain=plain,
            empty=f'No messages in {settings.folder} matched "{text}".',
        ),
        end="",
    )


def _load(settings: Settings, *, limit: int, offset: int):
    with ImapSession(settings) as session:
        return session.summaries(settings.folder, limit=limit, offset=offset)


def _window(args: argparse.Namespace, *, fallback: bool = False) -> tuple[int, int]:
    limit = args.limit if args.limit is not None else (DEFAULT_LIMIT if fallback else args.limit)
    if limit is None or limit <= 0:
        raise MailcliError("--limit must be a positive number.")
    if args.offset is not None:
        if args.offset < 0:
            raise MailcliError("--offset cannot be negative.")
        return limit, args.offset
    page = args.page if args.page is not None else (1 if fallback else args.page)
    if page is None or page < 1:
        raise MailcliError("--page starts at 1.")
    return limit, (page - 1) * limit


def _remember(settings: Settings, rows, exists: int, *, limit: int, offset: int, mode: str, query: str) -> None:
    save_listing(
        Listing(
            account=settings.account,
            folder=settings.folder,
            limit=limit,
            offset=offset,
            mode=mode,
            query=query,
            exists=exists,
            uids=[item.uid for item in rows],
        )
    )


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
