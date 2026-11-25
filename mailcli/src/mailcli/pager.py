"""Page long messages in a terminal, including Windows PowerShell.

Space or Enter shows the next screen. ``q`` stops. When stdout is not a
terminal, or ``pager`` is false in the config, the whole message is printed.
"""

from __future__ import annotations

import os
import shutil
import sys


def display(text: str, *, enabled: bool) -> None:
    """Print ``text``, pausing when it is taller than the terminal."""
    if not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines()
    height = shutil.get_terminal_size(fallback=(80, 24)).lines
    page_size = max(8, height - 2)
    if not enabled or not sys.stdout.isatty() or len(lines) <= page_size:
        print(text, end="")
        return
    index = 0
    while index < len(lines):
        chunk = lines[index : index + page_size]
        print("\n".join(chunk))
        index += page_size
        if index >= len(lines):
            break
        key = _read_key()
        if key in {"q", "Q", "\x03", "\x04"}:
            print()
            return
    return


def _read_key() -> str:
    prompt = "-- More -- space next, q quit "
    if os.name == "nt":
        import msvcrt

        sys.stdout.write(prompt)
        sys.stdout.flush()
        character = msvcrt.getwch()
        sys.stdout.write("\n")
        sys.stdout.flush()
        return character
    try:
        import termios
        import tty
    except ImportError:
        return input(prompt)[:1]
    sys.stdout.write(prompt)
    sys.stdout.flush()
    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        character = sys.stdin.read(1)
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return character
