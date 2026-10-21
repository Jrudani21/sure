"""JSONL sidecar of Sure? decisions (.sure/events.jsonl) plus a reader for the board's replay."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import directory

_LINE = re.compile(r"^- (\S+) (asked|ran|auto|failed|quiet|decided) \[([a-z_]+)/([a-z]+)\] ?(.*)$")
_DECISION = re.compile(r"(allow_once|block|ask)$")
WHAT_MAX = 160  # the card title is a template plus the (80 char) target, so 80 cut the target off
FIELDS = ("ts", "verb", "kind", "tier", "via", "session", "request_id", "decision", "what")


def _what(text):
    """The card's short title as stored: one clean line, secrets masked, at most WHAT_MAX characters."""
    w = directory.clean(text)
    return w if len(w) <= WHAT_MAX else w[: WHAT_MAX - 3] + "..."


def record(cwd, verb, kind=None, tier=None, via=None, session=None, request_id=None, decision=None, what=None):
    """Append one event. Never raises: the hook must not break or slow down."""
    try:
        d = directory.sure_dir(cwd)
        ev = {"ts": datetime.now().isoformat(timespec="seconds"), "verb": verb, "kind": kind,
              "tier": tier, "via": via, "session": session, "request_id": request_id}
        if decision is not None:
            ev["decision"] = decision
        if what is not None:
            ev["what"] = _what(what)
        with open(d / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
    except Exception:
        pass


def _from_ledger(text):
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            ts, verb, kind, tier, rest = m.groups()
            dm = _DECISION.fullmatch(rest) if verb == "decided" else None
            yield {"ts": ts, "verb": verb, "kind": kind, "tier": tier, "via": None, "session": None,
                   "request_id": None, "decision": dm.group(1) if dm else None,
                   "what": _what(rest) if verb == "asked" and rest else None}


def _from_jsonl(text):
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            yield {**{k: None for k in FIELDS}, **ev}


def _when(ev):
    try:
        t = datetime.fromisoformat(ev["ts"])
    except (KeyError, TypeError, ValueError):
        return None
    return None if t.tzinfo else t  # the hook writes local naive times; anything else cannot be compared


def read(cwd, days=7, now=None):
    """Events from the last `days`, oldest first. events.jsonl is the record; ledger.md lines older than
    its first event (history from before the sidecar existed) come first, so nothing is lost or doubled."""
    cutoff = (now or datetime.now()) - timedelta(days=days)
    if "\x00" in str(cwd):
        return []  # no such folder; bad UTF-8 still raises so the board can say the history is unreadable
    d = Path(cwd) / ".sure"
    evs = None
    try:
        evs = list(_from_jsonl((d / "events.jsonl").read_text(encoding="utf-8")))
    except OSError:
        pass
    try:
        md = list(_from_ledger((d / "ledger.md").read_text(encoding="utf-8")))
    except OSError:
        md = []
    if evs is None:
        evs = md
    else:
        first = min((t for t in map(_when, evs) if t), default=None)
        evs = [e for e in md if first is None or (_when(e) or first) < first] + evs
    return [ev for ev in evs if (_when(ev) or datetime.min) >= cutoff]
