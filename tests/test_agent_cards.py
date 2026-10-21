"""agent_config_edit (file paths) and agent_autonomy (commands): risky and safe look-alikes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import sure  # noqa: E402

RISKY = [
    "CLAUDE.md", "packages/api/CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "services/x/AGENTS.override.md",
    "GEMINI.md", ".cursorrules", ".windsurfrules", ".clinerules", ".github/copilot-instructions.md",
    ".github/instructions/api/x.instructions.md", ".aider.conf.yml", ".cursor/rules/style.mdc",
    ".windsurf/rules/a.md", ".mcp.json", ".cursor/mcp.json", r"C:\Users\me\.cursor\mcp.json",
    ".agents/skills/x/SKILL.md", ".gemini/commands/x.toml", ".codex/prompts/x.md", ".cursor/agents/s.md",
    ".codex/memories/p.md", ".claude.json", ".codex/config.toml", ".gemini/settings.json", "opencode.json", "CLAUDE.md.", ".github/prompts/x.prompt.md", "AGENT.md", ".roo/rules/a.md", ".kiro/steering/a.md", "plugins/t/skills/f/SKILL.md", ".codeium/windsurf/memories/g.md",
]
SAFE = [
    "README.md", "docs/guide.md", "src/claude_client.py", "docs/agents-overview.md", "src/mcp_client.py",
    "notes/CLAUDE-notes.txt", "src/rules/engine.py", "tests/test_skills.py", "src/mcp.json", "tests/fixtures/mcp.json", "vendor/lib/SKILL.md", "docs/agent-memory/index.md",
]


@pytest.mark.parametrize("path", RISKY)
@pytest.mark.parametrize("tool", ["Edit", "Write"])
def test_agent_config_paths_get_a_card(tool, path):
    r = sure.classify(tool, {"file_path": path})
    assert r and r["kind"] == "agent_config_edit" and r["tier"] == "hard"


@pytest.mark.parametrize("path", SAFE)
def test_ordinary_paths_are_not_agent_config(path):
    r = sure.classify("Edit", {"file_path": path})
    assert not r or r["kind"] != "agent_config_edit"


@pytest.mark.parametrize("cmd", [
    "claude " + "x " * 1900,
    "npx " + "-y " * 1300,
    "codex " + "x " * 1900,
    "aider " + "--x " * 900,
    "gemini " + "-p " * 1300,
    "claude " + "mcp " * 900,
    "AIDER_YES_ALWAYS=" * 230,
])
def test_agent_patterns_linear_on_long_input(cmd):
    import time
    t = time.perf_counter()
    sure.classify("Bash", {"command": cmd[:4000]})
    assert time.perf_counter() - t < 0.05
