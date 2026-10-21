import http.client
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import board  # noqa: E402

REQ = {"tool": "Bash", "kind": "delete", "tier": "hard", "what": "Delete files", "why": "w",
       "cost": "c", "undo": "u", "session": "s1", "cwd": str(Path(__file__).resolve().parent), "request_id": "r1"}


@pytest.fixture
def b(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "QUICK", 5)  # B9.4: any client call made under load gets a generous budget
    monkeypatch.setattr(board, "CONNECT", 3)
    bd = board.Board(runtime=tmp_path / "board.json")
    bd.start()
    yield bd
    bd.stop()


HOOK_ROUTES = ("/api/request", "/api/withdraw")


def tok(bd, path):
    """The token the real caller would send: the hook's for request/withdraw, the page's otherwise."""
    return bd.hook_token if path.split("?")[0] in HOOK_ROUTES else bd.page_token


def call(bd, method, path, body=None, token=True, headers=None):
    """token: True = the right token for the route, "hook"/"page" = that one, False = none."""
    c = http.client.HTTPConnection("127.0.0.1", bd.port, timeout=5)
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Sure-Token"] = {"hook": bd.hook_token, "page": bd.page_token}.get(token) or tok(bd, path)
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw), r
    except ValueError:
        return r.status, raw, r


def post_bg(bd, body):
    out = {}
    t = threading.Thread(target=lambda: out.update(res=call(bd, "POST", "/api/request", body)), daemon=True)
    t.start()
    return t, out


def see(bd, cwd, monkeypatch):
    """Ruling A10: replay (like trust) only reads a cwd seen on a card, so post one quick request from it."""
    monkeypatch.setattr(board, "WAIT_SECONDS", 0.05)
    assert call(bd, "POST", "/api/request", dict(REQ, request_id="see", cwd=str(cwd)))[1] == {"decision": "ask"}


def wait_pending(bd, n):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        s = call(bd, "GET", "/api/state")[1]
        if len(s["incoming"]) + len(s["needs_me"]) >= n:
            return s
        time.sleep(0.02)
    raise AssertionError("request never registered")


def test_request_waits_then_decide_releases(b):
    t, out = post_bg(b, REQ)
    wait_pending(b, 1)
    assert t.is_alive()
    assert call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "block"})[0] == 200
    t.join(5)
    assert not t.is_alive()
    assert out["res"][1] == {"decision": "block"}
    s = call(b, "GET", "/api/state")[1]
    assert [c["request_id"] for c in s["declined"]] == ["r1"]
    assert s["flow"] == [{"session": "s1", "kind": "delete", "handler": "you", "count": 1}]


def test_two_simultaneous_requests_independent(b):
    t1, o1 = post_bg(b, REQ)
    t2, o2 = post_bg(b, dict(REQ, request_id="r2"))
    wait_pending(b, 2)
    assert call(b, "POST", "/api/decide", {"request_id": "r2", "decision": "allow_once"})[0] == 200
    t2.join(5)
    assert o2["res"][1] == {"decision": "allow_once"}
    assert t1.is_alive()
    s = call(b, "GET", "/api/state")[1]
    assert [c["request_id"] for c in s["incoming"] + s["needs_me"]] == ["r1"]
    assert [c["request_id"] for c in s["done"]] == ["r2"]
    b.stop()  # releases r1 with ask
    t1.join(5)
    assert not t1.is_alive() and o1["res"][1] == {"decision": "ask"}


def test_incoming_moves_to_needs_me_after_heartbeat(b):
    t, _ = post_bg(b, REQ)
    s = wait_pending(b, 1)
    assert len(s["incoming"]) == 1 and not s["needs_me"]
    assert not b.alive()
    assert call(b, "POST", "/api/heartbeat")[0] == 200
    assert b.alive()
    s = call(b, "GET", "/api/state")[1]
    assert len(s["needs_me"]) == 1 and not s["incoming"]
    b.stop()
    t.join(5)


def test_heartbeat_expires(b, monkeypatch):
    monkeypatch.setattr(board, "HEARTBEAT_TTL", 0.05)
    call(b, "POST", "/api/heartbeat")
    time.sleep(0.1)
    assert not b.alive()


