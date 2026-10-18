# Security

## What Sure? is
A speed bump that makes risky actions understandable. It is **not** a sandbox and not a security boundary.

## Known limits
- Pattern matching only. A cleverly written command can avoid every rule. A launcher like `python script.py` is not recognised at all.
- Fails open by design: if Sure? errors, the action goes through the normal permission flow with no card. A broken hook must never block work, and the cost is a missed warning.
- Does not see inside scripts, or combinations of harmless steps.
- Save points cover tracked changes only; untracked files are not restored.

## Data handling
- No network calls and no model calls in v1.
- The ledger stays on disk in `.sure/ledger.md`. It contains the first 80 characters of risky commands, so it can include secrets typed on a command line. Keep `.sure/` out of git.
- Nothing is read from or written outside the working directory, except `refs/sure/*` in its git repo.

## Rules for changes
- No new dependency without a written reason.
- Never add code that runs or fetches content from commands it inspects.
- Report issues by opening an issue in the repo.
