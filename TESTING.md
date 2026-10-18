# Testing

## Run
```bash
python -m pytest -q
```
72 tests at time of writing, in `tests/test_sure.py` (features) and `tests/test_review.py` (regressions from the final review: UTF-8 input, save-point numbering, false positives and negatives, stats). All must pass before a commit.

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
