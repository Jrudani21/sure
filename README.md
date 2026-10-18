# Sure?

A tiny safety guard for [Claude Code](https://claude.com/claude-code). It stays quiet on safe actions and, before a risky one, shows a plain-English card instead of a raw command, so you actually read what you are approving.

```
WHAT: Permanently deletes files or discards your changes: rm -rf demo
WHY IT MATTERS: Deleted files skip the recycle bin, and discarded changes are not saved anywhere.
COST: Your work or project files could disappear.
UNDO: Hard: only if the files were saved in git or a backup beforehand.
```

It is a seatbelt, not a sandbox: it never blocks on its own and never breaks your agent. If Sure? itself errors, it stays silent and logs the error.

## Install

Needs Python 3.9+ and Claude Code.

```bash
git clone https://github.com/Jrudani21/sure.git
python sure/sure/sure.py install      # adds the hooks to ~/.claude/settings.json (backup: settings.json.sure.bak)
python sure/sure/sure.py uninstall    # removes only its own hooks
```

Restart Claude Code after installing.

## What it flags

| Kind | Examples | Undo |
|---|---|---|
| download_and_run, pipe_to_shell | `curl ... \| bash`, `curl -o x.sh ... && ./x.sh` | impossible |
| database | `DROP TABLE`, `prisma migrate`, `alembic` | impossible |
| delete | `rm -rf`, `Remove-Item`, `git reset --hard`, `git clean` | hard |
| push | `git push`, `git push --force` | hard |
| script_launcher | `npm run`, `make`, `./deploy.sh` | hard |
| install | `npm install`, `pip install`, `cargo add` | easy |
| config | edits to `package.json`, `.env`, auth/middleware/migration files | easy |

Commands are split on `&&`, `||`, `;`, `|` and newlines, but not inside quotes. Heredoc bodies are ignored, and `bash -c "..."` payloads are inspected. Deleting `/`, `~`, `*` or `.` adds an extra warning.

## Commands

```bash
python sure/sure/sure.py stats        # asked / ran / approval rate per kind (run inside a project)
python sure/sure/sure.py report 7     # last 7 days in plain words, with a nudge if you approve everything
python sure/sure/sure.py undo 1       # restore save point 1 (needs save points on)
```

Every decision is written to `.sure/ledger.md` in the project folder.

## Options (`sure.json` in your project)

```json
{
  "save_points": { "enabled": true },
  "auto_allow": ["install"]
}
```

- `save_points`: takes a git snapshot before risky actions so `undo` can restore tracked files. It does not remove files created afterwards.
- `auto_allow`: skip the card for these kinds. Only easy-undo kinds (`install`, `config`) are honoured; delete, push, database and the rest always ask.

## Limits

- Regex heuristics, not a full shell parser. Unusual quoting can slip through or cause an extra card.
- It only watches `Bash`, `Write`, `Edit`, `MultiEdit` and `NotebookEdit`.
- It cannot see what a script does once launched.
- `Pre` and `Post` hooks only log `ran` for commands that exit successfully.

## Develop

```bash
python -m pytest -q
```

MIT licensed.
