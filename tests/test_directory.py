import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import directory  # noqa: E402

TOUCHES = {"read", "write_files", "run_commands", "network", "external_tools", "delegate", "settings_or_mode", "protected_paths"}
TIERS = {"easy", "hard", "impossible"}
TEXT = ("id", "name", "what", "why", "cost", "undo")


def test_loads_seven_existing_kinds():
    ids = {e["id"] for e in directory.load()}
    assert {"delete", "push", "install", "config", "database", "script_launcher", "pipe_to_shell", "download_and_run"} <= ids


def test_every_entry_valid():
    for e in directory.load():
        assert e["touches"] in TOUCHES and e["tier"] in TIERS and e["lasts"] in {"once", "session", "forever"}
        assert all(isinstance(e[k], str) and e[k].strip() for k in TEXT)
        assert e["decided_by"] and set(e["decided_by"]) <= {"you", "saved rule", "classifier", "sandbox"}
        assert all(m["scope"] in {"whole", "seg", "path", "tool"} and m["tool"] and m["pattern"] for m in e["matchers"])


def test_validate_rejects_bad_tier():
    entries = copy.deepcopy(directory.load())
    entries[0]["tier"] = "scary"
    with pytest.raises(ValueError, match="tier") as ei:
        directory.validate(entries)
    assert entries[0]["id"] in str(ei.value)


def test_matcher_order_preserved():
    by_id = {e["id"]: e for e in directory.load()}
    assert [m["scope"] for m in by_id["delete"]["matchers"]] == ["seg", "seg", "seg", "whole", "seg", "seg"]
    assert len(by_id["pipe_to_shell"]["matchers"]) == 3
    order = [e["id"] for e in directory.load() if e["matchers"]]
    assert order.index("pipe_to_shell") < order.index("database") < order.index("delete") < order.index("push") < order.index("install") < order.index("script_launcher")


def test_broken_directory_keeps_hook_silent(tmp_path, monkeypatch):
    import importlib
    import json

    import sure

    bad = tmp_path / "permissions.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(directory, "DEFAULT_PATH", bad)
    try:
        importlib.reload(sure)  # import-time load fails; must not raise
        assert sure.classify("Bash", {"command": "rm -rf /"}) is None
        assert sure.main(["pre"], json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}), str(tmp_path)) == ""
    finally:
        monkeypatch.undo()
        importlib.reload(sure)
    assert sure.classify("Bash", {"command": "rm -rf /"})["kind"] == "delete"


def _reload_with(monkeypatch, tmp_path, content):
    import importlib
    import json

    import sure

    p = tmp_path / "permissions.json"
    if content is not None:
        p.write_text(content, encoding="utf-8")
    monkeypatch.setattr(directory, "DEFAULT_PATH", p)
    try:
        importlib.reload(sure)
        assert sure.classify("Bash", {"command": "rm -rf /"}) is None
        assert sure.classify("Write", {"file_path": "package.json"}) is None
        assert sure.main(["pre"], json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}), str(tmp_path)) == ""
    finally:
        monkeypatch.undo()
        importlib.reload(sure)


def _entry_with(pattern):
    e = dict(directory.load()[0])
    e["matchers"] = [{"tool": "Bash", "scope": "seg", "pattern": pattern}]
    import json

    return json.dumps([e])


@pytest.mark.parametrize("pattern", ["(", 5])
def test_bad_pattern_keeps_hook_silent(tmp_path, monkeypatch, pattern):
    _reload_with(monkeypatch, tmp_path, _entry_with(pattern))


def test_validate_rejects_bad_patterns():
    for pat in ("(", 5):
        with pytest.raises(ValueError, match="download_and_run"):
            directory.validate(__import__("json").loads(_entry_with(pat)))


def test_missing_directory_keeps_hook_silent(tmp_path, monkeypatch):
    _reload_with(monkeypatch, tmp_path, None)
