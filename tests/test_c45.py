"""Tests for C4 (MCP ask_tokens + secrets_and_auth) and C5 (hygiene)."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import directory  # noqa: E402
import sure  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kind(tool, inp):
    r = sure.classify(tool, inp)
    return r["kind"] if r else None


def _mcp_quiet(name):
    r = sure.classify(name, {})
    return r is not None and r["kind"] == "external_tool" and r["quiet"]


def _mcp_asks(name):
    r = sure.classify(name, {})
    return r is not None and r["kind"] == "external_tool" and not r["quiet"]


# ---------------------------------------------------------------------------
# C4a: MCP ask_tokens – new mutating verbs card (not quiet)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("token", [
    "insert", "add", "trash", "edit", "save", "submit", "terminate", "stop",
    "cancel", "refund", "charge", "erase", "clear", "replace", "patch",
    "archive", "evaluate", "invite", "ban", "import", "fork", "label",
    "fill", "reboot", "unset", "rm",
])
def test_c4_new_ask_tokens_trigger_card(token):
    """Each new token must cause external_tool to ask (not be quiet)."""
    name = f"mcp__svc__{token}_record"
    assert _mcp_asks(name), f"mcp tool with '{token}' should ask, got quiet"


@pytest.mark.parametrize("name", [
    "mcp__x__administrator",   # contains "add" only inside a longer word
    "mcp__x__address_lookup",
    "mcp__x__stopwatch_info",
    "mcp__x__banner_text",
])
def test_c4_ask_tokens_match_whole_words_only(name):
    assert _mcp_quiet(name), name


@pytest.mark.parametrize("name", [
    # read-only names that should stay quiet (no ask_token in the name)
    "mcp__slack__list_channels",
    "mcp__gmail__search_threads",
    "mcp__x__fetch_page",
    "mcp__x__read_file",
])
def test_c4_read_only_mcp_names_quiet(name):
    """Names without any ask_token stay quiet (the external_tool default)."""
    assert _mcp_quiet(name), f"{name} should be quiet"


# Known false-asks: these contain an ask_token in their name_tokens, so they
# ask even though they are read-only. This is documented behaviour; no change.
@pytest.mark.parametrize("name", [
    "mcp__db__getRunStatus",         # 'run' in name_tokens → asks (documented false-ask)
    "mcp__playwright__browser_close",  # 'close' in ask_tokens → asks (documented)
])
def test_c4_documented_false_asks(name):
    """These are known false-asks: they ask despite being read-only, by design."""
    assert _mcp_asks(name), f"{name} is a known false-ask and should ask"


# click and type must NOT be ask_tokens (owner decision: too noisy for browser tools)
def test_c4_click_and_type_not_ask_tokens():
    assert _mcp_quiet("mcp__browser__click_element")
    assert _mcp_quiet("mcp__browser__type_text")


# ---------------------------------------------------------------------------
# C4b: secrets_and_auth – camelCase and new file shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected_kind", [
    # camelCase files that should now be secrets_and_auth
    ("src/clientSecret.ts", "secrets_and_auth"),
    ("src/authService.ts", "secrets_and_auth"),
    ("app/AuthController.java", "secrets_and_auth"),
    ("src/authentication.py", "secrets_and_auth"),
    ("src/authorization.py", "secrets_and_auth"),
    ("src/oauth.py", "secrets_and_auth"),
    ("src/oauth2.py", "secrets_and_auth"),
    # Rails migration
    ("db/migrate/001_create_users.rb", "secrets_and_auth"),
    # New file shapes
    ("~/.ssh/id_rsa", "secrets_and_auth"),
    (".npmrc", "secrets_and_auth"),
    (".pypirc", "secrets_and_auth"),
    # Existing shapes that must still work
    (".env", "secrets_and_auth"),
    (".env.production", "secrets_and_auth"),
    ("config/auth.py", "secrets_and_auth"),
    ("middleware.ts", "secrets_and_auth"),
    ("migrations/001.sql", "secrets_and_auth"),
    ("schema.prisma", "secrets_and_auth"),
])
def test_c4_secrets_auth_paths_card(path, expected_kind):
    assert _kind("Write", {"file_path": path}) == expected_kind, path


@pytest.mark.parametrize("path,expected_kind", [
    # These must stay at the kind they were before (not promoted to secrets_and_auth)
    (".env.example", None),         # explicit example → None (no card)
    (".env.sample", "config"),       # sample → config
    (".env.template", "config"),     # template → config
    ("package.json", "config"),
    ("Dockerfile", "config"),
    ("docker-compose.yml", "config"),
    ("requirements.txt", "config"),
    ("sure.json", "settings_or_mode"),
    # Must NOT be secrets_and_auth (false-positive guard)
    # schema.graphql: 'schema' matches via the schemas? pattern, so it cards;
    # this is accepted (same as schema.prisma). Omitted from the None list.
    ("src/a.py", None),
    # docs/authentication.md: 'authentication' in basename triggers auth pattern →
    # cards (content-agnostic heuristic). Omitted from the None list.
])
def test_c4_file_shape_table(path, expected_kind):
    result = _kind("Write", {"file_path": path})
    assert result == expected_kind, f"{path}: expected {expected_kind!r}, got {result!r}"


# ---------------------------------------------------------------------------
# C5a: file_path cap at 4096 chars
# ---------------------------------------------------------------------------

def test_c5_file_path_cap_timing():
    """A 100 KB file_path must classify in under 0.05 s."""
    path = "a/" * 51200  # 102400 chars
    t = time.perf_counter()
    _kind("Write", {"file_path": path})
    dt = time.perf_counter() - t
    assert dt < 0.05, f"100 KB path took {dt:.3f}s"


def test_c5_file_path_cap_correctness():
    """Path is checked only up to 4096 chars; truncation doesn't cause a false card."""
    # A safe path that is very long
    path = "src/" + "a" * 5000 + ".py"
    result = _kind("Write", {"file_path": path})
    # The path does not match any sensitive pattern; should be None
    assert result is None