def test_missing_token_403(b):
    assert call(b, "GET", "/api/state", token=False)[0] == 403
    assert call(b, "GET", "/api/state", headers={"X-Sure-Token": "wrong"})[0] == 403
    assert call(b, "GET", "/api/state", headers={"X-Sure-Token": "\u00e9"})[0] == 403
    st, _, r = call(b, "POST", "/api/decide", {"request_id": "x", "decision": "block"}, token=False)
    assert st == 403 and r.getheader("Cache-Control") == "no-store"


def test_foreign_origin_403(b):
    t, out = post_bg(b, REQ)
    wait_pending(b, 1)
    for o in ("http://evil.example", "null", f"http://localhost:{b.port}"):
        st = call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "allow_once"}, headers={"Origin": o})[0]
        assert st == 403
    assert t.is_alive()
    ok = call(b, "GET", "/api/state", headers={"Origin": f"http://127.0.0.1:{b.port}"})
    assert ok[0] == 200
    b.stop()
    t.join(5)
    assert out["res"][1] == {"decision": "ask"}


def test_timeout_returns_ask(b, monkeypatch):
    monkeypatch.setattr(board, "WAIT_SECONDS", 0.2)
    st, body, _ = call(b, "POST", "/api/request", REQ)
    assert (st, body) == (200, {"decision": "ask"})
    s = call(b, "GET", "/api/state")[1]
    assert not s["incoming"] and not s["needs_me"]
    assert s["done"][0]["outcome"] == "timeout"


def test_withdraw_releases_with_ask(b):
    t, out = post_bg(b, REQ)
    wait_pending(b, 1)
    assert call(b, "POST", "/api/withdraw", {"request_id": "r1", "reason": "answered elsewhere"})[0] == 200
    t.join(5)
    assert out["res"][1] == {"decision": "ask"}
    assert call(b, "POST", "/api/withdraw", {"request_id": "r1"})[0] == 404


def test_duplicate_request_id_replaces_and_releases_old(b):
    t1, o1 = post_bg(b, REQ)
    wait_pending(b, 1)
    t2, o2 = post_bg(b, dict(REQ, what="second"))
    t1.join(5)
    assert o1["res"][1] == {"decision": "ask"}
    s = wait_pending(b, 1)
    assert [c["what"] for c in s["incoming"] + s["needs_me"]] == ["second"]
    call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "block"})
    t2.join(5)
    assert o2["res"][1] == {"decision": "block"}


def test_decide_validation(b):
    assert call(b, "POST", "/api/decide", {"request_id": "nope", "decision": "block"})[0] == 404
    t, out = post_bg(b, dict(REQ, tier="impossible", kind="database"))
    wait_pending(b, 1)
    for d in ("ask", "always_allow", "maybe"):
        assert call(b, "POST", "/api/decide", {"request_id": "r1", "decision": d})[0] == 400
    assert t.is_alive()
    call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "allow_once"})
    t.join(5)
    assert out["res"][1] == {"decision": "allow_once"}


@pytest.mark.parametrize("bad", [{"request_id": ""}, {"request_id": "x" * 201}, {"request_id": 5},
                                 {"tier": "weird"}, {"kind": None}])
def test_request_validation(b, bad):
    assert call(b, "POST", "/api/request", dict(REQ, **bad))[0] == 400


def test_bad_bodies(b):
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    c.request("POST", "/api/decide", body=b"{not json", headers={"X-Sure-Token": b.page_token, "Content-Type": "application/json"})
    assert c.getresponse().status == 400
    c.close()
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    c.request("POST", "/api/decide", body=b"[1]", headers={"X-Sure-Token": b.page_token, "Content-Type": "application/json"})
    assert c.getresponse().status == 400
    c.close()
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    c.request("POST", "/api/decide", body=b"a=b", headers={"X-Sure-Token": b.page_token, "Content-Type": "text/plain"})
    assert c.getresponse().status == 415
    c.close()


