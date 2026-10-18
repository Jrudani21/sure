# PRD: Sure?

## Problem
People who build with AI coding agents (vibe coders) click "yes" on permission prompts without understanding them. Prompts show raw commands, not what they do. Research (2026) shows people approve about a third of malicious requests and stop reading after a few prompts (approval fatigue).

## Users
Non-expert and ADHD builders using Claude Code who want to stay in control without reading shell commands.

## Goals
- Stay silent on safe actions so prompts stay rare and meaningful.
- On risky actions, explain the effect in plain language, with a reversibility tier (easy / hard / impossible to undo).
- Keep a ledger of every decision and report approval rate per risk kind.
- Optional undo: a save point taken before risky actions.

## Non-goals
- Not a sandbox or a security boundary.
- No dashboard, no accounts, no network calls, no model calls in v1.
- No auto-allow in v1.

## Anti-goals
- Prompting on everything (makes fatigue worse).
- Cards that just restate the command.
- Ever breaking or blocking the agent because Sure? itself failed.

## Success
Approval rate per kind (from `stats`) shows people are declining some risky actions, and the user can read the ledger afterwards and say what they agreed to.

## Later (only on real need)
Auto-allow for easy-undo kinds, multi-step sequence detection, a "do you understand?" check, an LLM-written "why", other modules (install explainer, click-to-steer, one-line deploy).
