"""infra_change card: the new patterns stay fast on hostile input and stay quiet on look-alikes."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import sure  # noqa: E402

_HEADS = [
    ("kubectl", "-n x "),
    ("kubectl", "--context=a "),
    ("helm", "--namespace x "),
    ("aws", "--profile x "),
    ("aws", "--region=y "),
    ("gcloud", "--project=p "),
    ("gcloud", "compute "),
    ("az", "vm "),
    ("docker compose", "-f x "),
    ("docker compose down", "x "),
    ("terraform", "-chdir=x "),
    ("terraform", "-var=a "),
    ("pulumi", "--stack s "),
    ("eksctl", "-v "),
    ("gsutil", "-m "),
    ("docker-compose", "-f x "),
    ("docker.exe", "-H h "),
    ("cat", "a "),
    ("grep", "-i x "),
    ("cat ./", ".env "),
    ("scp", "-P 22 "),
    ("curl", "-F f=@ "),
    ("echo", "$A_B "),
    ("echo $", "TOKEN"),
    ("aws", "--region x "),
    ("vault", "kv "),
    ("kubectl get secret", " x -n y "),
    ("printenv", "x "),
]
_TAILS = ["", "x", "!", "describe", "get"]


@pytest.mark.parametrize("head,unit", _HEADS)
@pytest.mark.parametrize("tail", _TAILS)
def test_infra_patterns_never_backtrack(head, unit, tail):
    for cmd in (head + " " + unit * 600 + tail, "bash -c '" + head + " " + unit * 300 + tail):
        t = time.perf_counter()
        sure.classify("Bash", {"command": cmd[:4000]})
        assert time.perf_counter() - t < 0.05, cmd[:40]


@pytest.mark.parametrize("cmd", [
    "terraform plan",
    "kubectl get pods -A",
    "helm list -A",
    "aws s3 ls",
    "gcloud compute instances list",
    "az group list",
    "docker ps -a",
    'echo "terraform destroy"',
])
def test_read_only_infra_commands_stay_silent(cmd):
    r = sure.classify("Bash", {"command": cmd})
    assert r is None or r["kind"] != "infra_change"


def test_infra_card_is_impossible_tier_and_plain():
    r = sure.classify("Bash", {"command": "terraform destroy -auto-approve"})
    assert r["kind"] == "infra_change" and r["tier"] == "impossible"


@pytest.mark.parametrize("cmd", [
    "curl " + "@" * 3995,
    "curl " + "@." * 1997,
    "curl " + "-T " * 990,
    "grep " + ".env " * 700,
    "docker exec " + "x " * 1900 + "env",
])
def test_secret_patterns_linear_on_long_input(cmd):
    t = time.perf_counter()
    sure.classify("Bash", {"command": cmd[:4000]})
    assert time.perf_counter() - t < 0.05


@pytest.mark.parametrize("cmd", [
    "ssh " + "-R " * 1300,
    "ssh " + "-vv " * 900,
    "curl " + "-d " * 1300,
    "curl " + "-F x " * 800 + "transfer.sh",
    "nc " + "-e " * 1300,
    "python " + "import socket " * 280,
    "aws " + "--acl " * 650,
    "gsutil iam ch " * 280,
    "ngrok " + "x " * 1900,
    "echo " + "/dev/tcp/ " * 360,
])
def test_exposure_patterns_linear_on_long_input(cmd):
    t = time.perf_counter()
    sure.classify("Bash", {"command": cmd[:4000]})
    assert time.perf_counter() - t < 0.05


@pytest.mark.parametrize("cmd", [
    "git " + "-c a=b " * 400 + "commit --no-verify",
    "git commit " + "-m x " * 800,
    "git " + "x " * 1900 + "reflog expire",
    "pytest " + "--cov " * 600,
    "npx jest " + "-u " * 1300,
    "cargo " + "x " * 1900,
    "sed " + "-i " * 1300,
    "perl " + "-pi " * 900 + ".skip(",
    "ssh " + "-o StrictHostKeyChecking " * 160,
    "powershell " + "-ep x " * 650,
    "Set-ExecutionPolicy " + "x " * 1900,
    "journalctl " + "--since x " * 400,
    "HISTFILE " * 480,
    "git " + "rm " * 1330,
    "sudo git " + "rm " * 1300,
    "ssh-keyscan " + ">" * 3980,
    "git " + "commit " * 570,
    "git " + "am " * 1330,
    "git " + "gc " * 1330,
    "git commit " + "\"x y\" " * 600,
])
def test_bypass_and_tamper_patterns_linear_on_long_input(cmd):
    t = time.perf_counter()
    sure.classify("Bash", {"command": cmd[:4000]})
    assert time.perf_counter() - t < 0.05
