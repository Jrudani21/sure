"""One-time allow codes for calls Sure? stops while nobody is at the board (Ken's adapter turns every "ask" into a block).

The owner reads the code with `sure.py pending` and redeems it with `sure.py allow <code>` in their own terminal.
The code is never written anywhere the model sees: the state file keeps a nonce and the command hash, and the
code is recomputed from them with a secret key (HMAC), so `pending` can show it and nothing stores it.
Anything running as the owner can still read this file: this is friction against an agent, not a security boundary.
Best effort: the read-modify-write of the state file is atomic but not locked, so two writers at the same instant can drop an update.
"""
import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path

TTL = 600  # seconds a code, and an allowance after redeeming, stays valid
MAX_FAILS = 5  # wrong codes in a row before a lock of TTL seconds
MAX_PENDING = 20


def _file():
    base = os.environ.get("SURE_HOME") or str(Path.home())
    d = Path(base) / ".sure"  # a .sure/ path is already guarded: an agent writing here gets a card
    d.mkdir(parents=True, exist_ok=True)
    return d / "allow.json"


def _load():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("key"), str):
            for k, v in (("pending", []), ("allowed", []), ("fails", 0), ("locked_until", 0)):
                d.setdefault(k, v)  # a partial file (hand-edited, half-written) must not crash the CLI
            return d
    except (OSError, ValueError):
        pass
    return {"key": os.urandom(32).hex(), "pending": [], "allowed": [], "fails": 0, "locked_until": 0}


def _save(d):
    p = _file()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".allow-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_hash(tool_name, tool_input):
    blob = json.dumps({"t": tool_name, "i": tool_input}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _code(key, nonce, h):
    mac = hmac.new(bytes.fromhex(key), (nonce + h).encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode()[:6]


def issue(h, what, now=None):
    """Remember that call `h` was stopped. Returns nothing: the code is only ever shown by `pending`."""
    now = time.time() if now is None else now
    d = _load()
    d["pending"] = [e for e in d["pending"] if e["exp"] > now]
    if not any(e["hash"] == h for e in d["pending"]):
        d["pending"].append({"nonce": os.urandom(4).hex(), "hash": h, "what": str(what)[:160], "exp": now + TTL})
        d["pending"] = d["pending"][-MAX_PENDING:]
    _save(d)


def pending(now=None):
    """[(code, what, seconds_left)] for stopped calls that can still be allowed."""
    now = time.time() if now is None else now
    d = _load()
    return [(_code(d["key"], e["nonce"], e["hash"]), e["what"], int(e["exp"] - now)) for e in d["pending"] if e["exp"] > now]


def redeem(code, now=None):
    """True when `code` matches a live stopped call: that call may then run once. Wrong codes count toward a lock."""
    now = time.time() if now is None else now
    d = _load()
    if d["locked_until"] > now:
        return False
    code = (code or "").strip().upper()
    for e in d["pending"]:
        if e["exp"] > now and hmac.compare_digest(_code(d["key"], e["nonce"], e["hash"]), code):
            d["pending"].remove(e)
            d["allowed"] = [a for a in d["allowed"] if a["exp"] > now] + [{"hash": e["hash"], "exp": now + TTL}]
            d["fails"] = 0
            _save(d)
            return True
    d["fails"] += 1
    if d["fails"] >= MAX_FAILS:
        d["fails"], d["locked_until"] = 0, now + TTL
    _save(d)
    return False


def locked_for(now=None):
    now = time.time() if now is None else now
    return max(0, int(_load()["locked_until"] - now))


def consume(h, now=None):
    """True once for a call the owner allowed; the allowance is spent."""
    now = time.time() if now is None else now
    d = _load()
    live = [a for a in d["allowed"] if a["exp"] > now]
    hit = next((a for a in live if a["hash"] == h), None)
    if hit:
        live.remove(hit)
    if hit or len(live) != len(d["allowed"]):
        d["allowed"] = live
        _save(d)
    return hit is not None
