"""Task 6: the PreToolUse hook waits on the board and fails open."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import board  # noqa: E402
import sure  # noqa: E402
from test_board import call, wait_pending  # noqa: E402

REAL_QUICK, REAL_CONNECT = board.QUICK, board.CONNECT  # the budget tests below run on these, not the fixture's

REQ = {"tool": "Bash", "kind": "delete", "tier": "hard", "what": "Delete files", "why": "w",
       "cost": "c", "undo": "u", "session": "s1", "cwd": str(Path(__file__).resolve().parent), "request_id": "r1"}


@pytest.fixture
def rt(tmp_path, monkeypatch):
    p = tmp_path / "home" / "board.json"
    monkeypatch.setattr(board, "runtime_path", lambda: p)
    monkeypatch.setattr(board, "WAIT_SECONDS", 3)
    monkeypatch.setattr(board, "QUICK", 5)  # B9.4: a loaded machine must not make the probe give up before the hook posts
    monkeypatch.setattr(board, "CONNECT", 3)
    return p


@pytest.fixture
def b(rt):
    bd = board.Board(runtime=rt)
    bd.start()
    yield bd
    bd.stop()


def beat(bd):
    assert call(bd, "POST", "/api/heartbeat", {})[0] == 200


def project(tmp_path, enabled=True):
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    if enabled is not None:
        (d / "sure.json").write_text(json.dumps({"board": {"enabled": enabled}}), encoding="utf-8")
    return d


def hook(cwd, cmd="rm -rf build", tid="t1", tool="Bash"):
    inp = {"command": cmd} if tool == "Bash" else {"url": "http://x", "query": "x"}
    return json.dumps({"tool_name": tool, "tool_input": inp, "cwd": str(cwd),
                       "session_id": "s1", "tool_use_id": tid})


def run_bg(stdin, event="pre", cwd="."):
    out = {}
    t = threading.Thread(target=lambda: out.update(res=sure.main([event], stdin, cwd)), daemon=True)
    t.start()
    return t, out


def decision(text):
    return json.loads(text)["hookSpecificOutput"]["permissionDecision"]


def pending(bd):
    s = call(bd, "GET", "/api/state")[1]
    return s["incoming"] + s["needs_me"]


# ---- the client ----

def test_no_runtime_file_returns_none(rt):
    t0 = time.monotonic()
    assert board.ask_board(REQ, 2) is None
    assert time.monotonic() - t0 < 1


def test_stale_runtime_file_dead_pid_returns_none_fast(rt):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(10)
    rt.parent.mkdir(parents=True)
    rt.write_text(json.dumps({"port": 1, "token": "x", "pid": p.pid}), encoding="utf-8")
    t0 = time.monotonic()
    assert board.ask_board(REQ, 2) is None
    assert time.monotonic() - t0 < 1


def test_garbage_runtime_file_returns_none(rt):
    rt.parent.mkdir(parents=True)
    for text in ["", "not json", "[]", '{"port": "x", "token": 1, "pid": null}']:
        rt.write_text(text, encoding="utf-8")
        assert board.ask_board(REQ, 2) is None


def test_no_heartbeat_returns_none_without_waiting(b):
    t0 = time.monotonic()
    assert board.ask_board(REQ, 2) is None
    assert time.monotonic() - t0 < 1
    assert pending(b) == []


def test_alive_route_needs_nonce_not_token(b):  # A2: alive is tokenless; it proves the board with HMAC(nonce)
    n = "ab" * 16
    assert call(b, "GET", "/api/alive", token=False)[0] == 400
    st, body, _ = call(b, "GET", f"/api/alive?nonce={n}", token=False)
    assert st == 200 and body["alive"] is False
    beat(b)
    assert call(b, "GET", f"/api/alive?nonce={n}", token=False)[1]["alive"] is True


def test_non_absolute_cwd_returns_none(b):
    beat(b)
    t0 = time.monotonic()
    assert board.ask_board(dict(REQ, cwd="relative/dir"), 2) is None
    assert time.monotonic() - t0 < 1
    assert pending(b) == []


def test_client_returns_decision(b):
    beat(b)
    out = {}
    t = threading.Thread(target=lambda: out.update(d=board.ask_board(REQ, 2)), daemon=True)
    t.start()
    wait_pending(b, 1)
    assert call(b, "POST", "/api/decide", {"request_id": "r1", "decision": "allow_once"})[0] == 200
    t.join(5)
    assert out["d"] == "allow_once"


# ---- main ----

def _decide_main(b, tmp_path, d):
    beat(b)
    stdin = hook(project(tmp_path))
    t, out = run_bg(stdin)
    wait_pending(b, 1)
    assert call(b, "POST", "/api/decide", {"request_id": "t1", "decision": d})[0] == 200
    t.join(5)
    assert not t.is_alive()
    return out["res"]


def test_main_block_decision_emits_deny(b, tmp_path):
    res = _decide_main(b, tmp_path, "block")
    h = json.loads(res)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert h["permissionDecisionReason"].startswith("WHAT:")
    led = (tmp_path / "proj" / ".sure" / "ledger.md").read_text(encoding="utf-8")
    assert led.index("asked [delete/hard]") < led.index("decided [delete/hard] block")
    evs = [json.loads(x) for x in (tmp_path / "proj" / ".sure" / "events.jsonl").read_text().splitlines()]
    assert evs[-1] == dict(evs[-1], verb="decided", kind="delete", tier="hard", via="board",
                           session="s1", request_id="t1")


def test_main_allow_once_emits_allow(b, tmp_path):
    res = _decide_main(b, tmp_path, "allow_once")
    assert decision(res) == "allow"
    assert "decided [delete/hard] allow_once" in (tmp_path / "proj" / ".sure" / "ledger.md").read_text()


def test_main_ask_decision_emits_normal_ask(b, tmp_path):
    res = _decide_main(b, tmp_path, "ask")
    (tmp_path / "proj" / "sure.json").unlink()
    stdin = hook(tmp_path / "proj")
    assert res == sure.main(["pre"], stdin, str(tmp_path / "proj"))  # same bytes as with no board at all
    assert "decided [delete/hard] ask" in (tmp_path / "proj" / ".sure" / "ledger.md").read_text()  # B4: Ask Claude is logged


def test_board_dies_mid_wait_returns_ask_not_error(b, tmp_path):
    beat(b)
    stdin = hook(project(tmp_path))
    t, out = run_bg(stdin)
    wait_pending(b, 1)
    b.stop()
    t.join(5)
    assert not t.is_alive()
    assert decision(out["res"]) == "ask"
    assert "error" not in (tmp_path / "proj" / ".sure" / "ledger.md").read_text()


def test_main_with_board_disabled_unchanged(b, tmp_path):
    beat(b)
    stdin = hook(project(tmp_path, enabled=None))
    today = sure.main(["pre"], stdin, str(tmp_path / "proj"))
    risk = sure.classify("Bash", {"command": "rm -rf build"})
    assert today == json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                       "permissionDecisionReason": sure.render_card(risk)}})
    for man in [{"board": {"enabled": False}}, {"board": True}, {"board": "on"}, {"board": {}}]:
        (tmp_path / "proj" / "sure.json").write_text(json.dumps(man), encoding="utf-8")
        assert sure.main(["pre"], stdin, str(tmp_path / "proj")) == today
        assert sure.main(["post"], stdin, str(tmp_path / "proj")) == ""
    assert pending(b) == []


def test_quiet_kind_never_reaches_board(b, tmp_path, monkeypatch):
    beat(b)
    called = []
    monkeypatch.setattr(board, "ask_board", lambda *a, **k: called.append(a))
    stdin = hook(project(tmp_path), tool="WebSearch")
    assert sure.main(["pre"], stdin, str(tmp_path / "proj")) == ""
    assert called == [] and pending(b) == []


def test_two_simultaneous_hook_calls_get_their_own_decisions(b, tmp_path):
    beat(b)
    proj = project(tmp_path)
    t1, o1 = run_bg(hook(proj, tid="t1"))
    t2, o2 = run_bg(hook(proj, tid="t2"))
    wait_pending(b, 2)
    assert call(b, "POST", "/api/decide", {"request_id": "t2", "decision": "block"})[0] == 200
    t2.join(5)
    assert decision(o2["res"]) == "deny"
    assert t1.is_alive()
    assert call(b, "POST", "/api/decide", {"request_id": "t1", "decision": "allow_once"})[0] == 200
    t1.join(5)
    assert decision(o1["res"]) == "allow"


def test_post_hook_withdraw_releases_waiting_request(b, tmp_path):
    beat(b)
    proj = project(tmp_path)
    t, out = run_bg(hook(proj, tid="t1"))
    wait_pending(b, 1)
    t0 = time.monotonic()
    assert sure.main(["post"], hook(proj, tid="t1"), str(proj)) == ""
    assert time.monotonic() - t0 < 1
    t.join(5)
    assert decision(out["res"]) == "ask"
    assert pending(b) == []


def test_withdraw_unknown_id_and_no_board_are_harmless(rt, tmp_path):
    proj = project(tmp_path)
    t0 = time.monotonic()
    assert sure.main(["failed"], hook(proj, tid="nope"), str(proj)) == ""  # no runtime file
    assert time.monotonic() - t0 < 1
    bd = board.Board(runtime=rt)
    bd.start()
    try:
        assert call(bd, "POST", "/api/withdraw", {"request_id": "nope"})[0] == 404  # the server's answer
        t0 = time.monotonic()
        assert sure.main(["post"], hook(proj, tid="nope"), str(proj)) == ""  # same 404, ignored
        assert time.monotonic() - t0 < 1
    finally:
        bd.stop()


def test_main_survives_closed_stderr(b, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    stdin = hook(project(tmp_path))
    assert decision(sure.main(["pre"], stdin, str(tmp_path / "proj"))) == "ask"  # no heartbeat: ask at once


def _normal_ask(proj):
    risk = sure.classify("Bash", {"command": "rm -rf build"})
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                              "permissionDecisionReason": sure.render_card(risk)}})


def test_board_import_failure_still_asks(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "board", None)  # `import board` raises ImportError
    proj = project(tmp_path)
    assert sure.main(["pre"], hook(proj), str(proj)) == _normal_ask(proj)
    assert sure.main(["post"], hook(proj), str(proj)) == ""
    assert "error" not in (proj / ".sure" / "ledger.md").read_text()


def test_board_drops_connection_mid_wait_returns_ask(b, tmp_path):
    beat(b)

    def crash(body):
        raise ConnectionResetError()  # _guarded swallows it: the socket closes with no response

    b.request = crash
    t0 = time.monotonic()
    assert board.ask_board(REQ, 2) is None
    proj = project(tmp_path)
    assert sure.main(["pre"], hook(proj), str(proj)) == _normal_ask(proj)
    assert time.monotonic() - t0 < 2


# ---- hostile or dead servers: probe and withdraw stay bounded in total, not per socket operation ----

def _fake_server(talk):
    """Listening socket on 127.0.0.1; each connection gets talk(conn) in a daemon thread."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.1)
    stop, conns = threading.Event(), []

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            conns.append(conn)
            threading.Thread(target=lambda: _quietly(talk, conn, stop), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()

    def close():
        stop.set()
        for s in [srv] + conns:
            s.close()
    return srv.getsockname()[1], close


def _quietly(talk, conn, stop):
    try:
        conn.recv(65536)
        talk(conn, stop)
    except OSError:
        pass


def _trickle(conn, stop):
    for ch in b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + b'{"alive": true}' * 100:
        if stop.wait(0.2):
            return
        conn.sendall(bytes([ch]))


def _chunked_forever(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
    while not stop.wait(0.01):
        conn.sendall(b"1\r\n \r\n")


def _point_runtime(rt, port):
    rt.parent.mkdir(parents=True, exist_ok=True)
    rt.write_text(json.dumps({"port": port, "token": "x", "pid": os.getpid()}), encoding="utf-8")


@pytest.mark.parametrize("talk", [_trickle, _chunked_forever])
def test_hostile_server_bounded(rt, tmp_path, talk, monkeypatch):
    monkeypatch.setattr(board, "QUICK", REAL_QUICK)
    monkeypatch.setattr(board, "CONNECT", REAL_CONNECT)
    port, close = _fake_server(talk)
    try:
        _point_runtime(rt, port)
        proj = project(tmp_path)
        for fn in (lambda: board.ask_board(REQ, 2), lambda: board.withdraw("t1"),
                   lambda: sure.main(["post"], hook(proj), str(proj))):
            t0 = time.monotonic()
            fn()
            assert time.monotonic() - t0 < 1.5
        t0 = time.monotonic()
        assert sure.main(["pre"], hook(proj), str(proj)) == _normal_ask(proj)
        assert time.monotonic() - t0 < 1.5
    finally:
        close()


def test_live_pid_dead_port_bounded(rt, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "QUICK", REAL_QUICK)
    monkeypatch.setattr(board, "CONNECT", REAL_CONNECT)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now: connects are refused (slowly, on Windows)
    _point_runtime(rt, port)
    for fn in (lambda: board.ask_board(REQ, 2), lambda: board.withdraw("t1")):
        t0 = time.monotonic()
        assert fn() is None
        assert time.monotonic() - t0 < 0.6


RUNNER = """import sys
sys.stderr = None
sys.path.insert(0, sys.argv[1])
import sure
out = sure.main(["pre"], '{"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}', sys.argv[2])
sys.stdout.write(out + "OK")
"""


def test_import_with_broken_directory_and_no_stderr(tmp_path):
    src = Path(sure.__file__).resolve().parent
    shutil.copytree(src, tmp_path / "sure", ignore=shutil.ignore_patterns("__pycache__", "web"))
    (tmp_path / "directory").mkdir()
    (tmp_path / "directory" / "permissions.json").write_text("not json", encoding="utf-8")
    (tmp_path / "run.py").write_text(RUNNER, encoding="utf-8")
    r = subprocess.run([sys.executable, str(tmp_path / "run.py"), str(tmp_path / "sure"), str(tmp_path)],
                       capture_output=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"OK"  # no rules: silent hook, nothing else on stdout