def test_body_too_large_413(b):
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    c.putrequest("POST", "/api/request")
    c.putheader("X-Sure-Token", b.hook_token)
    c.putheader("Content-Type", "application/json")
    c.putheader("Content-Length", "70000")
    c.endheaders()
    assert c.getresponse().status == 413
    c.close()
    assert call(b, "GET", "/api/state")[0] == 200


def test_trust_hard_kind_400(b, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "WAIT_SECONDS", 0.05)
    call(b, "POST", "/api/request", dict(REQ, cwd=str(tmp_path)))  # trust needs a cwd seen on a card
    assert call(b, "POST", "/api/trust", {"cwd": str(tmp_path), "kind": "delete"})[0] == 400
    assert not (tmp_path / "sure.json").exists()
    assert call(b, "POST", "/api/trust", {"cwd": str(tmp_path / "missing"), "kind": "install"})[0] == 400
    assert call(b, "POST", "/api/trust", {"cwd": str(tmp_path), "kind": "install"})[0] == 200
    assert json.loads((tmp_path / "sure.json").read_text())["auto_allow"] == ["install"]
    assert call(b, "DELETE", "/api/trust", {"cwd": str(tmp_path), "kind": "install"})[0] == 200
    assert json.loads((tmp_path / "sure.json").read_text())["auto_allow"] == []


def test_trusted_kind_still_waits_on_the_board(b, tmp_path):
    # B3: the board never auto-releases; the hook already stays silent for auto_allow kinds
    (tmp_path / "sure.json").write_text(json.dumps({"auto_allow": ["install", "delete"]}))
    t1, _ = post_bg(b, dict(REQ, kind="install", tier="easy", cwd=str(tmp_path)))
    t2, _ = post_bg(b, dict(REQ, request_id="r2", cwd=str(tmp_path)))
    s = wait_pending(b, 2)
    assert s["delegated"] == [] and s["flow"] == []
    assert s["trusted"] == {str(tmp_path): ["install", "delete"]}
    assert t1.is_alive() and t2.is_alive()
    b.stop()
    t1.join(5)
    t2.join(5)


def test_static_and_index(b):
    st, raw, r = call(b, "GET", "/", token=False)
    assert st == 200 and r.getheader("Content-Type").startswith("text/html")
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    for p in ("/static/../board.py", "/static/..%2fboard.py", "/static/%2e%2e/board.py",
              "/static/..\\board.py", "/static/%5c..%5cboard.py", "/static/C:/Windows/win.ini",
              "/static//etc/passwd", "/static/nope.js", "/nope"):
        assert call(b, "GET", p, token=False)[0] == 404, p


def test_replay(b, tmp_path, monkeypatch):
    see(b, tmp_path, monkeypatch)
    assert call(b, "GET", "/api/replay?days=7")[0] == 400
    assert call(b, "GET", f"/api/replay?days=0&cwd={quote(str(tmp_path))}")[0] == 400
    assert call(b, "GET", f"/api/replay?days=x&cwd={quote(str(tmp_path))}")[0] == 400
    assert call(b, "GET", f"/api/replay?cwd={quote(str(tmp_path / 'missing'))}")[0] == 400
    (tmp_path / ".sure").mkdir()
    (tmp_path / ".sure" / "events.jsonl").write_text(
        json.dumps({"ts": "2999-01-01T00:00:00", "verb": "auto", "kind": "install", "tier": "easy", "session": "s"}) + "\n")
    st, body, _ = call(b, "GET", f"/api/replay?days=3&cwd={quote(str(tmp_path))}")
    assert st == 200 and len(body["events"]) == 1 and isinstance(body["report"], str)
    assert "error" not in body


def test_replay_corrupt_events_no_500(b, tmp_path, monkeypatch):
    see(b, tmp_path, monkeypatch)
    (tmp_path / ".sure").mkdir()
    (tmp_path / ".sure" / "events.jsonl").write_bytes(b"\xff\n")
    st, body, _ = call(b, "GET", f"/api/replay?cwd={quote(str(tmp_path))}")
    assert st == 200 and body["events"] == [] and body["report"] == "" and body["error"]


