# Testing

## Run
```bash
python -m pytest -q
```
About 1,070 tests at time of writing, under `tests/`: the classifier and cards, the hook, the permission directory, events, trusted kinds, the board server and its security checks, the hook-to-board client (including hostile servers and timing bounds), the page's static guards, and a 120-command labelled corpus (`tests/data/classifier_corpus.json`). All must pass before a commit. Lint with `ruff check sure tests scripts` (config in `ruff.toml`).

The suite starts local boards on port 0 and a few tests use wall-clock bounds; they were run under CPU load to check they are stable.

For the real end-to-end check run `SURE_SMOKE_ROOT=<folder> python scripts/live_board_smoke.py`. It starts a board, runs two real `claude -p` sessions that try `rm -rf` on a throwaway file, answers "Allow once" for one and "Block" for the other, and checks the file is gone or still there.

## Method
Test first: write the test, watch it fail for the right reason, write the minimum code, watch it pass, then run the whole suite.

## What is covered
- Classifier: every risk kind, tiers, Windows commands, chained commands, script launchers, config file paths, safe and unknown calls.
- Card: exactly four lines with the right labels; the `UNDO:` line starts with the tier.
- Hook: ask JSON shape, silence on safe actions, ledger lines, bad or empty stdin, unwritable ledger, CLI exit code 0.
- Save points: manifest missing or invalid, snapshot and restore, outside git, clean tree, off by default.
- Stats: counts and rate, empty ledger.

## Must always hold (review focus)
- `echo hi && rm -rf build` still gets a card.
- `Remove-Item` and `del /s /q` count as delete.
- Garbage stdin never produces output or a non-zero exit.
- Save points enabled outside a git repo still shows the card.
- `stats` on a missing ledger prints only the header.

## Manual smoke check
Ask Claude Code to run `rm -rf somefolder` in a scratch directory. The permission prompt should show the card, and `.sure/ledger.md` should gain `asked` and then `ran` lines.
