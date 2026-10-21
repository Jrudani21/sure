import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import directory  # noqa: E402
import sure  # noqa: E402

K = [
    ("WebFetch", {"url": "https://x.com"}, "network_fetch", "https://x.com"),
    ("WebSearch", {"query": "cats"}, "web_search", "cats"),
    ("mcp__slack__send", {}, "external_tool", "mcp__slack__send"),
    ("Task", {"prompt": "x"}, "subagent", "x"),
    ("Agent", {"description": "d", "prompt": "x"}, "subagent", "d"),
    ("Write", {"file_path": "proj/.git/config"}, "protected_path_write", ".git/config"),
    ("Edit", {"file_path": "proj/.git/hooks/pre-commit"}, "protected_path_write", "pre-commit"),
    ("Write", {"file_path": "C:" + chr(92) + "p" + chr(92) + ".claude" + chr(92) + "a.md"}, "protected_path_write", "a.md"),
    ("Edit", {"file_path": ".claude/settings.json"}, "settings_or_mode", "settings.json"),
    ("MultiEdit", {"file_path": "p/.claude/settings.local.json"}, "settings_or_mode", "settings.local.json"),
    ("Write", {"file_path": "package.json"}, "config", "package.json"),
]


@pytest.mark.parametrize("tool,inp,kind,frag", K)
def test_kinds(tool, inp, kind, frag):
    r = sure.classify(tool, inp)
    assert r["kind"] == kind and frag in r["what"]


def test_tiers():
    t = {e["id"]: e["tier"] for e in directory.load()}
    assert [t[k] for k in ("network_fetch", "web_search", "external_tool", "subagent", "protected_path_write", "settings_or_mode")] == ["easy", "easy", "hard", "easy", "hard", "hard"]


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob"])
def test_reads_stay_silent(tool):
    assert sure.classify(tool, {"file_path": "proj/.git/config", "pattern": "x"}) is None


def test_plain_writes_stay_silent():
    assert sure.classify("Write", {"file_path": "src/a.py"}) is None
    assert sure.classify("Write", {"file_path": "docs/.github/x.md"}) is None


def test_hook_matcher_covers_new_tools():
    for t in ("WebFetch", "WebSearch", "Task", "Agent", "mcp__"):
        assert t in sure._MATCHER


@pytest.mark.parametrize(
    "cmd",
    [
        "chmod -R 777 /var/www",
        "sudo chmod -R 0777 .",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "mkfs.ext4 /dev/sdb1",
        ":(){ :|:& };:",
        "kill -9 -1",
        "echo x > /etc/sudoers",
    ],
)
def test_ken_seeded_system_damage(cmd):
    assert sure.classify("Bash", {"command": cmd})["kind"] == "system_damage"


@pytest.mark.parametrize("cmd", ["chmod 644 a.txt", "dd if=a.iso of=out.img", "kill -9 1234", "echo mkfs", "cat /etc/hosts"])
def test_system_damage_no_false_positive(cmd):
    assert sure.classify("Bash", {"command": cmd}) is None


def _pre(tmp_path, tool, inp, manifest=None):
    import json

    if manifest is not None:
        (tmp_path / "sure.json").write_text(json.dumps(manifest))
    return sure.main(["pre"], json.dumps({"tool_name": tool, "tool_input": inp}), str(tmp_path))


@pytest.mark.parametrize("name", ["mcp__slack__list_channels", "mcp__gmail__search_threads", "mcp__a__get_x", "mcp__a__fetch_page",
                                  "mcp__listonly", "mcp__list"])  # B1: no mutating word = quiet
def test_mcp_read_verbs_quiet(name):
    r = sure.classify(name, {})
    assert r["kind"] == "external_tool" and r["quiet"]


@pytest.mark.parametrize("name", ["mcp__slack__send_message", "mcp__x__delete_thing"])
def test_mcp_other_ask(name):
    r = sure.classify(name, {})
    assert r["kind"] == "external_tool" and not r["quiet"]


def test_quiet_defaults():
    for t, i in (("WebFetch", {"url": "u"}), ("WebSearch", {"query": "q"}), ("Task", {"prompt": "p"})):
        assert sure.classify(t, i)["quiet"]
    assert not sure.classify("Write", {"file_path": ".git/config"})["quiet"]


def test_quiet_logs_and_returns_empty(tmp_path):
    assert _pre(tmp_path, "WebFetch", {"url": "https://x.com"}) == ""
    assert "quiet [network_fetch/easy]" in (tmp_path / ".sure" / "ledger.md").read_text()
    assert not (tmp_path / ".sure" / "ledger.md").read_text().count("asked")


def test_ask_on_reenables_card(tmp_path):
    assert "permissionDecision" in _pre(tmp_path, "WebFetch", {"url": "u"}, {"ask_on": ["network_fetch"]})
    assert _pre(tmp_path, "WebSearch", {"query": "q"}, {"ask_on": "network_fetch"}) == ""  # non-list ignored


def test_hard_mcp_still_asks(tmp_path):
    assert "permissionDecision" in _pre(tmp_path, "mcp__slack__send_message", {})
    assert _pre(tmp_path, "mcp__slack__list_channels", {}) == ""


def test_validate_rejects_bad_default():
    es = directory.load()
    es[0]["default"] = "loud"
    with pytest.raises(ValueError, match=es[0]["id"]):
        directory.validate(es)


def test_dd_dev_null_not_flagged():
    assert sure.classify("Bash", {"command": "dd if=a of=/dev/null"}) is None
    assert sure.classify("Bash", {"command": "dd if=a of=/dev/sda"})["kind"] == "system_damage"


def test_backslash_settings_path():
    assert sure.classify("Edit", {"file_path": "p" + chr(92) + ".claude" + chr(92) + "settings.json"})["kind"] == "settings_or_mode"
