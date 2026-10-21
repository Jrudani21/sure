"""Local decision board: the hook posts a request and long-polls; the page drags a card to decide.

Two secrets. The hook token (in the runtime file) opens only POST /api/request and /api/withdraw.
The page token (only in the printed URL's fragment, never on disk) opens the page's routes: state,
decide, trust, heartbeat, replay. So an agent that reads the runtime file still cannot approve its
own request or fake a watching page. GET /api/alive takes no token: it proves the board's identity
with HMAC(hook token, nonce) before the hook sends anything. Every /api/ route also refuses a
foreign Origin. GET / and /static/* are open: the page holds no secret.
"""
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import directory
import events
import trust

WAIT_SECONDS = 120
HEARTBEAT_TTL = 5
MAX_BODY = 64 * 1024
WEB = Path(__file__).resolve().parent / "web"
DECISIONS = {"allow_once", "block", "ask"}
TEXT = ("tool", "what", "why", "cost", "undo", "session", "cwd")
MAX_PENDING = 50  # request 51+ gets "ask" at once
MAX_COMMAND = 2200  # 2000 shown characters plus the truncation marker, with room to spare
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8"}


def runtime_path():
    # SURE_RUNTIME_FILE is a test/dev knob: the hook sends command text to whatever board this file names,
    # so only an absolute path is honoured.
    env = os.environ.get("SURE_RUNTIME_FILE")
    return Path(env) if env and os.path.isabs(env) else Path.home() / ".sure" / "board.json"


def pid_alive(pid, strict=False):
    """strict (the hook client): a process we may not query counts as dead, so a stale file whose pid
    now belongs to someone else's process is not trusted. Lenient (serve's "already running"): alive."""
    if not isinstance(pid, int) or pid <= 0:
        return False  # os.kill(0, 0) would signal our own process group
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        if pid > 0xFFFFFFFF:
            return False
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return not strict and ctypes.get_last_error() == 5  # access denied: it exists, just not ours
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return not strict
    except (OSError, OverflowError):
        return False
    return True


def _flow(rows):
    """[(session, kind, handler)] -> sorted count list for the flow map."""
    c = Counter(rows)
    return [{"session": s, "kind": k, "handler": h, "count": n}
            for (s, k, h), n in sorted(c.items(), key=lambda kv: tuple(str(x) for x in kv[0]))]


def _replay_flow(evs):
    rows = []
    for ev in evs:
        verb, kind = ev.get("verb"), ev.get("kind")
        if verb in ("quiet", "auto"):
            rows.append((ev.get("session"), kind, "rule"))
        elif verb == "asked":
            rows.append((ev.get("session"), kind, "you"))
    return _flow(rows)


_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
_DEVICE = ("\\\\?\\", "\\??\\", "\\\\.\\", "//?/", "/??/", "//./")


def _local_cwd(cwd):
    """Whitelist: a plain local absolute path. UNC, NT/device paths, streams and relative paths are
    refused before os.path touches them, so nothing reaches out over SMB."""
    if not isinstance(cwd, str) or "\x00" in cwd or "::" in cwd or any(d in cwd for d in _DEVICE):
        return False
    if _DRIVE.match(cwd):
        return True
    return sys.platform != "win32" and cwd.startswith("/") and not cwd.startswith("//")


class _Reject(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


class _Server(ThreadingHTTPServer):
    """On Windows SO_REUSEADDR lets another process bind the same port: refuse it and ask for exclusive use."""
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if sys.platform == "win32" and opt is not None:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, opt, 1)
            except OSError:
                pass  # best effort: the bind still goes ahead
        super().server_bind()


