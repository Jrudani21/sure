# Copyright (c) 2026 Janak Rudani
# SPDX-License-Identifier: MIT
"""Gaps found by the cross-guard evasion pilot: a command hidden as base64 that eval decodes and
runs, and a command written to a script file that the same command line then runs.
Every dangerous string below acts on a harmless placeholder path; nothing here is executed."""
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import sure  # noqa: E402


def kind(cmd):
    return sure._bash_kind(cmd)


def b64(s):
    return base64.b64encode(s.encode()).decode()


PLAIN = kind("rm -rf build")


def test_plain_command_is_a_card():
    assert PLAIN is not None


def test_eval_of_decoded_command_is_seen():
    enc = b64("rm -rf build")
    assert kind(f"eval \"$(printf '%s' '{enc}' | base64 -d)\"") == PLAIN
    assert kind(f'eval "$(echo {enc} | base64 --decode)"') == PLAIN
    assert kind(f'bash -c "$(echo -n {enc} | base64 -d)"') == PLAIN


def test_decode_piped_to_shell_stays_a_card():
    assert kind(f"echo {b64('rm -rf build')} | base64 -d | sh") is not None  # already worked; keep it


def test_eval_of_a_safe_decoded_command_stays_quiet():
    assert kind(f'eval "$(echo {b64("ls -la")} | base64 -d)"') is None


def test_decode_that_is_not_run_stays_quiet():
    assert kind(f"echo {b64('rm -rf build')} | base64 -d > notes.txt") is None


def test_garbage_and_binary_base64_stay_quiet():
    assert kind('eval "$(echo !!!!!!!! | base64 -d)"') is None
    assert kind('eval "$(echo QUJDREVGR0g= | base64 -d)"') is None  # ABCDEFGH
    assert kind('eval "$(echo AAECAwQFBgcICQ== | base64 -d)"') is None  # control bytes


def test_huge_base64_is_bounded():
    t = time.time()
    assert kind('eval "$(echo ' + "A" * 100000 + ' | base64 -d)"') is None
    assert time.time() - t < 2


def test_write_then_run_is_seen():
    assert kind("printf '%s' 'rm -rf build' > run.sh; sh run.sh") == PLAIN
    assert kind("echo 'rm -rf build' > /tmp/x.sh && bash /tmp/x.sh") == PLAIN
    assert kind("printf '%s' 'rm -rf build' > run.sh; ./run.sh") == PLAIN


def test_write_without_run_stays_quiet():
    assert kind("printf '%s' 'rm -rf build' > notes.sh") is None


def test_write_then_run_of_a_different_file_stays_quiet():
    assert kind("printf '%s' 'rm -rf build' > a.sh; sh b.sh") is None


def test_write_a_safe_script_then_run_stays_quiet():
    assert kind("printf '%s' 'ls -la' > run.sh; sh run.sh") is None


def test_many_writes_are_bounded():
    t = time.time()
    cmd = "; ".join(f"echo 'ls' > f{i}.sh" for i in range(500)) + "; sh f499.sh"
    assert kind(cmd) is None
    assert time.time() - t < 2
