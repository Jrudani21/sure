import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import trust  # noqa: E402


def _sj(tmp_path, data=None, raw=None):
    p = tmp_path / "sure.json"
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
    elif data is not None:
        p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_add_easy_kind(tmp_path):
    assert trust.add(str(tmp_path), "install") is True
    assert trust.add(str(tmp_path), "install") is True
    assert trust.trusted(str(tmp_path)) == ["install"]


def test_add_hard_kind_refused_and_file_unchanged(tmp_path):
    p = _sj(tmp_path, {"a": 1})
    before = p.read_bytes()
    assert trust.add(str(tmp_path), "delete") is False
    assert p.read_bytes() == before
    assert trust.trusted(str(tmp_path)) == []


def test_add_impossible_and_unknown_refused(tmp_path):
    assert trust.add(str(tmp_path), "database") is False
    assert trust.add(str(tmp_path), "nope") is False
    assert not (tmp_path / "sure.json").exists()


def test_remove(tmp_path):
    trust.add(str(tmp_path), "install")
    trust.add(str(tmp_path), "config")
    trust.remove(str(tmp_path), "install")
    assert trust.trusted(str(tmp_path)) == ["config"]


def test_remove_absent_creates_nothing(tmp_path):
    trust.remove(str(tmp_path), "install")
    assert not (tmp_path / "sure.json").exists()


def test_other_keys_preserved_in_order(tmp_path):
    p = _sj(tmp_path, {"save_points": {"enabled": True}, "board": "é", "ask_on": ["config"]})
    trust.add(str(tmp_path), "install")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert list(d) == ["save_points", "board", "ask_on", "auto_allow"]
    assert d["board"] == "é" and d["auto_allow"] == ["install"]
    trust.remove(str(tmp_path), "install")
    assert json.loads(p.read_text(encoding="utf-8"))["save_points"] == {"enabled": True}
    assert [f.name for f in tmp_path.iterdir()] == ["sure.json"]


def test_trusted_tolerates_bad_files(tmp_path):
    assert trust.trusted(str(tmp_path)) == []
    p = _sj(tmp_path, raw="{nope")
    assert trust.trusted(str(tmp_path)) == []
    p.write_text("[1]", encoding="utf-8")
    assert trust.trusted(str(tmp_path)) == []
    p.write_text('{"auto_allow": "install"}', encoding="utf-8")
    assert trust.trusted(str(tmp_path)) == []
    p.write_text('{"auto_allow": ["install", 3, null]}', encoding="utf-8")
    assert trust.trusted(str(tmp_path)) == ["install"]


def test_invalid_json_not_overwritten(tmp_path):
    p = _sj(tmp_path, raw="{nope")
    assert trust.add(str(tmp_path), "install") is False
    assert p.read_text(encoding="utf-8") == "{nope"
