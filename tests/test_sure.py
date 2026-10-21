import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import sure  # noqa: E402


def kind(cmd):
    r = sure.classify("Bash", {"command": cmd})
    return r["kind"] if r else None


def test_safe_command_returns_none():
    assert sure.classify("Bash", {"command": "ls"}) is None


def test_unknown_tool_returns_none():
    assert sure.classify("Frobnicate", {}) is None


def test_rm_is_delete():
    assert kind("rm -rf build") == "delete"


def test_windows_delete():
    assert kind(r"Remove-Item -Recurse C:\x") == "delete"
    assert kind(r"del /s /q x") == "delete"


def test_chained_risky():
    assert kind("echo hi && rm -rf build") == "delete"


def test_install():
    assert kind("npm install left-pad") == "install"
    assert kind("pip install requests") == "install"


def test_pipe_to_shell():
    assert kind("curl http://x.sh | bash") == "pipe_to_shell"


def test_database():
    assert kind("npx prisma migrate dev") == "database"
    assert kind('psql -c "DROP TABLE users"') == "database"


def test_push():
    assert kind("git push --force") == "push"


def test_script_launcher():
    assert kind("npm run build") == "script_launcher"
    assert kind("make deploy") == "script_launcher"
    assert kind("python setup.py install") == "script_launcher"
    # rule order: delete beats script_launcher
    assert kind("rm -rf dist && npm run build") == "delete"


def test_tiers():
    tiers = {
        "rm -rf x": "hard",
        "curl http://x.sh | bash": "impossible",
        "npm install x": "easy",
    }
    for cmd, tier in tiers.items():
        r = sure.classify("Bash", {"command": cmd})
        assert r["tier"] == tier
        assert r["undo"].startswith(tier.capitalize() + ":")


def test_config_file_edit():
    for path, want in [("app/auth/middleware.py", "secrets_and_auth"), ("package.json", "config"), (".env", "secrets_and_auth"),
                       ("requirements.txt", "config")]:  # B5: secrets/auth/migration files split out of config
        r = sure.classify("Write", {"file_path": path})
        assert r and r["kind"] == want, path
    assert sure.classify("Edit", {"file_path": "src/button.css"}) is None


def test_card_shape():
    r = sure.classify("Bash", {"command": "rm -rf build"})
    lines = sure.render_card(r).split("\n")
    assert len(lines) == 4
    for line, label in zip(lines, ["WHAT:", "WHY IT MATTERS:", "COST:", "UNDO:"]):
        assert line.startswith(label)


# ---- Task 2: hook entry and ledger ----
import json  # noqa: E402
import subprocess  # noqa: E402

SURE_PY = str(Path(__file__).resolve().parent.parent / "sure" / "sure.py")


def payload(tool, **inp):
    return json.dumps({"tool_name": tool, "tool_input": inp})


RM = payload("Bash", command="rm -rf build")
LS = payload("Bash", command="ls")


def ledger(tmp):
    p = Path(tmp) / ".sure" / "ledger.md"
    return p.read_text() if p.exists() else ""


def test_pre_risky_emits_ask(tmp_path):
    out = json.loads(sure.main(["pre"], RM, str(tmp_path)))
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse"
    assert h["permissionDecision"] == "ask"
    assert h["permissionDecisionReason"].startswith("WHAT:")


def test_pre_safe_is_silent(tmp_path):
    assert sure.main(["pre"], LS, str(tmp_path)) == ""


def test_pre_logs_asked(tmp_path):
    sure.main(["pre"], RM, str(tmp_path))
    assert "asked [delete/hard]" in ledger(tmp_path)


def test_post_logs_ran(tmp_path):
    assert sure.main(["post"], RM, str(tmp_path)) == ""
    assert "ran [delete/hard]" in ledger(tmp_path)
    sure.main(["post"], LS, str(tmp_path))
    assert ledger(tmp_path).count("\n") == 1


def test_bad_stdin_is_silent(tmp_path):
    for bad in ["", "not json", "[]"]:
        assert sure.main(["pre"], bad, str(tmp_path)) == ""