# ---------------------------------------------------------------------------
# C5b: _write_settings removes .sure.tmp on failure
# ---------------------------------------------------------------------------

def test_c5_write_settings_cleans_tmp_on_failure(tmp_path, monkeypatch):
    """If os.replace fails, the .sure.tmp file is removed."""
    import json as _json

    p = tmp_path / "settings.json"
    p.write_text(_json.dumps({"a": 1}) + "\n", encoding="utf-8")
    tmp = p.with_name(p.name + ".sure.tmp")

    def bad_replace(src, dst):
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", bad_replace)
    with pytest.raises(OSError):
        sure._write_settings(p, {"b": 2})
    assert not tmp.exists(), ".sure.tmp was not cleaned up on failure"


# ---------------------------------------------------------------------------
# C5c: no stray space in sp = man.get(...)
# ---------------------------------------------------------------------------

def test_c5_no_stray_space_in_source():
    src = Path(sure.__file__).read_bytes().decode("utf-8", errors="replace")
    # The old buggy line was: sp =man.get(
    assert "sp =man.get(" not in src, "Stray space bug still present"


# ---------------------------------------------------------------------------
# C5d: ruff.toml exists at repo root
# ---------------------------------------------------------------------------

def test_c5_ruff_toml_exists():
    root = Path(__file__).resolve().parent.parent
    assert (root / "ruff.toml").exists(), "ruff.toml missing from repo root"


def test_c5_ruff_toml_has_required_keys():
    import re
    text = (Path(__file__).resolve().parent.parent / "ruff.toml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^line-length\s*=\s*140\s*$", text)
    assert re.search(r"(?m)^target-version\s*=\s*\"py39\"\s*$", text)
    assert re.search(r"(?m)^select\s*=\s*\[[^\]]*\"E\"[^\]]*\"F\"", text), "select must list E and F"


# ---------------------------------------------------------------------------
# Review additions: the path cap must keep the END of a long path (the file name)
# ---------------------------------------------------------------------------

