"""ntfy phone push beside the macOS banner. No network: httpx.post is faked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from pkmn_quant.cli import app
from pkmn_quant.live import notify
from pkmn_quant.live.notify import NtfyConfig


@pytest.fixture(autouse=True)
def _reset_ntfy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "_ntfy", None)


def _write_cfg(root: Path, text: str) -> None:
    cfg = root / "data" / "config"
    cfg.mkdir(parents=True)
    (cfg / "notify.toml").write_text(text, encoding="utf-8")


def test_load_config_absent_returns_none(tmp_path: Path) -> None:
    assert notify.load_ntfy_config(tmp_path) is None


def test_load_config_reads_topic_and_default_server(tmp_path: Path) -> None:
    _write_cfg(tmp_path, '[ntfy]\ntopic = "my-topic"\n')
    assert notify.load_ntfy_config(tmp_path) == NtfyConfig(topic="my-topic")
    assert NtfyConfig(topic="x").server == "https://ntfy.sh"


def test_load_config_reads_custom_server(tmp_path: Path) -> None:
    _write_cfg(tmp_path, '[ntfy]\ntopic = "t"\nserver = "https://example.test"\n')
    assert notify.load_ntfy_config(tmp_path) == NtfyConfig("t", "https://example.test")


def test_load_config_missing_topic_returns_none(tmp_path: Path) -> None:
    _write_cfg(tmp_path, '[ntfy]\nserver = "https://example.test"\n')
    assert notify.load_ntfy_config(tmp_path) is None


def test_load_config_bad_toml_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_cfg(tmp_path, "[ntfy\ntopic = ")
    assert notify.load_ntfy_config(tmp_path) is None
    assert capsys.readouterr().err != ""


def test_send_ntfy_posts_json_with_unicode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    real_post = httpx.post

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            return c.post(url, **kw)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert real_post is not fake_post
    title, body = "pkmn daily: ingest problem", "stalled — no new days"
    assert notify.send_ntfy(NtfyConfig("tp"), title, body) is True
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://ntfy.sh"
    assert json.loads(req.content.decode("utf-8")) == {
        "topic": "tp",
        "title": title,
        "message": body,
    }


@pytest.mark.parametrize("failure", ["connect", "500"])
def test_send_ntfy_failure_returns_false(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    def fake_post(url: str, **kw: Any) -> httpx.Response:
        if failure == "connect":
            raise httpx.ConnectError("boom")
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.send_ntfy(NtfyConfig("tp"), "t", "b") is False
    assert capsys.readouterr().err != ""


def test_send_notification_without_config_makes_no_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("no http expected")

    monkeypatch.setattr(notify.httpx, "post", boom)
    monkeypatch.setattr(notify.sys, "platform", "linux")
    notify.send_notification("t", "b")


def test_send_notification_with_config_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify, "_ntfy", NtfyConfig("tp"))
    monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, b: calls.append((t, b)) or True)
    notify.send_notification("t", "b")
    assert calls == [("t", "b")]


def test_notify_test_command_exits_1_without_config(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["notify-test", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "notify.toml" in result.output


def test_notify_test_command_sends_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(tmp_path, '[ntfy]\ntopic = "tp"\n')
    sent: list[tuple[NtfyConfig, str, str]] = []
    monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, b: sent.append((cfg, t, b)) or True)
    result = CliRunner().invoke(app, ["notify-test", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert sent == [
        (NtfyConfig("tp"), "pkmn test", "If you can read this on your phone, ntfy works.")
    ]


def test_notify_test_command_exits_1_on_send_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(tmp_path, '[ntfy]\ntopic = "tp"\n')
    monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, b: False)
    result = CliRunner().invoke(app, ["notify-test", "--root", str(tmp_path)])
    assert result.exit_code == 1
