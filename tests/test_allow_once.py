"""One-time allow codes: a call Sure? stopped while nobody was at the board can be allowed once by the owner."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SURE_DIR = Path(__file__).resolve().parent.parent / "sure"
sys.path.insert(0, str(SURE_DIR))
import allow  # noqa: E402
import sure  # noqa: E402

CARDED = {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}, "session_id": "s1"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SURE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SURE_CLIENT", raising=False)
    return tmp_path


def run_hook(tmp_path, data=CARDED):
    return sure.main(["pre"], json.dumps(data), str(tmp_path))


def decision(out):
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None


def h(cmd="rm -rf build"):
    return allow.cmd_hash("Bash", {"command": cmd})


def test_code_redeems_once():
    allow.issue(h(), "deletes files", now=1000)
    (code, what, _), = allow.pending(now=1001)
    assert len(code) == 6 and what == "deletes files"
    assert allow.redeem(code, now=1002) is True
    assert allow.consume(h(), now=1003) is True
    assert allow.consume(h(), now=1004) is False  # spent
    assert allow.redeem(code, now=1005) is False  # the code is spent too


def test_code_rejected_for_other_command():
    allow.issue(h("rm -rf build"), "deletes", now=1000)
    (code, _, _), = allow.pending(now=1000)
    assert allow.redeem(code, now=1001) is True
    assert allow.consume(h("rm -rf other"), now=1002) is False
    assert allow.redeem("ZZZZZZ", now=1003) is False


def test_code_expires_after_ttl():
    allow.issue(h(), "x", now=1000)
    (code, _, _), = allow.pending(now=1000)
    assert allow.pending(now=1000 + 601) == []
    assert allow.redeem(code, now=1000 + 601) is False
    allow.issue(h(), "x", now=2000)
    (code, _, _), = allow.pending(now=2000)
    assert allow.redeem(code, now=2001) is True
    assert allow.consume(h(), now=2001 + 601) is False  # an unused allowance also expires


def test_lock_after_five_wrong_tries_then_recovers():
    allow.issue(h(), "x", now=1000)
    (code, _, _), = allow.pending(now=1000)
    for i in range(5):
        assert allow.redeem("AAAAA" + str(i), now=1001 + i) is False
    assert allow.locked_for(now=1010) > 0
    assert allow.redeem(code, now=1010) is False  # even the right code, while locked
    allow.issue(h(), "x", now=1700)
    (code2, _, _), = allow.pending(now=1700)
    assert allow.redeem(code2, now=1700) is True  # lock over (600 s)


def test_state_file_never_holds_the_code():
    allow.issue(h(), "x", now=1000)
    (code, _, _), = allow.pending(now=1000)
    text = (Path(os.environ["SURE_HOME"]) / ".sure" / "allow.json").read_text(encoding="utf-8")
    assert code not in text and h() in text


def test_ken_client_gets_a_pending_code_but_it_is_never_in_the_hook_output(home, monkeypatch):
    monkeypatch.setenv("SURE_CLIENT", "ken")
    out = run_hook(home)
    assert decision(out) == "ask"
    (code, _, _), = allow.pending()
    assert code not in out and code.lower() not in out.lower()


def test_hook_allows_exactly_once_after_the_owner_redeems(home, monkeypatch):
    monkeypatch.setenv("SURE_CLIENT", "ken")
    assert decision(run_hook(home)) == "ask"
    (code, _, _), = allow.pending()
    assert allow.redeem(code) is True
    assert decision(run_hook(home)) == "allow"
    assert decision(run_hook(home)) == "ask"  # spent


def test_a_different_command_is_not_allowed_by_that_code(home, monkeypatch):
    monkeypatch.setenv("SURE_CLIENT", "ken")
    run_hook(home)
    (code, _, _), = allow.pending()
    allow.redeem(code)
    other = dict(CARDED, tool_input={"command": "rm -rf dist"})
    assert decision(run_hook(home, other)) == "ask"


def test_other_clients_leave_nothing_pending(home):
    assert decision(run_hook(home)) == "ask"  # Claude Code's own prompt decides
    assert allow.pending() == []


def test_cli_pending_and_allow_round_trip(home):
    env = {**os.environ, "SURE_HOME": os.environ["SURE_HOME"]}
    allow.issue(h(), "deletes files")
    pend = subprocess.run([sys.executable, str(SURE_DIR / "sure.py"), "pending"], capture_output=True, text=True, env=env)
    (code, _, _), = allow.pending()
    assert code in pend.stdout and "deletes files" in pend.stdout
    bad = subprocess.run([sys.executable, str(SURE_DIR / "sure.py"), "allow", "NOPE00"], capture_output=True, text=True, env=env)
    assert "allowed" not in bad.stdout.lower() or "not" in bad.stdout.lower()
    ok = subprocess.run([sys.executable, str(SURE_DIR / "sure.py"), "allow", code], capture_output=True, text=True, env=env)
    assert "allowed once" in ok.stdout.lower()
    assert allow.consume(h()) is True


@pytest.mark.parametrize("cmd", ["python sure/sure.py allow ABC123", "python sure/sure/sure.py pending"])
def test_the_agent_running_allow_or_pending_is_itself_carded(cmd):
    risk = sure.classify("Bash", {"command": cmd})
    assert risk and risk["kind"] == "settings_or_mode"
