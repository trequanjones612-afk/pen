"""Number-driven inbox loop that works in Windows PowerShell.

PowerShell 5.1 does not need curses. Commands are typed at a prompt: a message
number opens it, ``b`` returns to the list, and ``q`` quits.
"""

from __future__ import annotations

from mailcli.config import Settings
from mailcli.errors import MailcliError
from mailcli.imap_client import ImapSession, Summary
from mailcli.render import Row, render_message, render_table
from mailcli.smtp_client import send_message


def run_interactive(settings: Settings, *, plain: bool) -> int:
    """Browse ``settings.folder`` until the user quits."""
    folder = settings.folder
    viewing: tuple[int, str] | None = None
    while True:
        try:
            if viewing is None:
                _print_list(settings, folder, plain)
            else:
                number, text = viewing
                print(text, end="" if text.endswith("\n") else "\n")
                print("b back   n next   p previous   q quit")
        except MailcliError as exc:
            print(f"mailcli: {exc}")
        try:
            typed = input("> ").strip()
        except EOFError:
            print()
            return 0
        if not typed:
            continue
        command, _, rest = typed.partition(" ")
        lowered = command.lower()
        if lowered in {"q", "quit", "exit"}:
            return 0
        if lowered in {"h", "help", "?"}:
            _help()
            continue
        if viewing is not None and lowered in {"b", "back"}:
            viewing = None
            continue
        if viewing is not None and lowered in {"n", "p"}:
            delta = 1 if lowered == "n" else -1
            viewing = _open(settings, folder, viewing[0] + delta, plain)
            continue
        if lowered in {"r", "refresh"}:
            viewing = None
            continue
        if lowered in {"f", "folders"}:
            _print_folders(settings)
            continue
        if lowered in {"g", "folder"}:
            name = rest.strip()
            if not name:
                print("Usage: g FOLDER")
                continue
            folder = name
            settings.folder = name
            viewing = None
            continue
        if lowered in {"s", "search"}:
            query = rest.strip() or _ask("Search: ")
            if not query:
                continue
            _print_search(settings, folder, query, plain)
            continue
        if lowered in {"c", "compose"}:
            _compose(settings)
            viewing = None
            continue
        if command.isdigit():
            viewing = _open(settings, folder, int(command), plain)
            continue
        print("Unknown command. Type h for help.")


def _print_list(settings: Settings, folder: str, plain: bool) -> None:
    with ImapSession(settings) as session:
        rows = session.summaries(folder)
    title = f"{folder}  {len(rows)} message" + ("s" if len(rows) != 1 else "") + "  newest first"
    empty = f"{folder} is empty."
    print(
        render_table(
            [_row(item) for item in rows],
            title=title,
            plain=plain,
            empty=empty,
        ),
        end="",
    )
    print("Type a number to open it.  r refresh   s TEXT search   c compose   f folders   h help   q quit")


def _print_search(settings: Settings, folder: str, query: str, plain: bool) -> None:
    with ImapSession(settings) as session:
        rows = session.search(folder, query)
    title = f'Search "{query}" in {folder}  {len(rows)} match' + ("es" if len(rows) != 1 else "")
    print(
        render_table(
            [_row(item) for item in rows],
            title=title,
            plain=plain,
            empty=f'No messages in {folder} matched "{query}".',
        ),
        end="",
    )
    print("Numbers match inbox. Type one to open that message.")


def _print_folders(settings: Settings) -> None:
    with ImapSession(settings) as session:
        names = session.folders()
    print("Folders:")
    if not names:
        print("  (none)")
        return
    for name in names:
        print(f"  {name}")


def _open(settings: Settings, folder: str, number: int, plain: bool) -> tuple[int, str] | None:
    del plain
    try:
        with ImapSession(settings) as session:
            summary, parsed, total = session.read(folder, number)
    except MailcliError as exc:
        print(f"mailcli: {exc}")
        return None
    title = f"Message {summary.index} of {total}  {folder}"
    return summary.index, render_message(title, parsed)


def _compose(settings: Settings) -> None:
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
    if body == "":
        print("Compose cancelled: empty body.")
        return
    send_message(settings, [part.strip() for part in recipient.split(",")], subject, body)
    print(f"Sent to {recipient}.")


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        print()
        return ""


def _help() -> None:
    print(
        "\n".join(
            [
                "Commands:",
                "  <number>     open that message (newest is 1)",
                "  b            back to the list",
                "  n / p        next or previous message while one is open",
                "  r            refresh the list",
                "  s TEXT       search this folder; numbers match the inbox",
                "  f            list folders",
                "  g FOLDER     open another folder",
                "  c            compose a message",
                "  h            show this help",
                "  q            quit",
            ]
        )
    )


def _row(item: Summary) -> Row:
    return Row(item.index, item.when, item.sender, item.subject, item.unread)
