# Copyright (c) 2026 Janak Rudani
# SPDX-License-Identifier: MIT
# Provenance: Jrudani21/sure/tests/conftest.py 2ba88e1169e31cfb
"""Every test runs from an empty temp folder, so a hook started without a cwd (subprocesses
inherit ours) writes its .sure/ ledger there and never into the checkout."""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _outside_the_checkout(tmp_path_factory, monkeypatch, request):
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    had = (REPO / ".sure").exists()
    yield
    assert had or not (REPO / ".sure").exists(), f"{request.node.nodeid} wrote .sure/ into the repo root"
