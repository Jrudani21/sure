# Sure?

A seatbelt for [Claude Code](https://claude.com/claude-code). It stays quiet on safe actions. Before a risky one it shows a short plain-English card instead of a raw command, so you actually read what you are approving. An optional local board lets you decide by dragging the card.

```
WHAT: Permanently deletes files or discards your changes: rm -rf demo
WHY IT MATTERS: Deleted files skip the recycle bin, and discarded changes are not saved anywhere.
COST: Your work or project files could disappear.
UNDO: Hard: only if the files were saved in git or a backup beforehand.
```

Sure? is not a sandbox and not a security boundary. It helps an owner who is paying attention. If Sure? itself fails, it stays silent and Claude Code's normal prompt applies; it never breaks or blocks the agent. Read [SECURITY.md](SECURITY.md) for what it does and does not protect against.

Built and tested on Windows 11 with Python 3.9+ and the standard library only. Other platforms are untested.

## Install

```bash
git clone https://github.com/Jrudani21/sure.git
python sure/sure/sure.py install     # adds the hooks to ~/.claude/settings.json
python sure/sure/sure.py uninstall   # removes only Sure?'s own hooks
```

`install` keeps every other setting and hook, writes the file atomically, and keeps a rolling backup (`settings.json.sure.bak`) and a one-time original copy (`settings.json.sure.orig`). The PreToolUse hook gets a 130 second timeout so a board wait can finish; it returns in milliseconds when the board is off. Restart Claude Code after installing. A failing command prints one line to stderr and exits 1.

## What it flags

Commands are split on `&&`, `||`, `;`, `|`, `&` and newlines, but not inside quotes. `bash -c "..."` and `powershell -Command "..."` payloads are inspected, heredocs that feed a shell or database client are inspected, and leading words such as `sudo`, `time`, `xargs` and `git -C path` are skipped. When several kinds match, the highest tier wins.

| Kind | Examples | Tier | Card |
|---|---|---|---|
| download_and_run, pipe_to_shell, database | `curl ... \| bash`, `DROP TABLE`, `alembic`, `flyway clean` | impossible | yes |
| system_damage | `chmod -R 777`, `mkfs`, `dd of=/dev/...` | hard | yes |
| delete, push, script_launcher | `rm -rf`, `Remove-Item`, `git push --force`, `npm run`, `make` | hard | yes |
| unreadable_command | a command over 4000 characters with nothing else matched | hard | yes |
| install | `npm install`, `pip install`, `cargo add` | easy | yes |
| config | `package.json`, `requirements.txt`, `Dockerfile` | easy | yes |
| secrets_and_auth | `.env`, auth, middleware, secret, credential, migration and schema files | hard | yes |
| settings_or_mode, protected_path_write | `.claude/settings.json`, `sure.json`, `.sure/`, anything under `.git/` or `.claude/` | hard | yes |
| WebFetch, WebSearch, Task, Agent | | easy | logged only |
| MCP tools (`mcp__*`) | | hard when the name has a mutating word (delete, send, reply, forward, share, connect, update, javascript, ...) | otherwise logged only |

"Logged only" means Sure? adds no card, so Claude Code's own permission flow decides. The desktop app's own `mcp__ccd_*` tools are logged only except delete, archive, stop and merge style names. Read-only tools (Read, Grep, Glob) are not hooked.

## Commands

```bash
python sure/sure/sure.py stats          # asked / ran / approval rate per kind (run in a project)
python sure/sure/sure.py report 7       # last 7 days in plain words, with a nudge if you approve almost everything
python sure/sure/sure.py undo 1         # restore save point 1 (needs save points; it does not delete new files)
python sure/sure/sure.py board          # start the local board, prints http://127.0.0.1:<port>/#<page key>
python sure/sure/sure.py board on|off   # enable or disable the board for the current project
```

## Project options (`sure.json` in the project folder)

```json
{
  "save_points": { "enabled": true },
  "auto_allow": ["install"],
  "ask_on": ["web_search"],
  "board": { "enabled": true }
}
```

- `save_points`: a git snapshot before risky actions (tracked changes only).
- `auto_allow`: no card for these kinds. Only easy kinds are honoured; hard and impossible kinds always ask.
- `ask_on`: force a card for a kind that is normally quiet. It wins over `auto_allow`.
- `board`: when enabled, risky requests wait on the board.

Sure? reads `sure.json` and writes `.sure/` in Claude's current working folder, which can be a subfolder of your repo.

## The board

Run `board`, open the printed URL in a browser, and turn it on in a project with `board on`. A risky request becomes a card and the hook waits up to 120 seconds for you.

- Drag a card to **Allow once**, **Block**, **Ask Claude** or **Always allow** (easy kinds only; it saves `auto_allow` in that project). Every card also has real buttons for keyboard and touch use.
- Impossible-tier cards offer only **Block** and **Allow once**, and Allow once needs a second click.
- Each card shows the plain title, the tier, the full command (up to 2000 characters), why, cost, undo and a countdown.
- A flow map shows sessions, kinds and who decided. A Trusted kinds strip lists your Always allow choices with a remove button. A Replay tab shows past decisions for a project and a day range.
- If the board is not running, no browser tab is visible, the runtime file is stale, or nobody answers, Claude Code's normal prompt takes over.

"Allow once" returns a real `allow` to Claude Code. That skips Claude's own prompt, but your deny rules still win (checked by a test). Auto and bypass permission modes and `ask` rules were not tested.

## Files Sure? writes

- `~/.sure/board.json`: board port, hook key and process id while a board runs (no page key).
- `<project>/.sure/ledger.md`: readable decision log with the first 80 characters of risky commands. Secrets and URL passwords are masked.
- `<project>/.sure/events.jsonl`: kind, tier, session, decision and a short title. `.sure/.gitignore` contains `*`.
- `<project>/sure.json`: your options, and `auto_allow` when you use Always allow.
- `settings.json.sure.bak` and `settings.json.sure.orig` next to the settings file the installer changed.

The hook makes a short HTTP call to 127.0.0.1 only when a project has the board enabled. Sure? makes no other network calls and calls no model.

## Known limits

- Detection is pattern matching, not a shell parser. Unusual quoting, wrapper forms, `$(...)` inside `echo`, some PowerShell constructs (`Start-Process -Verb RunAs`, `Set-ExecutionPolicy Bypass`, `Format-Volume`, ...), and tools such as `docker`, `kubectl`, `terraform destroy`, `rsync --delete` and `truncate` can get past it.
- It cannot see what a script does once launched, and it does not inspect MCP tool input.
- Anything running as you, including the agent, can tamper with Sure? (see SECURITY.md).
- A visible but unattended board tab makes the hook wait up to 120 seconds.

## Develop

```bash
python -m pytest -q
ruff check sure tests scripts
python scripts/live_board_smoke.py    # needs SURE_SMOKE_ROOT=<a folder>, runs two real `claude -p` sessions
```

MIT licensed.
