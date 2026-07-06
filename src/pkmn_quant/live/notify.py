"""macOS banner notifications via osascript. No-op on other platforms.

Kept in its own module so tests (and the daily CLI) can monkeypatch
`send_notification` without touching subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys


def send_notification(title: str, body: str) -> None:
    if sys.platform != "darwin":  # pragma: no cover
        return
    script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
