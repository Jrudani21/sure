"""Final fix wave C: regex backtracking, replay cap, self-protection shapes, MCP/secrets tokens, hygiene."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import directory  # noqa: E402
import events  # noqa: E402
import sure  # noqa: E402


def _timed(cmd, tool="Bash"):
    t = time.perf_counter()
    r = sure.classify(tool, {"command": cmd})
    return r, time.perf_counter() - t


# ---- C1: no catastrophic backtracking in the leading-word strip ----

_N = 600
_OPTS = {
    "timeout": ["--a=b ", "--verbose ", "-s KILL ", "-k 5 ", "-foo ", "--a-b=c "],
    "sudo": ["--a=b ", "--verbose ", "-u root ", "-uroot ", "-E ", "-- "],
    "xargs": ["--a=b ", "--verbose ", "-0 ", "-n 1 ", "-I {} ", "-0pr "],
    "env": ["-i ", "-u X ", "-S ", "A=b "],
    "nice": ["-n 5 ", "-5 ", "-n -5 "],
    "command": ["-p ", "-p -p "],
    "git -C": ["-C x ", "-C . ", "-c a=b "],
}
_CASES = [(h, o) for h, os_ in _OPTS.items() for o in os_]
_BAD = ["x", "", "ls", "!", "9 9 9", "echo"]


@pytest.mark.parametrize("head,opt", _CASES)
@pytest.mark.parametrize("tail", _BAD)
def test_lead_options_never_backtrack(head, opt, tail):
    for cmd in (head + " " + opt * _N + tail, "(" * _N + head + " " + opt * 300 + tail, "A=b " * _N + tail):
        r, dt = _timed(cmd[:4000])
        assert dt < 0.05, (cmd[:40], dt)


@pytest.mark.parametrize("shape", [
    "timeout " + "--a=b " * 600 + "x", "timeout " + "--verbose " * 380 + "x", "timeout " + "-s KILL " * 400 + "x",
], ids=["a=b", "verbose", "s-KILL"])  # all under MAX_CMD (4000), so the result is None; longer ones are timed above
def test_timeout_shapes_fast_and_quiet(shape):
    r, dt = _timed(shape)
    assert r is None and dt < 0.05


@pytest.mark.parametrize("pre,unit,tail", [
    ("", "<<EOF\n", ""), ("bash -c \"", "<<EOF\n", "EOF"), ("cat <<EOF | bash\n", "<<EOF\n", "'"),
    ("", "<<-X\n", "x"), ("echo ", "<<'A' ", ""),
])
def test_heredoc_scan_bounded(pre, unit, tail):
    cmd = (pre + unit * (4000 // len(unit)))[:3990] + tail
    for _ in range(3):
        r, dt = _timed(cmd)
        assert dt < 0.05, dt


# ---- C2: Replay "what" keeps the target (cap 160, not 80) ----

def test_delete_target_visible_in_replay_what(tmp_path):
    r = sure.classify("Bash", {"command": "rm -rf node_modules/.cache/foo"})
    assert len(r["what"]) > 80  # template + target is the card's title
    sure.main(["pre"], json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf node_modules/.cache/foo"}}), str(tmp_path))
    asked = [e for e in events.read(str(tmp_path)) if e["verb"] == "asked"][0]
    assert asked["what"] == r["what"] and asked["what"].endswith("node_modules/.cache/foo")


def test_what_cap_is_160_with_ellipsis_and_masked():
    w = events._what("a" * 300)
    assert len(w) == 160 and w.endswith("...")
    assert events._what("b" * 160) == "b" * 160
    assert "ghp_" not in events._what("x " * 70 + "ghp_" + "a" * 30)


def test_ledger_fallback_what_cap_160(tmp_path):
    d = tmp_path / ".sure"
    d.mkdir()
    (d / "ledger.md").write_text(f"- {sure.datetime.now().isoformat(timespec='seconds')} asked [delete/hard] {'w' * 150}\n", encoding="utf-8")
    assert events.read(str(tmp_path))[0]["what"] == "w" * 150


# ---- C3: Sure?'s own controls are guarded against more write shapes, with the harmless ones quiet ----

def _kind(cmd, tool="Bash"):
    r = sure.classify(tool, {"command": cmd})
    return r["kind"] if r else None


@pytest.mark.parametrize("cmd", [
    "echo {}>sure.json", "echo {} >sure.json", "echo {}>>sure.json", "echo {}>.sure/board.json",
    'echo x > "sure.json"', "echo x > 'sure.json'", 'echo x >> "$HOME/.sure/board.json"',
    "dd of=sure.json", "curl --output=sure.json http://x", "f=sure.json; echo {} > $f",
    "cd .sure && echo {} > board.json", "cd ~/.sure; echo {} > board.json", 'cd "$HOME/.sure" && tee board.json',
    "Set-Location .sure; Set-Content board.json x", "pushd .sure/ && cp /tmp/b board.json", "sl .sure; Out-File board.json",
    "sed -i s/a/b/ sure.json", "perl -pi -e s/a/b/ sure.json", "tee sure.json", "cp x sure.json", "mv x sure.json",
    "Set-Content sure.json x", "Add-Content sure.json x", "Out-File sure.json", "touch sure.json", "truncate -s 0 sure.json",
    "git checkout sure.json", "git rm sure.json", "jq . a > sure.json",
    "python sure.py board", "python sure.py install", "python sure.py uninstall", 'python "/t/sure.py" uninstall',
    "bash -c 'echo {}>sure.json'",
])
def test_c3_writes_to_controls_ask(cmd):
    assert _kind(cmd, "PowerShell" if cmd.split()[0] in ("Set-Location", "Set-Content", "Add-Content", "Out-File", "sl") else "Bash") == "settings_or_mode", cmd


@pytest.mark.parametrize("cmd", ["rm sure.json", "rm -rf .sure/", "del sure.json", "Remove-Item sure.json", "git restore sure.json"])
def test_c3_deletes_still_card(cmd):
    assert _kind(cmd, "PowerShell" if cmd.startswith("Remove") else "Bash") in ("settings_or_mode", "delete"), cmd


@pytest.mark.parametrize("cmd", [
    "git add sure.json", "git diff sure.json", "git log -p sure.json", "git show HEAD:sure.json", "git status sure.json",
    "git blame sure.json", "git ls-files sure.json", 'git commit -m "update sure.json"', "git commit -am 'bump .sure/x'",
    "git grep board sure.json", "git check-ignore -v .sure/ledger.md", "git -C . diff sure.json", "git --no-pager log sure.json",
    "jq .board sure.json", "less sure.json", "more sure.json", "bat sure.json", "stat sure.json", "diff sure.json old.json",
    "Test-Path sure.json", "test -f sure.json", "[ -f sure.json ]", "cd .sure", "cd .sure/", "cd ~/.sure", "Set-Location .sure",
    "cd .sure && ls", "cd .sure && cat ledger.md", "echo sure.json", "echo see .sure/ledger.md", 'echo "sure.json"',
    "jq . sure.json > out.json", "cat sure.json > /tmp/x", "ls ensure.json", "echo x > ensure.json", "cat my.sure/x", "python sure.py stats",
])
def test_c3_harmless_stays_quiet(cmd):
    assert _kind(cmd, "PowerShell" if cmd.split()[0] in ("Test-Path", "Set-Location") else "Bash") is None, cmd


def test_c3_guard_regexes_are_fast():
    for cmd in ("cd " + "x/" * 1500 + ".sure", "echo " + ">" * 3900, "git " + "-C a " * 700 + "add", "echo " + "sure.json " * 390 + ">"):
        r, dt = _timed(cmd[:4000])
        assert dt < 0.05, (cmd[:30], dt)
