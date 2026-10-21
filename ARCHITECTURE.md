# Architecture

Python 3.9+, standard library only, no build step.

## Files

| Path | Role |
|---|---|
| `sure/sure.py` | Hook entry `main`, classifier, CLI (`install`, `uninstall`, `board`, `stats`, `report`, `undo`), save points, installer |
| `directory/permissions.json` | The permission directory: every kind with its card text, tier, quiet default, ask tokens and matchers (list order is match priority) |
| `sure/directory.py` | Loads and validates the directory, plus the shared helpers: text cleaning and secret masking, the sure.json reader |
| `sure/events.py` | `.sure/events.jsonl` writer and reader (falls back to `ledger.md` for older history) |
| `sure/trust.py` | Per-project `auto_allow` store (easy kinds only) and its atomic writer |
| `sure/board.py` | Local HTTP server and the hook's client half (runtime file, identity proof, request, withdraw) |
| `sure/web/` | The board page: `index.html`, `app.js`, `style.css`, no framework |
| `scripts/live_board_smoke.py` | Manual end-to-end check with real `claude -p` sessions |
| `tests/` | pytest suite. `tests/data/classifier_corpus.json` holds 120 labelled commands |

## Hook flow

1. Claude Code runs the PreToolUse hook with JSON on stdin.
2. Sure? classifies the call. Quiet and `auto_allow` kinds are logged and return nothing. A kind in `ask_on` always continues.
3. Otherwise it logs `asked`, optionally takes a save point, and, if the project enabled the board, asks the board to prove itself and waits for a decision.
4. `allow_once` becomes `permissionDecision: allow`, `block` becomes `deny` with the card text, anything else or any failure becomes `ask` (Claude Code's normal prompt).
5. The post hooks log `ran` or `failed` and withdraw cards that were answered elsewhere.

Every path that can fail ends in "return nothing" or "ask". The hook never raises, prints only nothing or one JSON object, and bounds every probe to well under a second.

## Board

The server keeps requests in memory. Each request waits on an event until the page decides, the wait (120 seconds) ends, or the board stops. The hook key submits and withdraws requests; the page key reads state and decides. Decisions are logged to `events.jsonl` so Replay can show them later.

## Design rules

- Fail open. Quiet by default; a card only when it matters.
- Tiers and wording live in the directory, not in code.
- No dependencies. Plain files, plain HTTP on loopback.
- A seatbelt, not a sandbox.