def test_handler_exception_is_500_and_server_survives(b, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(b, "state", boom)
    st, body, _ = call(b, "GET", "/api/state")
    assert st == 500 and "secret" not in json.dumps(body)
    monkeypatch.undo()
    assert call(b, "GET", "/api/state")[0] == 200


def test_runtime_file_written_and_removed(tmp_path):
    rt = tmp_path / "board.json"
    bd = board.Board(runtime=rt)
    port = bd.start()
    try:
        d = json.loads(rt.read_text())
        assert d == {"port": port, "token": bd.hook_token, "pid": __import__("os").getpid()}
        assert bd.page_token.encode() not in rt.read_bytes()  # A1: the page token never touches disk
        assert bd.server.server_address[0] == "127.0.0.1"
    finally:
        bd.stop()
    assert not rt.exists()


def test_pid_alive():
    assert board.pid_alive(__import__("os").getpid())
    assert not board.pid_alive(2**31 - 1)
    assert not board.pid_alive(0) and not board.pid_alive(-1)


def test_second_serve_refuses_while_first_alive(tmp_path):
    rt = tmp_path / "board.json"
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        rt.write_text(json.dumps({"port": 1, "token": "t", "pid": p.pid}))
        before = rt.read_bytes()
        second = board.Board(runtime=rt)
        with pytest.raises(SystemExit):
            try:
                second.start()  # start(), not serve(): a regression fails fast instead of blocking
            finally:
                second.stop()
        assert rt.read_bytes() == before
    finally:
        p.kill()
        p.wait(5)
    # first one gone: a stale runtime file no longer blocks
    bd = board.Board(runtime=rt)
    bd.start()
    bd.stop()


def test_runtime_path():
    assert board.runtime_path() == Path.home() / ".sure" / "board.json"


def test_tier_comes_from_directory_not_body(b):
    t, out = post_bg(b, dict(REQ, kind="database", tier="easy"))
    s = wait_pending(b, 1)
    assert (s["incoming"] + s["needs_me"])[0]["tier"] == "impossible"
    assert call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "ask"})[0] == 400
    call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "block"})
    t.join(5)
    assert out["res"][1] == {"decision": "block"}


def test_unknown_kind_easy_tier_cannot_be_trusted(b, tmp_path):
    body = dict(REQ, kind="madeup", tier="easy", cwd=str(tmp_path))
    t, _ = post_bg(b, body)
    wait_pending(b, 1)
    assert call(b, "POST", "/api/trust", {"cwd": str(tmp_path), "kind": "madeup"})[0] == 400
    assert call(b, "POST", "/api/request", dict(body, tier="nope"))[0] == 400
    b.stop()
    t.join(5)


def test_bad_host_403(b):
    for host in ("evil.example", f"evil.example:{b.port}", "127.0.0.1:1"):
        st = call(b, "GET", "/", token=False, headers={"Host": host})[0]
        assert st == 403, host
        assert call(b, "GET", "/api/state", headers={"Host": host})[0] == 403
    assert call(b, "GET", "/api/state", headers={"Host": f"localhost:{b.port}"})[0] == 403
    assert call(b, "GET", "/api/state", headers={"Host": f"127.0.0.1:{b.port}"})[0] == 200


def test_request_during_stop_gets_ask(b):
    b._stopping = True  # what stop() sets first, before releasing waiters
    st, body, _ = call(b, "POST", "/api/request", REQ)
    assert (st, body) == (200, {"decision": "ask"})
    assert not call(b, "GET", "/api/state")[1]["needs_me"]


def test_long_poll_outlives_socket_timeout(b, monkeypatch):
    monkeypatch.setattr(board._Handler, "timeout", 0.2)
    t, out = post_bg(b, REQ)
    wait_pending(b, 1)
    time.sleep(0.5)
    call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "block"})
    t.join(5)
    assert out["res"][1] == {"decision": "block"}


def test_unc_cwd_rejected(b, tmp_path):
    for unc in (r"\\server\share", "//server/share", r"/\server\share", r"\??\UNC\localhost\C$\Windows",
                r"\\?\UNC\host\share", r"\\.\pipe\x", r"C:\proj::$DATA", "C:\\proj\x00", "proj", ""):
        assert call(b, "POST", "/api/request", dict(REQ, cwd=unc))[0] == 400, unc
        assert call(b, "GET", f"/api/replay?cwd={quote(unc)}")[0] == 400, unc
        assert call(b, "POST", "/api/trust", {"cwd": unc, "kind": "install"})[0] == 400, unc


