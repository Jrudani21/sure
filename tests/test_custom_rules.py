"""Custom rules file: users may add or tighten rules; they take effect only after `sure.py test` verified them."""
import json
import os
import subprocess
import sys
from pathlib import Path

SURE_DIR = Path(__file__).resolve().parent.parent / "sure"
sys.path.insert(0, str(SURE_DIR))
import directory  # noqa: E402

SURE_PY = str(SURE_DIR / "sure.py")


def entry(**kw):
    e = {"id": "internal_deploy", "name": "Deploy to production", "touches": "run_commands", "tier": "hard",
         "lasts": "once", "decided_by": ["you"], "what": "Deploys to production: {target}",
         "why": "Customers see it at once.", "cost": "A bad deploy is public.", "undo": "Roll back the release.",
         "matchers": [{"tool": "Bash", "scope": "seg", "pattern": "^deploy-prod\\b"}]}
    e.update(kw)
    return e


def rules_file(tmp_path, rules, tests=None):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"rules": rules, "tests": tests or []}), encoding="utf-8")
    return p


def sure(args, rules=None, stdin=None):
    env = {**os.environ}
    env.pop("SURE_RULES", None)
    if rules is not None:
        env["SURE_RULES"] = str(rules)
    return subprocess.run([sys.executable, SURE_PY, *args], capture_output=True, text=True, env=env, input=stdin, timeout=60)


def check(cmd, rules=None):
    return sure(["check", cmd], rules).stdout


def test_user_rule_adds_card_once_verified(tmp_path):
    p = rules_file(tmp_path, [entry()], [{"cmd": "deploy-prod now", "expect": "internal_deploy"}])
    assert "would stay quiet" in check("deploy-prod now", p)  # not verified yet: not loaded
    assert sure(["test"], p).returncode == 0
    out = check("deploy-prod now", p)
    assert "KIND: internal_deploy (hard)" in out and "Deploys to production" in out


def test_edited_file_must_be_verified_again(tmp_path):
    p = rules_file(tmp_path, [entry()], [{"cmd": "deploy-prod now", "expect": "internal_deploy"}])
    sure(["test"], p)
    p.write_text(p.read_text(encoding="utf-8").replace("deploy-prod", "ship-prod"), encoding="utf-8")
    assert "would stay quiet" in check("ship-prod now", p)


def test_user_rule_cannot_loosen_builtin():
    builtin = directory.load()
    loosen = {"id": "delete", "tier": "easy", "default": "quiet",
              "matchers": [{"tool": "Bash", "scope": "seg", "pattern": "^wipe\\b"}]}
    merged = {e["id"]: e for e in directory.merge(builtin, [loosen])}
    base = {e["id"]: e for e in builtin}
    assert merged["delete"]["tier"] == base["delete"]["tier"] == "hard"
    assert merged["delete"].get("default", "ask") == "ask"
    assert len(merged["delete"]["matchers"]) == len(base["delete"]["matchers"]) + 1  # the extra matcher is the tightening


def test_user_rule_can_raise_a_builtin_tier():
    raise_ = {"id": "delete", "tier": "impossible", "matchers": []}
    merged = {e["id"]: e for e in directory.merge(directory.load(), [raise_])}
    assert merged["delete"]["tier"] == "impossible"


def test_new_user_rule_cannot_be_quiet():
    quiet = entry(default="quiet")
    merged = {e["id"]: e for e in directory.merge(directory.load(), [quiet])}
    assert merged["internal_deploy"].get("default", "ask") == "ask"


def test_missing_user_file_is_noop(tmp_path):
    assert check("rm -rf build", tmp_path / "nope.json") == check("rm -rf build")


def test_malformed_user_file_keeps_builtins_and_reports(tmp_path):
    p = tmp_path / "rules.json"
    p.write_text("{not json", encoding="utf-8")
    assert "KIND: delete" in check("rm -rf build", p)  # the built-in rules still protect
    r = sure(["test"], p)
    assert r.returncode == 1 and "rules" in (r.stdout + r.stderr).lower()


def test_invalid_entry_is_reported_and_ignored(tmp_path):
    bad = entry(tier="super")
    p = rules_file(tmp_path, [bad])
    r = sure(["test"], p)
    assert r.returncode == 1 and "tier" in (r.stdout + r.stderr)
    assert "KIND: delete" in check("rm -rf build", p)


def test_slow_pattern_is_rejected_by_test_command(tmp_path):
    slow = entry(matchers=[{"tool": "Bash", "scope": "seg", "pattern": "^(a+)+$"}])
    p = rules_file(tmp_path, [slow])
    r = sure(["test"], p)
    assert r.returncode == 1 and "slow" in (r.stdout + r.stderr).lower()
    assert "would stay quiet" in check("aaaa", p)  # never verified, never loaded


def test_cli_test_exit_codes_and_summary(tmp_path):
    good = rules_file(tmp_path, [entry()], [{"cmd": "deploy-prod now", "expect": "internal_deploy"},
                                            {"cmd": "ls", "expect": "none"}])
    r = sure(["test"], good)
    assert r.returncode == 0 and "PASS 2 / FAIL 0" in r.stdout
    bad = rules_file(tmp_path, [entry()], [{"cmd": "deploy-prod now", "expect": "none"}])
    r = sure(["test"], bad)
    assert r.returncode == 1 and "PASS 0 / FAIL 1" in r.stdout


def test_test_command_accepts_a_path_argument(tmp_path):
    p = rules_file(tmp_path, [entry()], [{"cmd": "deploy-prod now", "expect": "internal_deploy"}])
    r = sure(["test", str(p)])
    assert r.returncode == 0 and "PASS 1 / FAIL 0" in r.stdout



def test_shipped_example_passes_its_own_tests(tmp_path):
    import shutil

    src = Path(__file__).resolve().parent.parent / "examples" / "custom-rules.json"
    dst = tmp_path / "custom-rules.json"
    shutil.copy(src, dst)  # the .ok marker lands next to the copy, not in the repo
    r = sure(["test", str(dst)])
    assert r.returncode == 0 and "PASS 4 / FAIL 0" in r.stdout
