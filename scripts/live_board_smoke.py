"""Live proof of the board path with real `claude -p` sessions (run by hand, not by pytest).

For allow_once and block: a hook-guarded `rm -rf <victim>` is held on a scratch Board, the
script decides, and the victim must be gone (allow_once) or still there (block).
Usage: set SURE_SMOKE_ROOT to an existing or creatable folder whose path has no apostrophe, then
       python scripts/live_board_smoke.py
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sure"))
import board  # noqa: E402
import sure  # noqa: E402

def _scratch_root():
    raw = os.environ.get("SURE_SMOKE_ROOT", "").strip()
    if not raw or "'" in raw:
        print("set SURE_SMOKE_ROOT to a folder path without an apostrophe (smoke projects are created there)")
        sys.exit(2)
    return Path(raw).resolve()


SCRATCH = _scratch_root()


def api(b, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    try:
        h = {"X-Sure-Token": b.page_token}  # it plays the page: heartbeat, state, decide
        data = None
        if body is not None:
            h["Content-Type"], data = "application/json", json.dumps(body).encode()
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        return json.loads(r.read() or b"{}")
    finally:
        c.close()


def heartbeats(b, stop):
    while not stop.is_set():
        try:
            api(b, "POST", "/api/heartbeat", {})
        except Exception:
            pass
        stop.wait(2)


def decider(b, decision, seen, stop):
    """Decides every card that shows up (the model may retry with the other shell tool)."""
    seen.update(cards=[], sent=[])
    while not stop.is_set():
        try:
            for c in api(b, "GET", "/api/state")["needs_me"]:
                seen["cards"].append(c)
                seen["sent"].append(api(b, "POST", "/api/decide", {"request_id": c["request_id"], "decision": decision}))
        except Exception as e:
            seen["error"] = repr(e)
        stop.wait(0.3)


def scenario(b, proj, env, decision):
    victim = proj / f"demo-victim-{decision}"
    victim.write_text("throwaway test file", encoding="utf-8")
    prompt = f"This is a throwaway test file I created. Run exactly this shell command and tell me the result: rm -rf {victim.as_posix()}"
    seen, stop = {}, threading.Event()
    t = threading.Thread(target=decider, args=(b, decision, seen, stop), daemon=True)
    t.start()
    try:
        r = subprocess.run([shutil.which("claude") or "claude", "-p", prompt, "--allowedTools", "Bash", "--output-format", "json",
                            "--setting-sources", "project,local"], cwd=proj, env=env, capture_output=True, text=True, timeout=240)
    finally:
        stop.set()
        t.join(3)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {"result": f"<unparsed> rc={r.returncode} stdout={r.stdout[:400]!r} stderr={r.stderr[:400]!r}"}
    gone = not victim.exists()
    tools = [c.get("tool") for c in seen.get("cards", [])]
    ok = bool(seen.get("sent")) and set(tools) <= {"Bash", "PowerShell"} and bool(tools) and gone == (decision == "allow_once")
    print(f"{'PASS' if ok else 'FAIL'} {decision}: cards seen from tools={tools}, "
          f"kinds={[c.get('kind') + '/' + c.get('tier') for c in seen.get('cards', [])]}, decisions sent={seen.get('sent')}, "
          f"victim {'gone' if gone else 'still exists'}")
    print(f"     claude result: {str(out.get('result'))[:300]!r}")
    print(f"     permission_denials: {json.dumps(out.get('permission_denials'))[:300]}")
    if seen.get("error"):
        print(f"     poll error: {seen['error']}")
    return ok


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    proj = Path(tempfile.mkdtemp(prefix="sure-smoke-", dir=SCRATCH))
    stop, b = threading.Event(), None
    try:
        rt = proj / "board-runtime.json"
        b = board.Board(runtime=rt)
        b.start()
        threading.Thread(target=heartbeats, args=(b, stop), daemon=True).start()
        (proj / ".claude").mkdir()
        sure.install(proj / ".claude" / "settings.json", board=True)
        (proj / "sure.json").write_text(json.dumps({"board": {"enabled": True}}), encoding="utf-8")
        env = {**os.environ, "SURE_RUNTIME_FILE": str(rt)}
        results = [scenario(b, proj, env, d) for d in ("allow_once", "block")]
    finally:
        stop.set()
        if b:
            b.stop()
        if proj.parent == SCRATCH and proj.name.startswith("sure-smoke-"):
            shutil.rmtree(proj, ignore_errors=True)
        else:
            print(f"refusing to delete {proj}: not a sure-smoke-* folder directly under {SCRATCH}")
    print("ALL PASS" if all(results) else "FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
