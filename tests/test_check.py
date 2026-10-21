"""`sure.py check`: a dry run that prints the card for a command or file, running and writing nothing."""
import subprocess
import sys
from pathlib import Path

SURE = str(Path(__file__).resolve().parent.parent / "sure" / "sure.py")


def check(*args):
    r = subprocess.run([sys.executable, SURE, "check", *args], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    return r.stdout


def test_check_shows_the_card_for_a_risky_command():
    out = check("rm", "-rf", "demo")
    assert "WHAT:" in out and "UNDO:" in out and "delete" in out


def test_check_says_quiet_for_a_safe_command():
    assert "stay quiet" in check("ls", "-la")


def test_check_path_shows_a_file_card():
    assert "agent_config_edit" in check("--path", "CLAUDE.md")


def test_check_runs_nothing(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("x")
    check("rm", str(victim))
    assert victim.exists()
