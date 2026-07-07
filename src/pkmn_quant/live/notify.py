"""macOS banner notifications via osascript. No-op on other platforms.

Kept in its own module so tests (and the daily CLI) can monkeypatch
`send_notification` without touching subprocess.
"""

from __future__ import annotations

import subprocess
import sys


def send_notification(title: str, body: str) -> None:
    if sys.platform != "darwin":  # pragma: no cover
        return
    # Pass body/title as argv so no escaping is needed -- embedding values in
    # the script string causes osascript to exit 1 on em dashes and other
    # characters that json.dumps encodes as \\uXXXX (which AppleScript doesn't
    # understand).
    result = subprocess.run(
        [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            "display notification (item 1 of argv) with title (item 2 of argv)",
            "-e",
            "end run",
            body,
            title,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:  # pragma: no cover
        sys.stderr.write(result.stderr.decode(errors="replace"))
