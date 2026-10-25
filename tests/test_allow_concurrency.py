"""Several Sure? processes can write ~/.sure/allow.json at once (a hook issuing while the owner redeems): no update may be lost."""
import os
import subprocess
import sys
from pathlib import Path

SURE_DIR = Path(__file__).resolve().parent.parent / "sure"
sys.path.insert(0, str(SURE_DIR))
import allow  # noqa: E402

WORKER = (
    "import sys; sys.path.insert(0, sys.argv[1]); import allow\n"
    "for i in range(int(sys.argv[3])): allow.issue(sys.argv[2] + str(i), 'x')\n"
)


def test_concurrent_writers_lose_no_update(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SURE_HOME", str(home))
    env = {**os.environ, "SURE_HOME": str(home)}
    allow.issue("seed", "seed")  # creates the file and key first
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(SURE_DIR), f"w{n}-", "3"], env=env) for n in range(6)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    assert len(allow.pending()) == 1 + 6 * 3  # every issue() survived (19, under the 20-entry cap)


def test_a_held_lock_does_not_hang_the_hook(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("SURE_HOME", str(tmp_path / "home"))
    allow.issue("a", "x")
    with allow._lock():  # simulate a writer that never lets go
        t = time.perf_counter()
        allow.issue("b", "x")  # must give up waiting, not block forever
        assert time.perf_counter() - t < 10
