"""sure status: is Sure? installed and healthy, what is this project set to, what happened lately."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SURE_DIR = Path(__file__).resolve().parent.parent / "sure"
sys.path.insert(0, str(SURE_DIR))
import events  # noqa: E402
import status  # noqa: E402
import sure  # noqa: E402


def render(tmp_path, settings=None, cwd=None, runtime=None):
    return status.render_status(str(cwd or tmp_path), settings_path=str(settings or tmp_path / "settings.json"),
                                runtime_path=str(runtime or tmp_path / "board.json"))


def installed(tmp_path):
    p = tmp_path / "settings.json"
    sure.install(str(p))
    return p


def test_a_fresh_install_has_no_problems(tmp_path):
    out = render(tmp_path, installed(tmp_path))
    assert "PROBLEM" not in out and "installed" in out.lower()


def test_not_installed_says_how_to_install(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{}", encoding="utf-8")
    out = render(tmp_path, p)
    assert "NOT INSTALLED" in out and "sure.py install" in out


def test_missing_settings_file_counts_as_not_installed(tmp_path):
    assert "NOT INSTALLED" in render(tmp_path, tmp_path / "nope.json")


def test_a_hook_pointing_at_a_missing_script_is_broken(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": 'python "E:/gone/sure/sure.py" pre', "timeout": 130}]}]}}), encoding="utf-8")
    out = render(tmp_path, p)
    assert "BROKEN" in out and "E:/gone/sure/sure.py" in out


def test_a_hook_from_a_different_copy_is_flagged(tmp_path):
    other = tmp_path / "other" / "sure.py"
    other.parent.mkdir()
    other.write_text("# other copy\n", encoding="utf-8")
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": sure._MATCHER, "hooks": [
        {"type": "command", "command": 'python "%s" pre' % other.as_posix(), "timeout": 130}]}]}}), encoding="utf-8")
    assert "different copy" in render(tmp_path, p).lower()


def test_old_matcher_and_short_timeout_are_flagged(tmp_path):
    p = tmp_path / "settings.json"
    script = Path(sure.__file__).resolve().as_posix()
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash|Write|Edit", "hooks": [
        {"type": "command", "command": 'python "%s" pre' % script, "timeout": 10}]}]}}), encoding="utf-8")
    out = render(tmp_path, p)
    assert "matcher" in out.lower() and "timeout" in out.lower() and "sure.py install" in out


def test_unreadable_settings_does_not_crash(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json", encoding="utf-8")
    assert "settings" in render(tmp_path, p).lower()


def test_project_section_shows_board_and_options(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "sure.json").write_text(json.dumps({"board": {"enabled": True}, "auto_allow": ["install"],
                                                "ask_on": ["web_search"], "save_points": {"enabled": True}}), encoding="utf-8")
    out = render(tmp_path, installed(tmp_path), cwd=proj)
    assert "install" in out and "web_search" in out
    assert "board: on" in out.lower() and "save points: on" in out.lower()


def test_project_without_options_says_defaults(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    assert "board: off" in render(tmp_path, installed(tmp_path), cwd=proj).lower()


def test_recent_decisions_and_the_busiest_kind(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    for k, t in (("delete", "hard"), ("delete", "hard"), ("install", "easy")):
        events.record(str(proj), "asked", kind=k, tier=t, what="something happened")
    out = render(tmp_path, installed(tmp_path), cwd=proj)
    assert "delete" in out and "install" in out and "most often" in out.lower()


def test_no_history_says_so(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    assert "no decisions yet" in render(tmp_path, installed(tmp_path), cwd=proj).lower()


def test_board_not_running_when_there_is_no_runtime_file(tmp_path):
    assert "not running" in render(tmp_path, installed(tmp_path)).lower()


def test_a_board_runtime_file_never_leaks_its_keys(tmp_path):
    rt = tmp_path / "board.json"
    rt.write_text(json.dumps({"port": 54321, "token": "SECRETKEYVALUE123", "pid": 999999999}), encoding="utf-8")
    out = render(tmp_path, installed(tmp_path), runtime=rt)
    assert "SECRETKEYVALUE123" not in out


def test_rule_count_matches_the_directory(tmp_path):
    n = len(sure.directory.load())
    assert ("%d kinds" % n) in render(tmp_path, installed(tmp_path))


def test_cli_status_runs_and_exits_zero(tmp_path):
    p = installed(tmp_path)
    r = subprocess.run([sys.executable, str(SURE_DIR / "sure.py"), "status", "--settings", str(p)],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "Sure?" in r.stdout
