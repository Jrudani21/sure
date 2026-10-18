# Database

There is no database. State lives in two plain places.

## Ledger: `.sure/ledger.md` (in the project being worked on)
Append-only markdown, one line per event:

```
- <ISO timestamp> asked [<kind>/<tier>] <what>
- <ISO timestamp> ran [<kind>/<tier>]
- <ISO timestamp> save point refs/sure/<n>
```

`asked` is written when the card is shown. `ran` is written after the tool actually ran, which means it was approved (or auto-allowed by the user's own settings). `stats` computes ran / asked per `kind/tier`.

## Save points: git refs `refs/sure/<n>`
Created with `git stash create` plus `git update-ref`, only when `sure.json` enables `save_points`. They hold tracked changes only. Restore with `python sure.py undo <n>`.

## Manifest: `sure.json`
`{"save_points": {"enabled": false}}`. Missing or invalid means everything is off.

## Gitignore
`.sure/` should be git-ignored in the project being worked on.
