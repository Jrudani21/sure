"""Things a public release must not do: leak the author's machine, trust the wrong environment variable."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURE_PY = str(ROOT / "sure" / "sure.py")
sys.path.insert(0, str(ROOT / "sure"))
import allow  # noqa: E402
import sure  # noqa: E402

PRIVATE = ["ken" + "-desktop", "E:" + "/Local", "E:" + chr(92) + "Local", "Janak" + "'s"]  # built at run time: this file is scanned too


def test_no_private_paths_in_shipped_files():
    hits = []
    for base in ("sure", "directory", "tests", "examples", "scripts"):
        for p in (ROOT / base).rglob("*"):
            if p.is_file() and p.suffix in (".py", ".json", ".md", ".html", ".js", ".css") and "__pycache__" not in p.parts:
                text = p.read_text(encoding="utf-8", errors="replace")
                hits += [f"{p.relative_to(ROOT)}: {s}" for s in PRIVATE if s in text]
    hits += [f"README.md: {s}" for s in PRIVATE if s in (ROOT / "README.md").read_text(encoding="utf-8")]
    assert not hits, hits


def _unverified_rules(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": [{
        "id": "internal_deploy", "name": "Deploy", "touches": "run_commands", "tier": "hard", "lasts": "once",
        "decided_by": ["you"], "what": "Deploys: {target}", "why": "w", "cost": "c", "undo": "u",
        "matchers": [{"tool": "Bash", "scope": "seg", "pattern": "^deploy-prod\\b"}]}], "tests": []}), encoding="utf-8")
    return rules


def _check(rules, extra_env):
    env = {**os.environ, "SURE_RULES": str(rules), **extra_env}
    env.pop("SURE_RULES_TRUST", None) if "SURE_RULES_TRUST" not in extra_env else None
    return subprocess.run([sys.executable, SURE_PY, "check", "deploy-prod now"], capture_output=True, text=True, env=env).stdout


def test_the_rule_used_by_the_trust_test_really_cards_once_verified(tmp_path):
    rules = _unverified_rules(tmp_path)
    env = {**os.environ, "SURE_RULES": str(rules)}
    assert subprocess.run([sys.executable, SURE_PY, "test"], env=env, capture_output=True).returncode == 0
    assert "KIND: internal_deploy" in _check(rules, {})  # proves the pattern is valid, so the next test can fail


def test_rules_trust_env_is_ignored_outside_the_selftest(tmp_path):
    rules = _unverified_rules(tmp_path)  # never verified, trust flag set in the hook's own environment
    assert "would stay quiet" in _check(rules, {"SURE_RULES_TRUST": "1"})


def test_client_name_no_ask_also_issues_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("SURE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SURE_CLIENT", "no-ask")
    sure.main(["pre"], json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}), str(tmp_path))
    assert len(allow.pending()) == 1


def test_partial_allow_state_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("SURE_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".sure").mkdir(parents=True)
    (tmp_path / "home" / ".sure" / "allow.json").write_text(json.dumps({"key": "ab" * 32}), encoding="utf-8")
    assert allow.pending() == []
    allow.issue("h" * 64, "x")
    assert len(allow.pending()) == 1 and allow.redeem("NOPE00") is False


def test_python_unwrapping_cannot_blow_up_the_text():
    assigns = "; ".join(f"v{i}='" + "x" * 280 + "'" for i in range(10))
    use = "print(" + " + ".join(f"v{i}" for i in range(10)) * 60 + ")"
    cmd = "python -c \"" + assigns + "; " + use + "\""
    assert len(sure._pydeobf(cmd[:sure.MAX_CMD])) <= sure.MAX_CMD * 4
