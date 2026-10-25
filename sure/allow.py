"""One-time allow codes for calls Sure? stops while nobody is at the board (Ken's adapter turns every "ask" into a block).

The owner reads the code with `sure.py pending` and redeems it with `sure.py allow <code>` in their own terminal.
The code is never written anywhere the model sees: the state file keeps a nonce and the command hash, and the
code is recomputed from them with a secret key (HMAC), so `pending` can show it and nothing stores it.
Anything running as the owner can still read this file: this is friction against an agent, not a security boundary.
Best effort: the read-modify-write of the state file is atomic but not locked, so two writers at the same instant can drop an update.
"""
import base64
import contextlib
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path

try:
    import msvcrt  # Windows
except ImportError:
    msvcrt = None
try:
    import fcntl  # POSIX
except ImportError:
    fcntl = None

LOCK_WAIT = 3  # seconds a writer waits for the state lock before going on without it: a hook must never hang
TTL = 600  # seconds a code, and an allowance after redeeming, stays valid
MAX_FAILS = 5  # wrong codes in a row before a lock of TTL seconds
MAX_PENDING = 20


def _file():
    base = os.environ.get("SURE_HOME") or str(Path.home())
    d = Path(base) / ".sure"  # a .sure/ path is already guarded: an agent writing here gets a card
    d.mkdir(parents=True, exist_ok=True)
    return d / "allow.json"


@contextlib.contextmanager
def _lock():
    """Exclusive lock around one read-modify-write of the state file, so two processes cannot drop each other's update.
    Yields whether it got the lock; after LOCK_WAIT seconds it carries on unlocked (best effort) instead of hanging."""
    f = open(_file().with_name("allow.json.lock"), "a+b")
    got, deadline = False, time.time() + LOCK_WAIT
    while not got:
        try:
            if msvcrt:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                break  # no locking primitive on this platform
            got = True
        except OSError:
            if time.time() > deadline:
                break
            time.sleep(0.02)
    try:
        yield got
    finally:
        if got:
            try:
                if msvcrt:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


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
        for attempt in range(5):
            try:
                os.replace(tmp, str(p))
                break
            except PermissionError:  # Windows: another process is replacing the same file at this instant
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
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


def _issue(h, what, now=None):
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


def _redeem(code, now=None):
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


def _consume(h, now=None):
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


def issue(h, what, now=None):
    """Remember that call `h` was stopped. Returns nothing: the code is only ever shown by `pending`."""
    with _lock():
        _issue(h, what, now)


def redeem(code, now=None):
    """True when `code` matches a live stopped call: that call may then run once. Wrong codes count toward a lock."""
    with _lock():
        return _redeem(code, now)


def consume(h, now=None):
    """True once for a call the owner allowed; the allowance is spent."""
    with _lock():
        return _consume(h, now)
