"""Render inbox rows as plain columns, or as a rich table when it is installed."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone

from mailcli.mimeutil import Attachment, ParsedMessage, format_size


class Row:
    """One line in an inbox or search table. A small carrier, not a model."""

    def __init__(
        self,
        index: int,
        when: datetime | None,
        sender: str,
        subject: str,
        unread: bool,
    ) -> None:
        self.index = index
        self.when = when
        self.sender = sender
        self.subject = subject
        self.unread = unread


def format_when(when: datetime | None) -> str:
    """Format a message timestamp as ``YYYY-MM-DD HH:MM`` in local time."""
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().strftime("%Y-%m-%d %H:%M")


def render_table(
    rows: list[Row],
    *,
    title: str,
    plain: bool,
    empty: str,
) -> str:
    """Return the inbox or search table, using rich when it is available."""
    if not rows:
        return empty + "\n"
    if not plain:
        rendered = _render_rich(rows, title)
        if rendered is not None:
            return rendered
    return _render_plain(rows, title)


def render_message(detail_title: str, parsed: ParsedMessage) -> str:
    """Return the plain-text view used by ``read``."""
    lines = [detail_title, ""]
    lines.append(f"From: {parsed.sender or '(unknown)'}")
    if parsed.to:
        lines.append(f"To: {parsed.to}")
    if parsed.cc:
        lines.append(f"Cc: {parsed.cc}")
    if parsed.date_raw:
        lines.append(f"Date: {parsed.date_raw}")
    lines.append(f"Subject: {parsed.subject or '(no subject)'}")
    lines.append("")
    if parsed.html_only:
        lines.append("[HTML-only message; showing a plain-text rendering]")
        lines.append("")
    lines.append(parsed.body or "(no text body)")
    lines.append("")
    if parsed.attachments:
        lines.append("Attachments:")
        for item in parsed.attachments:
            lines.append(f"  - {_attachment_line(item)}")
    else:
        lines.append("Attachments: none")
    lines.append("")
    return "\n".join(lines)


def _attachment_line(item: Attachment) -> str:
    return f"{item.filename} ({item.content_type}, {format_size(item.size)})"


def _render_plain(rows: list[Row], title: str) -> str:
    prepared = [
        (
            str(row.index),
            "*" if row.unread else "",
            format_when(row.when),
            _clip(row.sender or "(unknown)", 28),
            _clip(row.subject or "(no subject)", 60),
        )
        for row in rows
    ]
    headers = ("#", "U", "Date", "From", "Subject")
    widths = [len(header) for header in headers]
    for cells in prepared:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))
    # The unread marker is a single column and stays narrow.
    widths[1] = max(widths[1], 1)

    def line(cells: tuple[str, ...] | tuple[str, str, str, str, str]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)).rstrip()

    body = [title, "", line(headers), line(tuple("-" * width for width in widths))]
    body.extend(line(cells) for cells in prepared)
    body.append("")
    return "\n".join(body)


def _render_rich(rows: list[Row], title: str) -> str | None:
    try:
        from rich import box
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        return None
    table = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        show_edge=False,
        pad_edge=False,
        expand=False,
        title_justify="left",
    )
    table.add_column("#", justify="right", style="cyan", no_wrap=True, min_width=2)
    table.add_column("U", justify="center", no_wrap=True, min_width=1)
    table.add_column("Date", style="green", no_wrap=True, min_width=16)
    table.add_column("From", no_wrap=True, min_width=24, max_width=36, overflow="ellipsis")
    table.add_column("Subject", no_wrap=True, min_width=24, max_width=44, overflow="ellipsis")
    for row in rows:
        subject = row.subject or "(no subject)"
        sender = row.sender or "(unknown)"
        if row.unread:
            subject = f"[bold]{subject}[/bold]"
            sender = f"[bold]{sender}[/bold]"
        table.add_row(
            str(row.index),
            "[bold yellow]*[/bold yellow]" if row.unread else "",
            format_when(row.when),
            sender,
            subject,
        )
    # Both dimensions are required. Rich ignores a width-only override on a dumb terminal.
    columns, lines = shutil.get_terminal_size(fallback=(120, 32))
    console = Console(
        force_terminal=True,
        color_system="standard",
        width=columns if columns >= 100 else 120,
        height=lines if lines >= 20 else 32,
    )
    with console.capture() as capture:
        console.print(table)
    return capture.get()


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    if limit <= 3:
        return compact[:limit]
    return compact[: limit - 3] + "..."
