import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import events  # noqa: E402
import sure  # noqa: E402


def test_record_then_read_roundtrip(tmp_path):
    events.record(str(tmp_path), "asked", "delete", "hard", via="board", session="s1", request_id="r1")
    (ev,) = events.read(str(tmp_path))
    assert (ev["verb"], ev["kind"], ev["tier"], ev["via"], ev["session"], ev["request_id"]) == (
        "asked", "delete", "hard", "board", "s1", "r1")
    assert ev["ts"]


def test_read_falls_back_to_markdown_only_ledger(tmp_path):
    d = tmp_path / ".sure"
    d.mkdir()
    (d / "ledger.md").write_text(
        "- 2026-10-16T10:00:00 asked [delete/hard] x\n"
        "- 2026-10-16T10:00:01 ran [delete/hard]\n"
        "- 2026-10-16T10:00:02 auto [edit/easy]\n"
        "- 2026-10-16T10:00:03 failed [delete/hard]\n"
        "- 2026-10-16T10:00:04 quiet [read/easy]\n"
        "- 2026-10-16T10:00:05 save point abc\n",
        encoding="utf-8")
    evs = events.read(str(tmp_path), now=datetime(2026, 10, 17))
    assert [e["verb"] for e in evs] == ["asked", "ran", "auto", "failed", "quiet"]
    assert evs[0]["kind"] == "delete" and evs[0]["tier"] == "hard"
    assert all(e["via"] is None and e["session"] is None and e["request_id"] is None for e in evs)


def test_older_markdown_history_merged_before_jsonl(tmp_path):  # B4: pre-sidecar history is kept
    d = tmp_path / ".sure"
    d.mkdir()
    (d / "ledger.md").write_text("- 2026-10-16T10:00:00 asked [delete/hard] x\n", encoding="utf-8")
    (d / "events.jsonl").write_text(json.dumps({"ts": "2026-10-16T11:00:00", "verb": "ran"}) + "\n", encoding="utf-8")
    assert [e["verb"] for e in events.read(str(tmp_path), now=datetime(2026, 10, 17))] == ["asked", "ran"]


def test_read_skips_older_than_days(tmp_path):
    d = tmp_path / ".sure"
    d.mkdir()
    (d / "events.jsonl").write_text(
        json.dumps({"ts": "2026-10-01T10:00:00", "verb": "ran"}) + "\nnot json\n"
        + json.dumps({"ts": "2026-10-16T10:00:00", "verb": "asked"}) + "\n", encoding="utf-8")
    evs = events.read(str(tmp_path), days=7, now=datetime(2026, 10, 17))
    assert [e["verb"] for e in evs] == ["asked"]


def test_record_never_raises_on_unwritable_dir(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    events.record(str(f), "asked", "delete", "hard")  # cwd is a file: mkdir fails
    events.record(None, "asked")


def test_main_records_events_next_to_ledger(tmp_path):
    inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}, "session_id": "sid"})
    sure.main(["pre"], inp, str(tmp_path))
    sure.main(["post"], inp, str(tmp_path))
    evs = events.read(str(tmp_path))
    assert [e["verb"] for e in evs] == ["asked", "ran"]
    assert evs[0]["session"] == "sid" and evs[0]["via"] is None
