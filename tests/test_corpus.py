import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
import sure  # noqa: E402

ITEMS = json.loads((Path(__file__).parent / "data" / "classifier_corpus.json").read_text(encoding="utf-8"))
# Labels a later ruling overrides; the corpus file itself stays as generated.
# Ruling A6 (final fixes): the highest tier wins, so `make` (script_launcher, hard) beats install (easy).
SUPERSEDED = {"make && sudo npm install -g pkg": "script_launcher"}


def test_corpus_matches_labels():
    bad = []
    for it in ITEMS:
        if "skip" in it:
            continue
        r = sure.classify("Bash", {"command": it["cmd"]})
        got = r["kind"] if r else "none"
        if got != SUPERSEDED.get(it["cmd"], it["expect"]):
            bad.append(f"{it['cmd']!r}: expected {it['expect']}, got {got}")
    assert not bad, "\n".join(bad)


def test_corpus_has_skip_reasons():
    for it in ITEMS:
        if "skip" in it:
            assert isinstance(it["skip"], str) and it["skip"].strip(), it["cmd"]
