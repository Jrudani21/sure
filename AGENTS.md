# Agent rules for this repo

Read first: `PRD.md` (what and why), `ARCHITECTURE.md`.

## Do
- Test first. Run `python -m pytest -q` and read the output before claiming anything works.
- Keep changes small and in `sure/sure.py`.
- Follow `CODE_STYLE.md`, `DESIGN_SYSTEM.md` (card wording), and `SECURITY.md`.
- Check on the artifact after editing: the file parses, tests pass.
- Prefer targeted edits over rewriting whole files.

## Don't
- Add dependencies, network calls, or model calls.
- Make the hook able to block or crash the agent: it must fail open.
- Turn on auto-allow or other opt-in modules by default.
- Change the user's `settings.json` without asking; it affects every session.
- Add co-author trailers to commits.

## Commits
Short imperative message with a type prefix: `feat:`, `fix:`, `docs:`, `test:`.