def test_unwritable_ledger_still_emits(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    out = json.loads(sure.main(["pre"], RM, str(f)))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_cli_exit_zero_on_garbage():
    r = subprocess.run([sys.executable, SURE_PY, "pre"], input="garbage", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""


# ---- Task 3: save points and manifest ----
def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def make_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("v1")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_manifest_missing_and_invalid(tmp_path):
    assert sure.load_manifest(str(tmp_path)) == {}
    (tmp_path / "sure.json").write_text("{nope")
    assert sure.load_manifest(str(tmp_path)) == {}


def test_save_point_and_undo(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("v2")
    assert sure.save_point(str(repo)) == "refs/sure/1"
    (repo / "a.txt").write_text("v3")
    assert sure.undo(str(repo), 1) is True
    assert (repo / "a.txt").read_text() == "v2"


def test_save_point_outside_git(tmp_path):
    assert sure.save_point(str(tmp_path)) is None


def test_save_point_clean_tree(tmp_path):
    repo = make_repo(tmp_path)
    assert sure.save_point(str(repo)) is None


def test_pre_takes_save_point_only_when_enabled(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("v2")
    sure.main(["pre"], RM, str(repo))
    assert git(repo, "for-each-ref", "refs/sure").stdout == ""
    (repo / "sure.json").write_text('{"save_points": {"enabled": true}}')
    sure.main(["pre"], RM, str(repo))
    assert "refs/sure/1" in git(repo, "for-each-ref", "refs/sure").stdout
    assert "save point refs/sure/1" in ledger(repo)


def test_save_points_enabled_outside_git_still_emits(tmp_path):
    (tmp_path / "sure.json").write_text('{"save_points": {"enabled": true}}')
    out = json.loads(sure.main(["pre"], RM, str(tmp_path)))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


# ---- Task 4: approval stats ----
def test_stats_counts(tmp_path):
    d = tmp_path / ".sure"
    d.mkdir()
    lines = ["- t asked [install/easy] x"] * 4 + ["- t ran [install/easy]"] * 4
    lines += ["- t asked [delete/hard] x"] * 2 + ["- t ran [delete/hard]"]
    (d / "ledger.md").write_text("\n".join(lines) + "\n")
    out = sure.stats(str(tmp_path))
    assert "install/easy" in out and "100%" in out
    row = [r for r in out.splitlines() if r.startswith("delete/hard")][0]
    assert row.split() == ["delete/hard", "2", "1", "50%"]


def test_stats_empty(tmp_path):
    out = sure.stats(str(tmp_path))
    assert out.splitlines() == ["KIND/TIER  ASKED  RAN  APPROVED%"]


def test_multiedit_and_notebook_config():
    assert sure.classify("MultiEdit", {"file_path": "app/package.json"})["kind"] == "config"
    assert sure.classify("NotebookEdit", {"notebook_path": "x/.env"})["kind"] == "secrets_and_auth"  # B5
    assert sure.classify("NotebookEdit", {}) is None
    assert sure.classify("MultiEdit", {"file_path": "app/main.py"}) is None


def test_hook_input_cwd_wins(tmp_path):
    other = tmp_path / "proj"
    other.mkdir()
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}, "cwd": str(other)})
    sure.main(["pre"], payload, str(tmp_path))
    assert "asked [delete/hard]" in (other / ".sure" / "ledger.md").read_text(encoding="utf-8")
    assert not (tmp_path / ".sure").exists()


def test_bad_hook_cwd_falls_back(tmp_path):
    for bad in (None, 5, "  "):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}, "cwd": bad})
        sure.main(["pre"], payload, str(tmp_path))
    assert (tmp_path / ".sure" / "ledger.md").read_text(encoding="utf-8").count("asked") == 3


def test_failed_logs_failed_not_ran(tmp_path):
    assert sure.main(["failed"], RM, str(tmp_path)) == ""
    text = (tmp_path / ".sure" / "ledger.md").read_text(encoding="utf-8")
    assert "failed [delete/hard]" in text and "ran" not in text