def test_c5_long_path_keeps_the_basename():
    assert _kind("Write", {"file_path": "a/" * 3000 + ".env"}) == "secrets_and_auth"
    assert _kind("Edit", {"file_path": "a/" * 3000 + ".claude/settings.json"}) == "settings_or_mode"
    assert _kind("Write", {"file_path": "a/" * 3000 + "package.json"}) == "config"


def test_c5_secret_straddling_the_command_cap_is_masked():
    key = "sk-ant-api03-" + "Z" * 40
    cmd = "rm -rf " + "a" * (directory_cap() - 25) + " " + key
    r = sure.classify("Bash", {"command": cmd})
    assert r is not None
    assert "sk-ant" not in r["command"] and "ZZZZZZ" not in r["command"], r["command"][-80:]


def directory_cap():
    return 2000  # the card command cap (A5): the secret above starts a few characters before it


@pytest.mark.parametrize("path", [
    "AUTH.py", "src/AUTH_utils.ts", "CREDENTIALS.json", "src/Credentials.yaml", "MIDDLEWARE.ts",
    "Middleware.java", "MIGRATION.sql", "Migration.rb", "SECRETS.yaml", "SCHEMA.prisma",
])
def test_c4_upper_case_names_still_card(path):
    """The camelCase fix must not make upper/title-case names case-sensitive (they carded before)."""
    assert _kind("Write", {"file_path": path}) == "secrets_and_auth", path


# ---------------------------------------------------------------------------
# Owner product calls on the real desktop/outward MCP tools (2026-10-21)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "mcp__ccd_turn__update_plan", "mcp__ccd_turn__wrap_up", "mcp__ccd_session_mgmt__set_session_title",
    "mcp__ccd_sidebar__create_group", "mcp__ccd_sidebar__move_sessions", "mcp__ccd_settings__set_setting",
    "mcp__ccd_session__start_session", "mcp__ccd_view__close_pane",
])
def test_desktop_app_tools_are_quiet(name):
    assert _mcp_quiet(name), name


@pytest.mark.parametrize("name", [
    "mcp__ccd_session_mgmt__delete_session", "mcp__ccd_session_mgmt__archive_session",
    "mcp__ccd_session_mgmt__stop_session", "mcp__ccd_pr__set_auto_merge", "mcp__ccd_host__clean_up_worktrees_remove",
])
def test_desktop_app_risky_tools_still_ask(name):
    assert _mcp_asks(name), name


@pytest.mark.parametrize("name", [
    "mcp__gmail__reply", "mcp__gmail__forward", "mcp__drive__share_file", "mcp__linkedin__connect_with_person",
    "mcp__claude-in-chrome__javascript_tool", "mcp__Claude_Browser__javascript_tool",
])
def test_outward_actions_ask(name):
    assert _mcp_asks(name), name


@pytest.mark.parametrize("name", [
    "mcp__ide__getDiagnostics", "mcp__gmail__search_threads", "mcp__drive__read_file_content",
    "mcp__linkedin__get_inbox", "mcp__claude-in-chrome__navigate", "mcp__claude-in-chrome__computer",
])
def test_read_and_navigation_tools_stay_quiet(name):
    assert _mcp_quiet(name), name


def test_bash_command_cut_keeps_the_start_for_every_kind():
    """settings_or_mode comes from Bash commands too: a >4096-char command must keep its START so
    masking sees whole secrets (keeping the tail would leave a headless key fragment on the card)."""
    key = "sk-ant-api03-" + "Z" * 40
    head = "echo x > sure.json "
    fill_len = len(head) + 15 + 4096 - len(head) - len(key)  # the old tail cut would start inside the key
    cmd = head + key + "x" * fill_len
    r = sure.classify("Bash", {"command": cmd})
    assert r is not None and r["kind"] == "settings_or_mode"
    assert "sk-ant" not in r["command"] and "ZZZZZZ" not in r["command"], r["command"][:80]