class Board:
    def __init__(self, host="127.0.0.1", port=0, runtime=None):
        if host != "127.0.0.1":
            raise ValueError("the board binds 127.0.0.1 only")
        self.host, self.port, self.runtime = host, port, Path(runtime or runtime_path())
        self.hook_token = secrets.token_urlsafe(24)  # written to the runtime file for the hook
        self.page_token = secrets.token_urlsafe(24)  # only ever in self.url, printed by serve()
        self.server = self.thread = None
        self._lock = threading.Lock()
        self._pending = {}  # request_id -> {"card", "event", "decision"}
        self._finished = deque(maxlen=200)  # ponytail: last 200 cards in memory; replay covers history
        self._seq = self._beat_seq = 0
        self._beat = None
        self._stopping = False
        try:
            self._tiers = {e["id"]: e["tier"] for e in directory.load()}
        except Exception:
            self._tiers = {}  # no directory: body tiers are used, and nothing can be always allowed

    # lifecycle

    def start(self):
        try:
            pid = json.loads(self.runtime.read_text(encoding="utf-8")).get("pid")
        except (OSError, ValueError, AttributeError):
            pid = None
        # ponytail: check-then-write race on the runtime file; fine for one user starting one board
        if pid != os.getpid() and pid_alive(pid):
            raise SystemExit(f"sure board: already running (pid {pid}); stop it or delete {self.runtime}")
        self.server = _Server((self.host, self.port), _Handler)
        self.server.daemon_threads = True
        self.server.board = self
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.url = f"{self.origin}/#{self.page_token}"
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()
        self._write_runtime()
        return self.port

    def stop(self):
        if not self.server:
            return
        with self._lock:
            self._stopping = True  # requests arriving from now on get "ask" at once
            for entry in list(self._pending.values()):
                self._finish(entry, "ask", "board stopped", "done")
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.server = None
        try:
            if json.loads(self.runtime.read_text(encoding="utf-8")).get("token") == self.hook_token:
                self.runtime.unlink()
        except (OSError, ValueError, AttributeError):
            pass

    def _write_runtime(self):
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.runtime.parent), prefix=".board-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"port": self.port, "token": self.hook_token, "pid": os.getpid()}, f)
            try:
                os.chmod(tmp, 0o600)  # ponytail: on Windows only the home folder's ACL protects it
            except OSError:
                pass
            os.replace(tmp, str(self.runtime))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def alive(self):
        """A page heartbeat arrived in the last HEARTBEAT_TTL seconds."""
        return self._beat is not None and time.monotonic() - self._beat < HEARTBEAT_TTL

    # decisions

    def _finish(self, entry, decision, outcome, column, handler=None):
        """The one release path (decide, withdraw, timeout, replace, stop). Caller holds the lock."""
        if entry["decision"] is not None:
            return False
        entry["decision"] = decision
        card = entry["card"]
        card.update(decision=decision, outcome=outcome, column=column, handler=handler)
        if self._pending.get(card["request_id"]) is entry:
            del self._pending[card["request_id"]]
        self._finished.append(card)
        entry["event"].set()
        return True

    def request(self, body):
        rid = body.get("request_id")
        if not isinstance(rid, str) or not 0 < len(rid) <= 200:
            raise _Reject(400, "bad request_id")
        if body.get("tier") not in directory.TIERS:
            raise _Reject(400, "bad tier")
        kind = body.get("kind")
        if not isinstance(kind, str) or not 0 < len(kind) <= 100:
            raise _Reject(400, "bad kind")
        # a known kind's tier comes from the directory, never the body (the page and decide gate on it)
        card = {"request_id": rid, "kind": kind, "tier": self._tiers.get(kind, body["tier"]),
                "ts": datetime.now().isoformat(timespec="seconds")}
        for k in TEXT:
            v = body.get(k, "")
            if not isinstance(v, str):
                raise _Reject(400, f"bad {k}")
            card[k] = v[:2000] if k == "cwd" else directory.clean(v[:2000])  # cwd is matched exactly, the rest is shown
        cmd = body.get("command", "")
        if not isinstance(cmd, str) or len(cmd) > MAX_COMMAND:
            raise _Reject(400, "bad command")
        card["command"] = directory.clean(cmd, keep_newlines=True)  # line breaks kept for the <pre>; the hook already capped it at 2000 + a "…(+N chars)" marker
        if not _local_cwd(card["cwd"]):
            raise _Reject(400, "cwd must be a local absolute path")
        entry = {"card": card, "event": threading.Event(), "decision": None}
        with self._lock:
            if self._stopping or (len(self._pending) >= MAX_PENDING and rid not in self._pending):
                return {"decision": "ask"}  # stopping, or too many waiting: no thread is parked
            old = self._pending.get(rid)
            if old:
                self._finish(old, "ask", "replaced", "done")
            self._seq += 1
            card["_seq"] = self._seq
            card["_deadline"] = time.monotonic() + WAIT_SECONDS
            self._pending[rid] = entry
        entry["event"].wait(WAIT_SECONDS)
        with self._lock:
            self._finish(entry, "ask", "timeout", "done")
            out = {"decision": entry["decision"]}
            if out["decision"] == "ask" and entry["card"]["handler"] == "you":
                out["by"] = "you"  # the owner chose Ask Claude; timeout/stop/replace/withdraw answer "ask" without it
            return out

    def decide(self, body):
        d = body.get("decision")
        if d not in DECISIONS:
            raise _Reject(400, "bad decision")
        with self._lock:
            entry = self._pending.get(body.get("request_id")) if isinstance(body.get("request_id"), str) else None
            if not entry:
                raise _Reject(404, "no such pending request")
            if entry["card"]["tier"] == "impossible" and d not in ("allow_once", "block"):
                raise _Reject(400, "impossible-tier requests take allow_once or block")
            self._finish(entry, d, "decided", "declined" if d == "block" else "done", "you")
        return {"ok": True}

    def withdraw(self, body):
        with self._lock:
            entry = self._pending.get(body.get("request_id")) if isinstance(body.get("request_id"), str) else None
            if not entry:
                raise _Reject(404, "no such pending request")
            self._finish(entry, "ask", "answered elsewhere", "done")
        return {"ok": True}

    def heartbeat(self, body):
        with self._lock:
            self._beat, self._beat_seq = time.monotonic(), self._seq
        return {"ok": True}

    # views

    @staticmethod
    def _trusted(cwd):
        try:
            return trust.trusted(cwd) if _local_cwd(cwd) and os.path.isdir(cwd) else []
        except Exception:
            return []

    def state(self):
        with self._lock:
            pending = [e["card"] for e in self._pending.values()]
            finished = list(self._finished)
            beat_seq = self._beat_seq
        out = {"incoming": [], "needs_me": [], "delegated": [], "done": [], "declined": []}
        # ponytail: "incoming" = arrived since the page's last heartbeat; a per-card ack if that proves too coarse
        for c in sorted(pending, key=lambda c: c["_seq"]):
            out["incoming" if c["_seq"] > beat_seq else "needs_me"].append(c)
        out_pending = out["incoming"] + out["needs_me"]
        for c in finished:
            out[c["column"]].append(c)
        out = {k: [{f: v for f, v in c.items() if not f.startswith("_")} for c in cs] for k, cs in out.items()}
        now = time.monotonic()
        for c, view in zip(out_pending, out["incoming"] + out["needs_me"]):
            view["deadline_in"] = max(0.0, round(c["_deadline"] - now, 1))  # seconds left, for the page's countdown
        out["wait_seconds"] = WAIT_SECONDS
        out["flow"] = _flow([(c["session"], c["kind"], c["handler"]) for c in finished if c["handler"]])
        cwds = {c["cwd"] for c in pending + finished if c["cwd"]}
        out["trusted"] = {cwd: self._trusted(cwd) for cwd in sorted(cwds) if _local_cwd(cwd) and os.path.isdir(cwd)}
        return out

    def replay(self, query):
        q = parse_qs(query)
        try:
            days = int(q.get("days", ["7"])[0])
        except ValueError:
            days = 0
        if not 1 <= days <= 365:
            raise _Reject(400, "days must be 1..365")
        cwd = q.get("cwd", [""])[0]
        if not _local_cwd(cwd) or not os.path.isdir(cwd):
            raise _Reject(400, "cwd must be an existing local folder")
        if cwd not in self._seen_cwds():
            raise _Reject(400, "cwd has not asked for anything on this board")
        try:
            import sure  # lazy: sure.py loads the directory and builds rules at import
            evs = events.read(cwd, days)
            report = sure.report(cwd, days)
        except Exception as e:
            return {"events": [], "report": "", "error": f"could not read history ({type(e).__name__})"}
        return {"events": evs, "report": report, "flow": _replay_flow(evs)}

    def _seen_cwds(self):
        """Folders that asked on this board: the only ones trust and replay may touch."""
        with self._lock:
            return {e["card"]["cwd"] for e in self._pending.values()} | {c["cwd"] for c in self._finished}

    def set_trust(self, body, add):
        cwd, kind = body.get("cwd"), body.get("kind")
        if not _local_cwd(cwd) or not os.path.isdir(cwd) or not isinstance(kind, str):
            raise _Reject(400, "need an existing local cwd and a kind")
        if cwd not in self._seen_cwds():
            raise _Reject(400, "cwd has not asked for anything on this board")
        try:
            if add:
                if self._tiers.get(kind) != "easy":
                    raise _Reject(400, "only easy-tier kinds can be always allowed")
                if not trust.add(cwd, kind):  # easy kind refused: sure.json is unreadable
                    raise _Reject(400, "sure.json is not valid JSON")
            else:
                trust.remove(cwd, kind)
            return {"ok": True, "trusted": trust.trusted(cwd)}
        except OSError:
            raise _Reject(500, "could not write sure.json")


