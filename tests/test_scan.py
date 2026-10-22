"""sure scan: replay Claude Code transcripts through today's rules. Read-only, masked, tolerant of junk."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SURE_DIR = Path(__file__).resolve().parent.parent / "sure"
sys.path.insert(0, str(SURE_DIR))
import scan  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)


def ts(days_ago=0):
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def call(tool, inp, days_ago=0, session="s1"):
    return json.dumps({"type": "assistant", "sessionId": session, "timestamp": ts(days_ago),
                       "message": {"role": "assistant",
                                   "content": [{"type": "text", "text": "ok"},
                                               {"type": "tool_use", "id": "t", "name": tool, "input": inp}]}})


def write(root, rel, lines):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def root(tmp_path):
    write(tmp_path, "proj-a/sess1.jsonl", [
        call("Bash", {"command": "rm -rf build"}),
        call("Bash", {"command": "ls -la"}),
        call("Bash", {"command": "cat .env"}),
        call("Bash", {"command": "npm install left-pad"}),
        call("Write", {"file_path": "CLAUDE.md", "content": "x"}),
        call("WebSearch", {"query": "python docs"}),
        call("Read", {"file_path": "a.py"}),
    ])
    return tmp_path


def run(root, **kw):
    return scan.scan(root, days=kw.pop("days", 30), now=NOW, **kw)


# ---- counting ----

def test_counts_calls_cards_kinds_and_tiers(root):
    s = run(root)
    assert s["calls"] == 7 and s["sessions"] == 1
    assert s["by_kind"]["delete"] == 1 and s["by_kind"]["secret_read"] == 1
    assert s["by_kind"]["install"] == 1 and s["by_kind"]["agent_config_edit"] == 1
    assert s["by_tier"] == {"impossible": 1, "hard": 2, "easy": 1}
    assert s["cards"] == 4


def test_quiet_kinds_are_counted_separately_not_as_cards(root):
    s = run(root)
    assert s["logged_only"] == 1 and "web_search" not in s["by_kind"]


def test_old_calls_are_outside_the_range(tmp_path):
    write(tmp_path, "p/a.jsonl", [call("Bash", {"command": "rm -rf x"}, days_ago=40),
                                  call("Bash", {"command": "rm -rf y"}, days_ago=2)])
    assert run(tmp_path, days=30)["by_kind"] == {"delete": 1}
    assert run(tmp_path, days=60)["by_kind"] == {"delete": 2}


def test_nested_subagent_files_are_read(tmp_path):
    write(tmp_path, "proj/sess/subagents/agent-1.jsonl", [call("Bash", {"command": "rm -rf z"}, session="sub")])
    assert run(tmp_path)["by_kind"] == {"delete": 1}


def test_junk_lines_never_raise(tmp_path):
    write(tmp_path, "p/a.jsonl", ["not json", "[1,2]", "null", json.dumps({"type": "assistant"}),
                                  json.dumps({"type": "assistant", "message": {"content": "text"}}),
                                  json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}),
                                  json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": "rm -rf x"}]}}),
                                  call("Bash", {"command": "rm -rf ok"})])
    assert run(tmp_path)["by_kind"] == {"delete": 1}


def test_empty_root_is_reported(tmp_path):
    s = run(tmp_path)
    assert s["calls"] == 0 and s["files"] == 0
    assert "No Claude Code transcripts found" in scan.render_text(s)


def test_repeated_commands_are_ranked(tmp_path):
    write(tmp_path, "p/a.jsonl", [call("Bash", {"command": "rm -rf build"})] * 3 + [call("Bash", {"command": "rm -rf dist"})])
    top = run(tmp_path)["top"]
    assert top[0]["count"] == 3 and top[0]["kind"] == "delete" and "rm -rf build" in top[0]["text"]


# ---- output safety ----

def test_secrets_are_masked_and_long_text_is_cut(tmp_path):
    cmd = "cat .env && echo TOKEN=hunter2value && echo " + "x" * 400
    write(tmp_path, "p/a.jsonl", [call("Bash", {"command": cmd})])
    s = run(tmp_path)
    text, page = scan.render_text(s), scan.render_html(s)
    assert "hunter2value" not in text and "hunter2value" not in page
    assert all(len(t["text"]) <= 140 for t in s["top"])


def test_html_is_self_contained_and_escapes_commands(tmp_path):
    write(tmp_path, "p/a.jsonl", [call("Bash", {"command": 'rm -rf "<script>alert(1)</script>"'})])
    page = scan.render_html(run(tmp_path))
    assert page.lower().startswith("<!doctype html>")
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "http://" not in page and "https://" not in page and "<script" not in page.lower().replace("&lt;script", "")


def test_report_says_it_shows_what_would_match_now(root):
    assert "what would match now" in scan.render_text(run(root)).lower()


def test_scan_never_modifies_the_transcripts(root):
    def digest():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(root).rglob("*") if p.is_file()}
    before = digest()
    run(root)
    scan.render_html(run(root))
    assert digest() == before


# ---- command line ----

def test_cli_prints_a_summary_and_writes_html(root, tmp_path):
    out = tmp_path / "report.html"
    r = subprocess.run([sys.executable, str(SURE_DIR / "sure.py"), "scan", "--dir", str(root), "--days", "30",
                        "--html", str(out)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "delete" in r.stdout and "secret_read" in r.stdout
    assert out.exists() and out.read_text(encoding="utf-8").lower().startswith("<!doctype html>")


def test_cli_bad_arguments_do_not_crash(tmp_path):
    r = subprocess.run([sys.executable, str(SURE_DIR / "sure.py"), "scan", "--days", "abc", "--dir", str(tmp_path)],
                       capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0 and r.stdout.strip()


def test_each_kind_shows_its_own_tier(root):
    text = scan.render_text(run(root))
    assert "delete (hard)" in text and "secret_read (impossible)" in text and "install (easy)" in text
