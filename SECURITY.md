# Security

Sure? is a seatbelt for an owner who is paying attention. It explains risky agent actions in plain words and lets you stop them. It is not a sandbox and not a security boundary.

## What it protects against

- A mistaken or manipulated agent action that you would otherwise approve without reading.
- Other websites reaching the board. The board listens on 127.0.0.1 only, checks the Host and Origin headers exactly, answers no cross-origin preflight, and sends a strict content security policy. All server text is shown as plain text.
- A program that can only read the board's runtime file. The board uses two keys. The hook key in `~/.sure/board.json` can only submit and withdraw requests. The page key is printed once in the board URL and never written to disk; it is required to read state, decide, trust, send heartbeats or replay history.
- A fake board. Before the hook sends any command text it asks the board to prove itself with an HMAC over a fresh random nonce. A wrong or missing proof makes the hook send nothing and fall back to Claude Code's normal prompt.
- Abuse of the board's API: request bodies are capped at 64 KB, at most 50 requests can wait at once, project folders must be local absolute paths (no UNC or device paths) and must have appeared on a card before replay or trust, and card tiers come from the permission directory, not from the request.
- Misleading cards. Control and bidirectional characters are stripped, common secret shapes are masked, and the card shows the full command (up to 2000 characters, with a marker when cut).

If anything in the board path fails, is slow, or cannot be verified, the hook returns `ask` and Claude Code shows its normal prompt. The hook never raises and never blocks the agent.

## What it does not protect against

- **Anything running as your Windows user, including the AI agent.** The agent runs commands as you. It can read the hook key, edit `sure.json` or `settings.json` with commands Sure? does not recognise, start its own fake board, kill the real one, or read the page key from your browser profile. Commands that touch `.sure/`, `sure.json` or `sure.py board|install|uninstall` do raise a card, but a determined same-user program can evade that. "Allow once" returns `allow`, which skips Claude's own prompt, so a fake board that answers "allow" is the worst case here.
- **Pattern-matching gaps.** Detection is regex heuristics. Known gaps include `$(...)` inside `echo`, many wrapper forms, some PowerShell constructs, destructive cloud and container tools, `rsync --delete`, `truncate` and plain `>` redirection of important files. MCP tool input is not inspected.
- **Attended tabs.** A visible board tab counts as "someone is watching": an unattended visible tab makes the hook wait up to 120 seconds before Claude's prompt appears.
- **Browser storage.** The page key stays in browser history and session storage for that board run.
- **Hook key holders.** A program with the hook key can post up to 50 lookalike cards and can withdraw or replace pending ones. The result is always Claude's own prompt, never a silent allow. `/api/alive` tells local processes whether a page is open.
- **Permission modes.** Auto and bypass permission modes and `ask` rules were not tested together with "Allow once". Your deny rules still win over a hook `allow` (checked by a test).
- **Local files.** The ledger keeps the first 80 characters of risky commands. Secrets and URL passwords are masked, but not every shape is recognised. `.sure/` is created with a `.gitignore` that excludes everything. The settings backup is rolling; the one-time `.orig` copy is never overwritten.
- **Working folder.** The hook looks for `sure.json` and `.sure/` in Claude's current folder, which may be a subfolder.
- `SURE_RUNTIME_FILE` (absolute path only) is a test knob that changes where the hook looks for the board.

## Reporting a problem

Open a private security advisory on the GitHub repository.