HOOK_ROUTES = {("POST", "/api/request"), ("POST", "/api/withdraw")}
NONCE = re.compile(r"[0-9a-f]{32,64}")


def proof(token, nonce):
    return hmac.new(token.encode("utf-8"), nonce.encode("ascii"), hashlib.sha256).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 (the default) on purpose: rejected bodies are left unread, so no keep-alive
    timeout = 10  # silent clients cannot pin a thread; the long-poll reads nothing while it waits

    def log_message(self, *args):
        pass

    def _send(self, status, data, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):  # every response, send_error's included
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _drain(self):
        """Read an unread request body (bounded) before answering: closing with unread data makes
        Windows reset the connection, so the client would see an abort instead of our 403."""
        if getattr(self, "_consumed", False):
            return
        self._consumed = True
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= MAX_BODY and not self.headers.get("Transfer-Encoding"):
                self.rfile.read(n)
        except Exception:
            pass  # the socket timeout bounds a client that promises more than it sends

    def _guarded(self, fn):
        try:
            try:
                port = self.server.board.port
                if self.headers.get("Host") != f"127.0.0.1:{port}":
                    raise _Reject(403, "forbidden")  # DNS rebinding
                fn()
            except _Reject as r:
                self._drain()
                self._json(r.status, {"error": r.msg})
            except ConnectionError:
                raise
            except Exception:
                self._drain()
                self._json(500, {"error": "internal error"})
        except ConnectionError:
            pass  # client went away mid-poll

    def do_GET(self):
        self._guarded(self._get)

    def do_POST(self):
        self._guarded(self._api)

    def do_DELETE(self):
        self._guarded(self._api)

    def _get(self):
        path = urlsplit(self.path).path
        if path == "/":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path.startswith("/api/"):
            return self._api()
        raise _Reject(404, "not found")

    def _static(self, rel):
        rel = unquote(rel)
        if "\\" in rel or "\x00" in rel or ":" in rel or rel.startswith("/") or any(p in ("", ".", "..") for p in rel.split("/")):
            raise _Reject(404, "not found")
        root = WEB.resolve()
        p = (root / rel).resolve()
        if not p.is_relative_to(root) or not p.is_file() or p.suffix not in MIME:
            raise _Reject(404, "not found")
        self._send(200, p.read_bytes(), MIME[p.suffix])

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise _Reject(400, "chunked bodies not supported")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise _Reject(400, "bad Content-Length")
        if n < 0:
            raise _Reject(400, "bad Content-Length")
        if n > MAX_BODY:
            raise _Reject(413, "body too large")
        if not n:
            return {}
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise _Reject(415, "send application/json")
        try:
            self._consumed = True
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            raise _Reject(400, "bad JSON")
        if not isinstance(body, dict):
            raise _Reject(400, "body must be a JSON object")
        return body

    def _api(self):
        board = self.server.board
        origin = self.headers.get("Origin")
        if origin is not None and origin != board.origin:
            raise _Reject(403, "forbidden")
        url = urlsplit(self.path)
        route = (self.command, url.path)
        if route == ("GET", "/api/alive"):  # tokenless on purpose: the hook checks the proof before it sends its token
            nonce = parse_qs(url.query).get("nonce", [""])[0]
            if not NONCE.fullmatch(nonce):
                raise _Reject(400, "need nonce: 32-64 hex characters")
            return self._json(200, {"alive": board.alive(), "proof": proof(board.hook_token, nonce)})
        want = board.hook_token if route in HOOK_ROUTES else board.page_token
        tok = self.headers.get("X-Sure-Token", "")
        if not hmac.compare_digest(tok.encode("utf-8", "surrogateescape"), want.encode()):
            raise _Reject(403, "forbidden")  # before any body is read
        if route == ("GET", "/api/state"):
            return self._json(200, board.state())
        if route == ("GET", "/api/replay"):
            return self._json(200, board.replay(url.query))
        posts = {("POST", "/api/request"): board.request, ("POST", "/api/decide"): board.decide,
                 ("POST", "/api/withdraw"): board.withdraw, ("POST", "/api/heartbeat"): board.heartbeat,
                 ("POST", "/api/trust"): lambda b: board.set_trust(b, True),
                 ("DELETE", "/api/trust"): lambda b: board.set_trust(b, False)}
        if route not in posts:
            raise _Reject(404, "not found")
        self._json(200, posts[route](self._body()))