def test_local_absolute_cwd_accepted(b, monkeypatch):
    monkeypatch.setattr(board, "WAIT_SECONDS", 0.05)
    for cwd in (r"C:\Users\x\proj", "C:/Users/x/proj"):
        assert call(b, "POST", "/api/request", dict(REQ, cwd=cwd))[1] == {"decision": "ask"}
    assert board._local_cwd(r"C:\Users\x\proj") and board._local_cwd("C:/Users/x/proj")


def test_trust_corrupt_sure_json_400(b, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "WAIT_SECONDS", 0.05)
    (tmp_path / "sure.json").write_text("{not json")
    call(b, "POST", "/api/request", dict(REQ, cwd=str(tmp_path)))
    st, body, _ = call(b, "POST", "/api/trust", {"cwd": str(tmp_path), "kind": "install"})
    assert (st, body) == (400, {"error": "sure.json is not valid JSON"})
    st, body, _ = call(b, "POST", "/api/trust", {"cwd": str(tmp_path), "kind": "delete"})
    assert (st, body) == (400, {"error": "only easy-tier kinds can be always allowed"})


def test_trust_needs_cwd_seen_on_a_card(b, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(board, "WAIT_SECONDS", 0.05)
    call(b, "POST", "/api/request", dict(REQ, cwd=str(tmp_path)))
    assert call(b, "POST", "/api/trust", {"cwd": str(other), "kind": "install"})[0] == 400
    assert call(b, "POST", "/api/trust", {"cwd": str(tmp_path), "kind": "install"})[0] == 200


def test_trust_write_error_is_500_json(b, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "WAIT_SECONDS", 0.05)
    call(b, "POST", "/api/request", dict(REQ, cwd=str(tmp_path)))

    def ro(*a):
        raise OSError("read-only")
    monkeypatch.setattr(board.trust, "add", ro)
    st, body, _ = call(b, "POST", "/api/trust", {"cwd": str(tmp_path), "kind": "install"})
    assert (st, body) == (500, {"error": "could not write sure.json"})


def test_nul_static_and_send_error_headers(b):
    assert call(b, "GET", "/static/a%00.js", token=False)[0] == 404
    st, _, r = call(b, "OPTIONS", "/", token=False)
    assert st == 501
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    assert r.getheader("Cache-Control") == "no-store"


def test_state_exposes_wait_seconds(b, monkeypatch):
    assert call(b, "GET", "/api/state")[1]["wait_seconds"] == board.WAIT_SECONDS
    monkeypatch.setattr(board, "WAIT_SECONDS", 7)
    assert call(b, "GET", "/api/state")[1]["wait_seconds"] == 7


def test_pending_card_deadline_in_counts_down(b, monkeypatch):
    monkeypatch.setattr(board, "WAIT_SECONDS", 30)
    t, out = post_bg(b, REQ)
    s = wait_pending(b, 1)
    d1 = (s["incoming"] + s["needs_me"])[0]["deadline_in"]
    assert 0 <= d1 <= 30
    time.sleep(0.3)
    s = call(b, "GET", "/api/state")[1]
    d2 = (s["incoming"] + s["needs_me"])[0]["deadline_in"]
    assert 0 <= d2 < d1
    call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "block"})
    t.join(5)
    assert out["res"][1] == {"decision": "block"}
    assert "deadline_in" not in call(b, "GET", "/api/state")[1]["declined"][0]


def test_rejected_requests_with_a_body_get_their_403_not_a_reset(b):
    """An unread request body plus close makes Windows reset the connection: drain it before answering."""
    small = {"request_id": "r1", "decision": "allow_once"}
    big = {"request_id": "x", "decision": "block", "pad": "p" * 30000}
    for body in (small, big):
        for _ in range(250):
            for hdr in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {"Host": "evil.example"}):
                assert call(b, "POST", "/api/decide", body, headers=hdr)[0] == 403
