# Architecture

One Python file, `sure/sure.py`, stdlib only. Claude Code calls it as a hook.

```
Claude Code --PreToolUse--> sure.py pre  --> classify() --> risky? --> card (permissionDecision "ask") + ledger "asked" (+ save point)
            --PostToolUse-> sure.py post --> classify() --> risky? --> ledger "ran"
terminal: sure.py stats | sure.py undo <n>
```

## Parts
- `CARDS`, `BASH_RULES`, `CONFIG_PATH`: the rule table. First matching rule wins, most dangerous first.
- `classify(tool, input)`: returns a risk dict or `None`. `None` means stay silent.
- `render_card(risk)`: the four-line text.
- `main(argv, stdin, cwd)`: the hook entry. Always returns a string, never raises.
- `log`, `stats`: ledger write and report.
- `load_manifest`, `save_point`, `undo`: the opt-in save-points module, enabled in `sure.json`.

## Principles
- Fail open: any internal error means no output and exit code 0.
- Modules are off unless `sure.json` turns them on.
- Add a module only when the need shows up.
