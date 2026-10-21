# Responsible use of Sure?

Sure? is a safety aid for people using Claude Code. It is licensed under MIT (see `LICENSE`).
This page says what the project asks of you. It is a request and a statement of intent, not an
extra license term: the MIT license is the only legal grant, and nothing here removes any right it gives.

## What Sure? is

- A hook that shows a plain-English card before some risky actions and lets the person decide.
- A pattern matcher with known gaps (see "Known limits" in the README and `SECURITY.md`).
- Local only. It sends nothing off the machine and calls no model.

## What Sure? is not

- Not a sandbox, not a permission system, not a guarantee that a dangerous action is caught.
- Not a substitute for backups, code review, least-privilege credentials or your own judgment.

## What we ask

1. **Be honest about it.** Do not describe Sure? or a product built on it as complete, certified or
   "secure". Repeat its known limits wherever you describe what it protects against.
2. **Do not use it to mislead the person it protects.** Do not configure, fork or wrap it so that
   cards are hidden, auto-approved, reworded to understate the risk, or shown after the action has run,
   while the user is told they are protected.
3. **Do not bypass other people's controls with it.** Use it on your own machines and projects, or
   where the owner has agreed.
4. **Keep credit and do not rebrand.** If you redistribute it, keep the copyright and license notice
   (the MIT license requires this). Please do not sell a copy as your own product or ship a fork under
   the name "Sure?" in a way that suggests it is the original; pick your own name and say it is based on Sure?.
5. **Report problems.** Found a way past it or a misleading card? Follow `SECURITY.md`.

## What this cannot do

A notice cannot stop anyone from copying MIT-licensed code, and it cannot exclude particular people or
companies: MIT grants its rights to everyone. If the project's license ever changes, it will change only
for future versions, in the open, and copies already published under MIT stay MIT.
