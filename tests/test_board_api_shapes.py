"""The shapes the board page reads: state()["flow"], state()["trusted"], /api/replay."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import board  # noqa: E402
from test_board import REQ, call, post_bg, see, wait_pending  # noqa: E402


@pytest.fixture
def b(tmp_path):
    bd = board.Board(runtime=tmp_path / "board.json")
    bd.start()
    yield bd
    bd.stop()


def test_state_flow_counts(b, tmp_path):
    (tmp_path / "sure.json").write_text(json.dumps({"auto_allow": ["install"]}))
    reqs = [dict(REQ, request_id="a", session="s1"), dict(REQ, request_id="b", session="s1"),
            dict(REQ, request_id="c", session="s2")]
    threads = [post_bg(b, r)[0] for r in reqs]
    wait_pending(b, 3)
    for rid in ("a", "b", "c"):
        assert call(b, "POST", "/api/decide", {"request_id": rid, "decision": "allow_once"})[0] == 200
    for t in threads:
        t.join(5)
    # B3: a trusted kind waits for a decision like any other (no board-side auto-release)
    t, _ = post_bg(b, dict(REQ, request_id="d", session="s2", kind="install", tier="easy", cwd=str(tmp_path)))
    wait_pending(b, 1)
    assert call(b, "POST", "/api/decide", {"request_id": "d", "decision": "allow_once"})[0] == 200
    t.join(5)
    s = call(b, "GET", "/api/state")[1]
    assert s["flow"] == [{"session": "s1", "kind": "delete", "handler": "you", "count": 2},
                         {"session": "s2", "kind": "delete", "handler": "you", "count": 1},
                         {"session": "s2", "kind": "install", "handler": "you", "count": 1}]
    # every seen cwd that exists is a key (the Replay chooser lists them), even with nothing trusted
    assert s["trusted"] == {str(tmp_path): ["install"], REQ["cwd"]: []}


def _ledger(tmp_path, lines):
    (tmp_path / ".sure").mkdir(exist_ok=True)
    (tmp_path / ".sure" / "ledger.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ts(days_ago):
    return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_replay_shape_with_markdown_only_ledger(b, tmp_path, monkeypatch):
    see(b, tmp_path, monkeypatch)
    _ledger(tmp_path, [f"- {_ts(1)} asked [delete/hard] rm -rf build", f"- {_ts(1)} ran [delete/hard]",
                       f"- {_ts(2)} auto [install/easy]", f"- {_ts(40)} asked [push/hard] old"])
    st, body, _ = call(b, "GET", f"/api/replay?days=7&cwd={quote(str(tmp_path))}")
    assert st == 200 and "error" not in body
    assert [(e["verb"], e["kind"], e["tier"], e["session"], e["via"]) for e in body["events"]] == [
        ("asked", "delete", "hard", None, None), ("ran", "delete", "hard", None, None),
        ("auto", "install", "easy", None, None)]
    assert body["flow"] == [{"session": None, "kind": "delete", "handler": "you", "count": 1},
                            {"session": None, "kind": "install", "handler": "rule", "count": 1}]


def test_replay_includes_report_text(b, tmp_path, monkeypatch):
    _ledger(tmp_path, [f"- {_ts(0)} asked [delete/hard] x", f"- {_ts(0)} ran [delete/hard]"])
    see(b, tmp_path, monkeypatch)
    see(b, tmp_path / ".sure", monkeypatch)
    st, body, _ = call(b, "GET", f"/api/replay?days=1&cwd={quote(str(tmp_path))}")
    assert st == 200
    assert body["report"].startswith("Last 1 days:")
    assert "delete (hard): asked 1, you approved 1 of 1" in body["report"]
    st, body, _ = call(b, "GET", f"/api/replay?days=365&cwd={quote(str(tmp_path / '.sure'))}")
    assert st == 200 and body["report"] == "Nothing logged in the last 365 days." and body["events"] == []