# client half: used by the hook. Every failure means None ("no board"), never an exception.

def _runtime():
    """(port, token) of a live board, else None. Reads one small file; never waits."""
    try:
        rt = json.loads(runtime_path().read_text(encoding="utf-8"))
        port, token, pid = rt["port"], rt["token"], rt["pid"]
    except Exception:
        return None
    ok = isinstance(port, int) and 0 < port < 65536 and isinstance(token, str) and token
    return (port, token) if ok and pid_alive(pid, strict=True) else None


QUICK = 0.5  # total budget for the alive probe and the withdraw
CONNECT = 0.3  # Windows retries a refused localhost connect for ~1 s; a live board accepts in ms


def _kill(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)  # wakes a read blocked in another thread
    except Exception:
        pass


def _call(rt, method, path, body, budget, connect=None, token=True):
    """JSON object from a 200 answer, else {}. `budget` bounds the WHOLE call, not each socket
    operation: a trickling or never-ending server is cut off by a timer that shuts the socket.
    Connects to the literal 127.0.0.1 so Host matches. token=False sends no X-Sure-Token."""
    end = time.monotonic() + budget
    c = http.client.HTTPConnection("127.0.0.1", rt[0], timeout=min(connect or budget, budget))
    timer = None
    try:
        c.connect()
        sock = c.sock  # kept: http.client drops c.sock once a closing response is read
        sock.settimeout(max(end - time.monotonic(), 0.01))
        timer = threading.Timer(max(end - time.monotonic(), 0), _kill, (sock,))
        timer.daemon = True
        timer.start()
        headers, data = ({"X-Sure-Token": rt[1]} if token else {}), None
        if body is not None:
            headers["Content-Type"], data = "application/json", json.dumps(body).encode("utf-8")
        c.request(method, path, body=data, headers=headers)
        sock.settimeout(max(end - time.monotonic(), 0.01))
        r = c.getresponse()
        if r.status != 200:
            return {}
        raw = b""
        while not r.isclosed() and len(raw) <= MAX_BODY and time.monotonic() < end:
            sock.settimeout(max(end - time.monotonic(), 0.01))
            chunk = r.read(4096)
            if not chunk:
                break
            raw += chunk
        if time.monotonic() >= end or len(raw) > MAX_BODY:
            return {}  # out of time or too big: a truncated body is a failure, not an answer
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    finally:
        if timer:
            timer.cancel()
        c.close()


