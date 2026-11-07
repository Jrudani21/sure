# Copyright (c) 2026 Janak Rudani
# SPDX-License-Identifier: MIT
# Provenance: Jrudani21/sure/sure/trust.py 05ac8d4644a7f563
"""Per-project always-allowed kinds, stored as `auto_allow` in <project>/sure.json (easy-tier kinds only)."""
import json
from pathlib import Path

import directory


def _read(cwd):
    """Parsed sure.json: dict, None if absent, False if unreadable/invalid/not an object."""
    if not (Path(cwd) / "sure.json").exists():
        return None
    d = directory.read_manifest(cwd)
    return False if d is None else d


def _write(cwd, data):
    directory.atomic_write(Path(cwd) / "sure.json", json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def trusted(cwd):
    d = _read(cwd)
    a = d.get("auto_allow") if d else None
    return [k for k in a if isinstance(k, str)] if isinstance(a, list) else []


def add(cwd, kind):
    if not any(e["id"] == kind and e["tier"] == "easy" for e in directory.load()):
        return False
    d = _read(cwd)
    if d is False:
        return False
    d = d or {}
    cur = trusted(cwd)
    if kind not in cur:
        d["auto_allow"] = cur + [kind]
        _write(cwd, d)
    return True


def remove(cwd, kind):
    d = _read(cwd)
    if not d or kind not in trusted(cwd):
        return
    d["auto_allow"] = [k for k in trusted(cwd) if k != kind]
    _write(cwd, d)
