# Design system: the card

Sure? has no UI. The card is the whole interface, shown in Claude Code's permission prompt.

## Format
Exactly four lines, in this order, no blank lines:

```
WHAT: <effect in plain language>: <the command or file, max 80 chars>
WHY IT MATTERS: <one sentence>
COST: <what you lose if this goes wrong>
UNDO: <Easy|Hard|Impossible>: <how, or why not>
```

## Voice
- Describe the effect ("deletes the folder and everything in it"), never just repeat the command.
- Plain words. If a technical term is unavoidable, gloss it in the same sentence.
- No fear language, no emoji, no scolding.
- One sentence per line.

## Tiers
| Tier | Meaning |
|---|---|
| Easy | one step restores it (uninstall, put the old file back) |
| Hard | recoverable only if you already saved it elsewhere |
| Impossible | cannot be undone by anyone |

The `UNDO:` line always starts with the tier word and a colon.

## Risk kinds
`pipe_to_shell`, `database` (impossible); `delete`, `push`, `script_launcher` (hard); `install`, `config` (easy). Wording lives in the `CARDS` table in `sure/sure.py`.
