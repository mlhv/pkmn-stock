"""macOS banner notifications via osascript, plus optional ntfy phone push.

The banner is a no-op on other platforms. ntfy is off until `configure(root)`
finds `data/config/notify.toml`.

Kept in its own module so tests (and the daily CLI) can monkeypatch
`send_notification` without touching subprocess.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class NtfyConfig:
    topic: str
    server: str = "https://ntfy.sh"


_ntfy: NtfyConfig | None = None


def load_ntfy_config(root: Path) -> NtfyConfig | None:
    """Read `[ntfy]` from root/data/config/notify.toml; None if absent or unusable."""
    path = root / "data" / "config" / "notify.toml"
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"warning: cannot read {path}: {exc}\n")
        return None
    table = data.get("ntfy")
    if not isinstance(table, dict):
        return None
    topic = table.get("topic")
    if not isinstance(topic, str) or not topic:
        return None
    server = table.get("server")
    if isinstance(server, str) and server:
        return NtfyConfig(topic=topic, server=server)
    return NtfyConfig(topic=topic)


def configure(root: Path) -> None:
    global _ntfy
    _ntfy = load_ntfy_config(root)


def send_ntfy(config: NtfyConfig, title: str, body: str) -> bool:
    """POST one push to ntfy. Never raises; False on any failure."""
    try:
        response = httpx.post(
            config.server,
            json={"topic": config.topic, "title": title, "message": body},
            timeout=10.0,
        )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        sys.stderr.write(f"warning: ntfy push failed: {exc}\n")
        return False
    return True


def send_notification(title: str, body: str) -> None:
    _banner(title, body)
    if _ntfy is not None:
        send_ntfy(_ntfy, title, body)


def _banner(title: str, body: str) -> None:
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
            "--",  # prevent osascript from treating a leading '-' in body as a flag
            body,
            title,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:  # pragma: no cover
        sys.stderr.write(result.stderr.decode(errors="replace"))
