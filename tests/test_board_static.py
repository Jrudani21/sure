"""The board page: served without the token, CSP-locked, no external URLs, no HTML-injection sinks."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sure"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import board  # noqa: E402
from test_board import call  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "sure" / "web"
FILES = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@pytest.fixture
def b(tmp_path):
    bd = board.Board(runtime=tmp_path / "board.json")
    bd.start()
    yield bd
    bd.stop()


def text(name):
    return (WEB / name).read_text(encoding="utf-8")


def test_index_served_without_token(b):
    st, raw, r = call(b, "GET", "/", token=False)
    assert st == 200 and r.getheader("Content-Type").startswith("text/html")
    html = raw.decode("utf-8")
    assert "/static/app.js" in html and "/static/style.css" in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline script block"


def test_static_files_exist_and_nonempty(b):
    for name, ctype in FILES.items():
        assert (WEB / name).stat().st_size > 0, name
        st, raw, r = call(b, "GET", f"/static/{name}", token=False)
        assert st == 200 and raw, name
        assert r.getheader("Content-Type").startswith(ctype), name


def test_no_external_urls_in_web_files():
    for name in FILES:
        left = text(name).replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in left and "https://" not in left, name


def test_no_html_injection_sinks():
    for name in FILES:
        src = text(name)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert bad not in src, (name, bad)
        assert not re.search(r"\son[a-z]+\s*=", src), (name, "inline handler")
    assert 'style="' not in text("index.html")


def test_csp_header_on_every_response(b):
    for path in ("/", "/static/app.js", "/nope"):
        st, _, r = call(b, "GET", path, token=False)
        assert r.getheader("Content-Security-Policy") == CSP, path
        assert r.getheader("X-Content-Type-Options") == "nosniff", path
    assert call(b, "GET", "/nope", token=False)[0] == 404


def test_token_not_in_served_html(b):
    html = call(b, "GET", "/", token=False)[1].decode("utf-8")
    assert b.page_token not in html and b.hook_token not in html
    assert not re.search(r"[A-Za-z0-9_-]{32,}", html)


def test_no_indirect_sinks_in_app_js():
    src = text("app.js")
    for pat in (r"setAttribute\(\s*[\"'](on|href|src|style)", r"cssText", r"location\s*=", r"location\.href\s*=",
                r"\[\s*[\"']inner", r"\[\s*[\"']outer"):
        assert not re.search(pat, src), pat


def test_fetch_never_puts_token_in_url():
    src = text("app.js")
    for call_args in re.findall(r"fetch\(([^)]*)\)", src):
        assert "token" not in call_args.lower(), call_args
    assert not re.search(r"[?&]token=", src)


def test_tabs_follow_aria_tabs_pattern():
    html = text("index.html")
    assert 'role="tablist"' in html and "aria-pressed" not in html
    for tab, panel in (("tab-live", "panel-live"), ("tab-replay", "panel-replay")):
        assert re.search(rf'<button[^>]*id="{tab}"[^>]*role="tab"[^>]*aria-selected="(true|false)"'
                         rf'[^>]*aria-controls="{panel}"', html), tab
        assert re.search(rf'id="{panel}"[^>]*role="tabpanel"[^>]*aria-labelledby="{tab}"', html), panel
    assert not re.search(r'id="tab-replay"[^>]*disabled', html)
    js = text("app.js")
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert key in js, key


def test_every_id_app_js_looks_up_exists():
    ids = set(re.findall(r'\bid="([^"]+)"', text("index.html")))
    used = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"', text("app.js")))
    for need in ("flow-map", "trusted-strip", "replay-cwd", "replay-days", "replay-load", "replay-report",
                 "replay-flow", "replay-table", "replay-error", "replay-note"):
        assert need in used, need
    assert used <= ids, used - ids


def test_days_picker_offers_exact_ranges():
    html = text("index.html")
    sel = re.search(r'<select[^>]*id="replay-days".*?</select>', html, re.S).group(0)
    assert re.findall(r'value="(\d+)"', sel) == ["1", "7", "30", "90", "365"]


def test_fetch_has_timeout():
    js = text("app.js")
    assert "AbortController" in js and "8000" in js
