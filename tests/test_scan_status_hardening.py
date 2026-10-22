"""Audit fixes for sure scan / sure status: hostile transcripts, settings and project files must not crash or leak."""
import json
import sys
import time
from pathlib import Path

import pytest

SURE_DIR = Path(__file__).resolve().parent.parent / "sure"
sys.path.insert(0, str(SURE_DIR))
import scan  # noqa: E402
import status  # noqa: E402


def one_call(command):
    return json.dumps({"type": "assistant", "sessionId": "s", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}})


def scan_lines(tmp_path, lines):
    (tmp_path / "a.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return scan.scan(tmp_path)


@pytest.mark.parametrize("command", [
    "cat .env && PGPASSWORD=hunter2 psql",
    "cat .env && echo password=\"hunter2\"",
    "cat .env && echo DB_PASS=hunter2 API_KEY=hunter2",
    "cat .env && mysql -uroot -phunter2",
    "cat .env && cur" + "l -u admin:hunter2 x",
    "cat .env && cur" + "l --password hunter2 x",
    "cat .env && cur" + "l -H 'Authorization: Bas" + "ic aHVudGVyMmh1bnRlcg==' x",
    "cat .env && echo " + "ey" + "JhbGciOiJIUzI1." + "ey" + "JzdWIiOiIxMjM0.abcDEF123_-xyz",
])
def test_more_secret_shapes_are_masked(tmp_path, command):
    s = scan_lines(tmp_path, [one_call(command)])
    page = scan.render_text(s) + scan.render_html(s)
    assert "hunter2" not in page and "aHVudGVy" not in page and "abcDEF123" not in page


def test_bad_bytes_and_odd_types_do_not_crash(tmp_path):
    (tmp_path / "bad.jsonl").write_bytes(b"\xff\xfe\x00junk\n" + one_call("rm -rf x").encode() + b"\n")
    odd = [json.dumps({"type": "assistant", "timestamp": 123, "message": {"content": []}}),
           json.dumps({"type": "assistant", "sessionId": [1], "message": {"content": []}}),
           "[" * 200000]
    (tmp_path / "odd.jsonl").write_text("\n".join(odd) + "\n", encoding="utf-8")
    assert scan.scan(tmp_path)["by_kind"] == {"delete": 1}


def test_a_failing_classifier_is_skipped(tmp_path):
    def boom(tool, inp):
        raise ValueError("x")
    (tmp_path / "a.jsonl").write_text(one_call("ls") + "\n", encoding="utf-8")
    assert scan.scan(tmp_path, classify=boom)["cards"] == 0


def test_huge_days_value_is_clamped(tmp_path):
    out = scan.main(["--dir", str(tmp_path), "--days", "99999999999"])
    assert "No Claude Code transcripts found" in out


def test_big_line_scan_is_fast(tmp_path):
    t = time.time()
    scan_lines(tmp_path, [one_call("echo " + "a" * 100000)] * 20)
    assert time.time() - t < 5


def settings(tmp_path, text, encoding="utf-8"):
    p = tmp_path / "settings.json"
    p.write_bytes(text.encode(encoding))
    return p


@pytest.mark.parametrize("body", ["[]", '{"hooks": []}', '{"hooks": {"PreToolUse": 5}}', "null"])
def test_odd_settings_shapes_mean_not_installed(tmp_path, body):
    out = status.render_status(str(tmp_path), settings_path=str(settings(tmp_path, body)),
                               runtime_path=str(tmp_path / "rt.json"))
    assert "NOT INSTALLED" in out


def test_settings_with_a_byte_order_mark_is_read(tmp_path):
    out = status.render_status(str(tmp_path), settings_path=str(settings(tmp_path, "﻿{}")),
                               runtime_path=str(tmp_path / "rt.json"))
    assert "could not be read" not in out


def test_a_directory_as_settings_does_not_crash(tmp_path):
    out = status.render_status(str(tmp_path), settings_path=str(tmp_path), runtime_path=str(tmp_path / "rt.json"))
    assert "Sure?" in out


def test_hostile_project_files_cannot_fake_the_report(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".sure").mkdir(parents=True)
    fake = "\x1b[31mRED\x1b[0m\nHook: installed\nNo problems found."
    (proj / "sure.json").write_text(json.dumps({"auto_allow": [fake, 1, ["x"]]}), encoding="utf-8")
    ev = {"ts": "2026-10-21T10:00:00Z", "verb": "asked", "kind": ["a"], "tier": "hard"}
    (proj / ".sure" / "events.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
    out = status.render_status(str(proj), settings_path=str(tmp_path / "none.json"), runtime_path=str(tmp_path / "rt.json"))
    assert "\x1b" not in out
    lines = out.splitlines()
    assert "No problems found." not in lines and not any(ln.startswith("Hook: installed") for ln in lines)


@pytest.mark.parametrize("text", [
    "PGPASSWORD=hunter2 psql", 'echo password="hunter2"', "x --password hunter2", "cur" + "l -u admin:hunter2 x",
    "echo API_KEY=hunter2", "echo DB_PASS=hunter2",
])
def test_directory_clean_masks_more_secret_shapes(text):
    import directory
    assert "hunter2" not in directory.clean(text)


@pytest.mark.parametrize("text", ["find . -print0", "ls -pa", "git commit -m 'fix password prompt'"])
def test_directory_clean_leaves_ordinary_commands_alone(text):
    import directory
    assert directory.clean(text) == text


def test_directory_clean_new_patterns_are_fast():
    import directory
    for text in ("pass" * 1000, "api_key" * 500 + "=", "Basic " * 600, "--password " * 400, "eyJ" + "a." * 2000):
        t = time.perf_counter()
        directory.clean(text)
        assert time.perf_counter() - t < 0.05


def test_html_report_carries_a_content_security_policy(tmp_path):
    assert "Content-Security-Policy" in scan.render_html(scan.scan(tmp_path))