def _probe(rt, budget):
    """The listener's alive answer, only if it proved it holds the hook token (HMAC over a fresh random
    nonce). No token and no card text go out here, so an impostor on the port learns nothing."""
    nonce = secrets.token_hex(16)
    got = _call(rt, "GET", f"/api/alive?nonce={nonce}", None, budget, CONNECT, token=False)
    p = got.get("proof")
    if not isinstance(p, str) or not p.isascii() or not hmac.compare_digest(p, proof(rt[1], nonce)):
        return None
    return got


def ask_board(request, wait_s=WAIT_SECONDS):
    """"allow_once", "block", "ask", or None: no board, stale file, dead pid, impostor, no page attached, any error."""
    try:
        rt = _runtime()
        got = _probe(rt, QUICK) if rt else None
        if not got or got.get("alive") is not True:
            return None  # no proven board, or nobody is watching it: do not wait
        got = _call(rt, "POST", "/api/request", request, wait_s + 5)
        d = got.get("decision")
        if d == "ask" and got.get("by") != "you":
            return None  # nobody decided (timeout, stop, replaced, cap): same card, but no decision to record
        return d if d in DECISIONS else None
    except Exception:
        return None


def withdraw(request_id):
    """Best effort: release a card answered elsewhere. An unknown id is a harmless 404."""
    try:
        end = time.monotonic() + QUICK  # one budget for probe + withdraw
        rt = _runtime()
        if rt and _probe(rt, QUICK) is not None and end - time.monotonic() > 0.01:
            _call(rt, "POST", "/api/withdraw", {"request_id": request_id, "reason": "answered_elsewhere"},
                  end - time.monotonic(), CONNECT)
    except Exception:
        pass


def serve(host="127.0.0.1", port=0, runtime=None):
    """Run the board until Ctrl+C. Refuses (SystemExit) if another live board owns the runtime file."""
    b = Board(host, port, runtime)
    b.start()
    print(b.url, flush=True)
    try:
        while b.thread.is_alive():
            b.thread.join(0.5)  # short joins so Ctrl+C is seen on Windows
    except KeyboardInterrupt:
        pass
    finally:
        b.stop()
