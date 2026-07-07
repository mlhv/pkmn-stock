"""Tests for pkmn_quant.live.notify.

Two approaches:
1. Monkeypatched subprocess.run — deterministic, CI-safe.  Verifies that
   body and title are passed as separate argv items (no string embedding).
2. Real osascript round-trip — guarded by sys.platform == "darwin".  Uses
   ``return (item 1 of argv)`` to avoid any visible banner while still
   exercising the actual interpreter.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from pkmn_quant.live import notify

# ---------------------------------------------------------------------------
# 1. Monkeypatched — CI-safe, verifies argv structure
# ---------------------------------------------------------------------------


def test_send_notification_passes_body_and_title_as_argv_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """body and title must arrive as separate argv items, not embedded in the
    AppleScript source — this is the fix for the em-dash / unicode bug."""
    title = 'test title with "quotes"'
    body = 'em dash — and backslash \\ and quotes "'

    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured.append(cmd)
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    monkeypatch.setattr(notify.sys, "platform", "darwin")

    notify.send_notification(title, body)

    assert len(captured) == 1, "send_notification must call subprocess.run exactly once"
    cmd = captured[0]
    assert cmd[0] == "osascript"

    # body and title must be the last two positional items, NOT embedded in -e text
    assert cmd[-2] == body, f"body not found as second-to-last argv item: {cmd}"
    assert cmd[-1] == title, f"title not found as last argv item: {cmd}"

    # Verify none of the -e strings contain the body or title text
    e_strings = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-e"]
    for s in e_strings:
        assert body not in s, f"-e script embeds the body string: {s!r}"
        assert title not in s, f"-e script embeds the title string: {s!r}"


def test_send_notification_noop_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """On non-macOS platforms the function returns without calling subprocess."""
    calls: list[Any] = []

    def fake_run(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
        calls.append(args)

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    monkeypatch.setattr(notify.sys, "platform", "linux")

    notify.send_notification("title", "body")

    assert calls == [], "subprocess.run must not be called on non-darwin"


# ---------------------------------------------------------------------------
# 2. Real osascript round-trip — darwin only, no visible banner
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="osascript only on macOS")
def test_osascript_argv_round_trip_with_special_chars() -> None:
    """Run osascript directly with body = em dash + double quotes + backslash
    and assert returncode 0 and the value round-trips via stdout.

    Uses ``return (item 1 of argv)`` so no banner is displayed.
    """
    body = 'em dash — and "quotes" and backslash \\'

    result = subprocess.run(
        [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            "return (item 1 of argv)",
            "-e",
            "end run",
            body,
            "ignored-title",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"osascript exited {result.returncode}: {result.stderr}"
    assert result.stdout.strip() == body


@pytest.mark.skipif(sys.platform != "darwin", reason="osascript only on macOS")
def test_send_notification_does_not_raise_with_em_dash() -> None:
    """Call the real send_notification (which calls real osascript).

    One banner may appear during the test run — that is acceptable.
    The key assertion is no exception is raised.
    """
    # No assertion needed beyond "does not raise".
    notify.send_notification(
        title="pkmn test",
        body="em dash — in the body — see dashboard",
    )
