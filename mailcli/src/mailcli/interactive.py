"""Persistent mailcli shell.

One IMAP connection stays open. ``read 3`` opens row 3 of the list on screen,
which is either the inbox page or the last search. PowerShell 5.1 can type
these commands; tab completion and history use readline or pyreadline3 when
that module is installed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import webbrowser
from pathlib import Path

from mailcli import __version__
from mailcli.config import Settings, list_account_names, load_settings
from mailcli.errors import MailcliError
from mailcli.imap_client import DEFAULT_LIMIT, ImapSession, Summary
from mailcli.listing import Listing, history_path, save_listing
from mailcli.mimeutil import format_links
from mailcli.pager import display
from mailcli.render import Row, render_message, render_table
from mailcli.smtp_client import send_message

_COMMANDS = (
    "inbox",
    "ls",
    "next",
    "n",
    "prev",
    "p",
    "read",
    "search",
    "folder",
    "folders",
    "refresh",
    "compose",
    "send",
    "reply",
    "open",
    "links",
    "view",
    "account",
    "accounts",
    "help",
    "clear",
    "quit",
    "exit",
)


def run_interactive(settings: Settings, *, plain: bool) -> int:
    """Log in once and read commands until the user quits."""
    session = ImapSession(settings)
    shell = _Shell(session, plain=plain)
    try:
        session.connect()
        _banner()
        shell.inbox()
        _enable_line_editing()
        while True:
            try:
                typed = input(shell.prompt()).strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                continue
            if not typed:
                continue
            try:
                if shell.handle(typed):
                    return 0
            except MailcliError as exc:
                print(f"mailcli: {exc}")
    except MailcliError as exc:
        print(f"mailcli: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        session.close()


class _Shell:
    def __init__(self, session: ImapSession, *, plain: bool) -> None:
        self.session = session
        self.plain = plain
        self.folder = session.settings.folder or "INBOX"
        self.limit = DEFAULT_LIMIT
        self.offset = 0
        self.mode = "inbox"
        self.query = ""
        self.rows: list[Summary] = []
        self.exists = 0
        self.prefer_html = False

    def prompt(self) -> str:
        account = self.session.settings.account or "mail"
        return f"mailcli [{account} {self.folder}]> "

    def handle(self, typed: str) -> bool:
        """Run one line. Return True when the shell should exit."""
        command, _, rest = typed.partition(" ")
        lowered = command.lower()
        if lowered in {"quit", "exit"}:
            return True
        if lowered in {"help", "?"}:
            _banner()
            return False
        if lowered == "clear":
            os.system("cls" if os.name == "nt" else "clear")
            return False
        if lowered in {"inbox", "ls"}:
            if rest.strip().isdigit():
                self.limit = int(rest.strip())
            self.offset = 0
            self.mode = "inbox"
            self.query = ""
            self.inbox()
            return False
        if lowered in {"next", "n"}:
            self.offset += self.limit
            self.mode = "inbox"
            self.inbox()
            return False
        if lowered in {"prev", "p"}:
            self.offset = max(0, self.offset - self.limit)
            self.mode = "inbox"
            self.inbox()
            return False
        if lowered == "refresh":
            if self.mode == "search" and self.query:
                self.search(self.query)
            else:
                self.inbox()
            return False
        if lowered == "read" or command.isdigit():
            number = int(command) if command.isdigit() else _number(rest)
            self.read(number)
            return False
        if lowered == "open":
            self.open_html(_number(rest))
            return False
        if lowered == "links":
            full = "--full" in rest or rest.strip().endswith(" full")
            number_text = rest.replace("--full", "").replace("full", "").strip()
            self.show_links(_number(number_text), full=full)
            return False
        if lowered == "view":
            choice = rest.strip().lower()
            if choice not in {"html", "text"}:
                print("Usage: view html   or   view text")
                return False
            self.prefer_html = choice == "html"
            print(f"Reading the {choice} part when a message has both.")
            return False
        if lowered == "search":
            text = rest.strip() or _ask("Search: ")
            if text:
                self.search(text)
            return False
        if lowered == "folders":
            self.folders()
            return False
        if lowered == "folder":
            name = rest.strip() or _ask("Folder: ")
            if name:
                self.folder = name
                self.session.settings.folder = name
                self.offset = 0
                self.mode = "inbox"
                self.inbox()
            return False
        if lowered in {"compose", "send"}:
            self.compose(reply_to=None)
            return False
        if lowered == "reply":
            self.compose(reply_to=_number(rest))
            return False
        if lowered == "accounts":
            self.accounts()
            return False
        if lowered == "account":
            name = rest.strip() or _ask("Account: ")
            if name:
                self.switch_account(name)
            return False
        print("Unknown command. Type help.")
        return False

    def inbox(self) -> None:
        rows, exists = self.session.summaries(self.folder, limit=self.limit, offset=self.offset)
        self.rows = rows
        self.exists = exists
        self.mode = "inbox"
        self._show(rows, exists, query="")
        self._remember("")

    def search(self, text: str) -> None:
        rows, exists = self.session.search(self.folder, text, limit=self.limit)
        self.rows = rows
        self.exists = exists
        self.mode = "search"
        self.query = text
        self._show(rows, exists, query=text)
        self._remember(text)

    def read(self, number: int) -> None:
        summary = self._row(number)
        _summary, parsed, exists = self.session.read_uid(
            self.folder,
            summary.uid,
            index=number,
            exists=self.exists or 0,
            prefer_html=self.prefer_html,
        )
        title = f"Message {number} of {exists:,}  {self.folder}"
        display(render_message(title, parsed), enabled=self.session.settings.pager)

    def open_html(self, number: int) -> None:
        summary = self._row(number)
        _summary, parsed, _exists = self.session.read_uid(
            self.folder,
            summary.uid,
            index=number,
            exists=self.exists or 0,
            prefer_html=True,
        )
        if not parsed.html:
            print("This message has no HTML part.")
            return
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".html", prefix="mailcli-", delete=False, encoding="utf-8"
        )
        path = Path(handle.name)
        try:
            handle.write(parsed.html)
        finally:
            handle.close()
        webbrowser.open(path.resolve().as_uri())
        print(f"Opened {path}")

    def show_links(self, number: int, *, full: bool) -> None:
        summary = self._row(number)
        _summary, parsed, _exists = self.session.read_uid(
            self.folder, summary.uid, index=number, exists=self.exists or 0
        )
        print(format_links(parsed.links, full=full))

    def folders(self) -> None:
        names = self.session.folders()
        if not names:
            print("No folders.")
            return
        print("Folders:")
        for name in names:
            mark = " *" if name == self.folder else ""
            print(f"  {name}{mark}")

    def accounts(self) -> None:
        path = self.session.settings.config_path
        names, default_name = list_account_names(path)
        if not names:
            print("No accounts yet. Run: python -m mailcli setup")
            return
        for name in names:
            mark = " (default)" if name == default_name else ""
            current = " *" if name == self.session.settings.account else ""
            print(f"  {name}{mark}{current}")

    def switch_account(self, name: str) -> None:
        config = self.session.settings.config_path
        fresh = load_settings(
            config_path=str(config) if config else None,
            account=name,
        )
        self.session.close()
        self.session.settings = fresh
        self.session.connect()
        self.folder = fresh.folder or "INBOX"
        self.offset = 0
        self.mode = "inbox"
        print(f"Using account {fresh.account} ({fresh.user}).")
        self.inbox()

    def compose(self, reply_to: int | None) -> None:
        settings = self.session.settings
        quoted = ""
        subject = ""
        recipient = ""
        if reply_to is not None:
            summary = self._row(reply_to)
            _summary, parsed, _exists = self.session.read_uid(
                self.folder,
                summary.uid,
                index=reply_to,
                exists=self.exists or 0,
                prefer_html=self.prefer_html,
            )
            recipient = _address(parsed.sender)
            subject = parsed.subject
            if subject.lower().startswith("re:"):
                subject = subject
            else:
                subject = f"Re: {subject}"
            quoted = "\n".join("> " + line for line in (parsed.body or "").splitlines())
            print(f"To: {recipient}")
            print(f"Subject: {subject}")
        else:
            recipient = _ask("To: ")
            if not recipient:
                print("Compose cancelled.")
                return
            subject = _ask("Subject: ")
        print("Enter the message. Finish with a line containing only a period.")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                print()
                break
            if line == ".":
                break
            lines.append(line)
        body = "\n".join(lines)
        if quoted:
            body = (body + "\n\n" + quoted).strip()
        if not body:
            print("Compose cancelled: empty body.")
            return
        print("")
        print(f"To: {recipient}")
        print(f"Subject: {subject}")
        if _ask("Send this message? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print("Not sent.")
            return
        send_message(settings, [part.strip() for part in recipient.split(",")], subject, body)
        print(f"Sent to {recipient}.")

    def _row(self, number: int) -> Summary:
        if number < 1 or number > len(self.rows):
            shown = "search results" if self.mode == "search" else "this page"
            raise MailcliError(
                f"No message {number} in {shown}. It has {len(self.rows)} row"
                + ("s." if len(self.rows) != 1 else ".")
            )
        return self.rows[number - 1]

    def _show(self, rows: list[Summary], exists: int, *, query: str) -> None:
        if query:
            title = f'Search "{query}" in {self.folder}  {len(rows)} match' + ("es" if len(rows) != 1 else "")
            empty = f'No messages in {self.folder} matched "{query}".'
        elif exists > len(rows):
            title = f"{self.folder}  showing {len(rows)} of {exists:,}  newest first"
            empty = f"{self.folder} is empty."
        else:
            noun = "message" if len(rows) == 1 else "messages"
            title = f"{self.folder}  {len(rows)} {noun}  newest first"
            empty = f"{self.folder} is empty."
        print(
            render_table(
                [Row(item.index, item.when, item.sender, item.subject, item.unread) for item in rows],
                title=title,
                plain=self.plain,
                empty=empty,
            ),
            end="",
        )

    def _remember(self, query: str) -> None:
        save_listing(
            Listing(
                account=self.session.settings.account,
                folder=self.folder,
                limit=self.limit,
                offset=self.offset,
                mode=self.mode,
                query=query,
                exists=self.exists,
                uids=[item.uid for item in self.rows],
            )
        )


def _banner() -> None:
    print(f"mailcli {__version__}. Type help, or a command. Ctrl+D quits.")
    print("  inbox [n]     newest mail (n rows). ls is the same command")
    print("  next, prev    another page of the inbox")
    print("  read <#>      open a row from the list on screen. A bare number works")
    print("  search <text> then read <#> uses those results")
    print("  open <#>      open the HTML part in the browser")
    print("  links <#>     list that message's links. links <#> --full is uncut")
    print("  view html     or view text, when a message has both parts")
    print("  folder <name>, folders, refresh")
    print("  compose       To, Subject, and a body ended by a line with only .")
    print("  reply <#>     answer that row. Sending asks you to confirm")
    print("  account <name>, accounts, clear, quit")
    print("")


def _enable_line_editing() -> None:
    """History and tab completion. Missing readline is not an error."""
    if not sys.stdin.isatty():
        return
    try:
        import readline
    except ImportError:
        try:
            import pyreadline3 as readline  # type: ignore
        except ImportError:
            return
    try:
        readline.set_completer(_complete)
        readline.parse_and_bind("tab: complete")
        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            readline.read_history_file(str(path))
        import atexit

        atexit.register(readline.write_history_file, str(path))
    except (OSError, AttributeError):
        return


def _complete(text: str, state: int) -> str | None:
    matches = [name for name in _COMMANDS if name.startswith(text)]
    if state < len(matches):
        return matches[state]
    return None


def _number(text: str) -> int:
    cleaned = text.strip()
    if not cleaned.isdigit():
        raise MailcliError("Give a message number, for example: read 3")
    return int(cleaned)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        print()
        return ""


def _address(sender: str) -> str:
    if "<" in sender and ">" in sender:
        return sender.split("<", 1)[1].split(">", 1)[0].strip()
    return sender.strip()
