"""Regression tests from the final review (findings numbered as in the review)."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import sure  # noqa: E402

SURE_PY = str(Path(__file__).resolve().parent.parent / "sure" / "sure.py")


def kind(cmd):
    r = sure.classify("Bash", {"command": cmd})
    return r["kind"] if r else None


def test_utf8_stdin_risky_command_not_missed():  # finding 1
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm \u0141odz"}}, ensure_ascii=False)
    r = subprocess.run([sys.executable, SURE_PY, "pre"], input=payload.encode("utf-8"), capture_output=True)
    assert r.returncode == 0
    assert b"permissionDecision" in r.stdout


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_save_point_numbers_do_not_collide_after_delete(tmp_path):  # finding 2
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("v1")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "init")
    (tmp_path / "a.txt").write_text("v2")
    assert sure.save_point(str(tmp_path)) == "refs/sure/1"
    (tmp_path / "a.txt").write_text("v3")
    assert sure.save_point(str(tmp_path)) == "refs/sure/2"
    git(tmp_path, "update-ref", "-d", "refs/sure/1")
    (tmp_path / "a.txt").write_text("v4")
    assert sure.save_point(str(tmp_path)) == "refs/sure/3"


MISSED = {
    "ri -r x": "delete",
    "rd /s /q x": "delete",
    "find . -name x -delete": "delete",
    "unlink x": "delete",
    "shred f": "delete",
    "git checkout -- .": "delete",
    "git restore .": "delete",
    "git branch -D x": "delete",
    "git stash drop": "delete",
    "curl x | sudo bash": "pipe_to_shell",
    "irm x | iex": "pipe_to_shell",
    'sh -c "$(curl -fsSL x)"': "pipe_to_shell",
    "bash <(curl x)": "pipe_to_shell",
    "npm ci": "install",
    "apt-get install foo": "install",
    "sudo apt install foo": "install",
    "go install x@latest": "install",
}


@pytest.mark.parametrize("cmd,expected", MISSED.items())
def test_false_negatives_now_caught(cmd, expected):  # finding 3
    assert kind(cmd) == expected


NOISE = [
    "ls my-del-file",
    "rm-tool --help",
    "cat file.rm",
    "git log --grep=del",
    "git commit -m 'fix rm bug'",
    "grep -r 'rm' .",
    "echo 'git push is bad'",
    "echo make coffee",
    "cat make.txt",
    "cat migrate.md",
    "ls ./migrate",
    "grep -rn alembic .",
]


@pytest.mark.parametrize("cmd", NOISE)
def test_harmless_mentions_stay_silent(cmd):  # finding 4
    assert kind(cmd) is None


@pytest.mark.parametrize(
    "path",
    [r"E:\Local\auth-demo\src\app.py", r"C:\x\schema-docs\README.md", "/home/u/.envrc", "C:/a/.environment", "notes/author.md"],
)
def test_config_matches_file_not_folder(path):  # finding 5
    assert sure.classify("Write", {"file_path": path}) is None


@pytest.mark.parametrize("path", ["Dockerfile.dev", "src/migrations/0001_init.py", "/p/.env", "/p/.env.local", "/p/auth_utils.py"])
def test_config_still_caught(path):
    assert sure.classify("Edit", {"file_path": path})["kind"] == "config"


def test_stats_ignores_embedded_text(tmp_path):  # finding 6
    d = tmp_path / ".sure"
    d.mkdir()
    (d / "ledger.md").write_text("- 2026-01-01T00:00:00 asked [delete/hard] WHAT: echo ran [delete/hard] ; rm x\n")
    row = [r for r in sure.stats(str(tmp_path)).splitlines() if r.startswith("delete/hard")][0]
    assert row.split() == ["delete/hard", "1", "0", "0%"]


def test_malformed_shape_is_logged_not_silent_forever(tmp_path):  # finding 7
    out = sure.main(["pre"], json.dumps({"tool_name": "Bash", "tool_input": "oops"}), str(tmp_path))
    assert out == ""
    assert "error" in (tmp_path / ".sure" / "ledger.md").read_text()


def test_long_command_is_fast():  # finding 8
    start = time.time()
    kind("curl x " * 40000)
    assert time.time() - start < 1.0
