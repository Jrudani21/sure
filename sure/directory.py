"""Permission directory: card wording and detection rules, read from directory/permissions.json.
Also the text and folder hygiene every writer of card text, ledger lines and events shares."""
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "directory" / "permissions.json"
TOUCHES = {"read", "write_files", "run_commands", "network", "external_tools", "delegate", "settings_or_mode", "protected_paths"}
TIERS = {"easy", "hard", "impossible"}
LASTS = {"once", "session", "forever"}
DECIDERS = {"you", "saved rule", "classifier", "sandbox"}
SCOPES = {"whole", "seg", "path", "tool"}
TEXT = ("id", "name", "what", "why", "cost", "undo")


def validate(entries):
    """Raise ValueError naming the bad entry id and field."""
    if not isinstance(entries, list):
        raise ValueError("directory must be a list")
    for e in entries:
        eid = e.get("id", "?") if isinstance(e, dict) else "?"
        if not isinstance(e, dict):
            raise ValueError(f"{eid}: entry must be an object")
        for k in TEXT:
            if not isinstance(e.get(k), str) or not e[k].strip():
                raise ValueError(f"{eid}: {k} must be non-empty text")
        for k, ok in (("touches", TOUCHES), ("tier", TIERS), ("lasts", LASTS)):
            if e.get(k) not in ok:
                raise ValueError(f"{eid}: bad {k} {e.get(k)!r}")
        d = e.get("decided_by")
        if not isinstance(d, list) or not d or not set(d) <= DECIDERS:
            raise ValueError(f"{eid}: bad decided_by {d!r}")
        if e.get("default", "ask") not in ("ask", "quiet"):
            raise ValueError(f"{eid}: bad default {e['default']!r}")
        at = e.get("ask_tokens")
        if at is not None and not (isinstance(at, list) and all(isinstance(t, str) and re.fullmatch(r"[a-z0-9]+", t) for t in at)):
            raise ValueError(f"{eid}: bad ask_tokens {at!r}")
        ap, aat = e.get("app_prefix"), e.get("app_ask_tokens")
        if (ap is None) != (aat is None) or (ap is not None and not (
                isinstance(ap, str) and ap and isinstance(aat, list)
                and all(isinstance(t, str) and re.fullmatch(r"[a-z0-9]+", t) for t in aat))):
            raise ValueError(f"{eid}: bad app_prefix/app_ask_tokens")
        ms = e.get("matchers")
        if not isinstance(ms, list):
            raise ValueError(f"{eid}: matchers must be a list")
        for m in ms:
            if not isinstance(m, dict):
                raise ValueError(f"{eid}: bad matcher {m!r}")
            if m.get("scope") not in SCOPES:
                raise ValueError(f"{eid}: bad matcher {m!r}")
            if not m.get("tool"):
                raise ValueError(f"{eid}: bad matcher {m!r}")
            if not isinstance(m.get("pattern"), str) or not m["pattern"]:
                raise ValueError(f"{eid}: bad matcher {m!r}")
            try:
                re.compile(m["pattern"], re.I)
            except re.error as err:
                raise ValueError(f"{eid}: matcher pattern does not compile: {err}")


def name_tokens(tool):
    """Lower-case words of an MCP tool name's last part: split on _ and -, camelCase and digits.
    mcp__db__dropTable -> ["drop", "table"]; mcp__x__runHTTPRequest -> ["run", "http", "request"]."""
    return [t.lower() for t in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", tool.rsplit("__", 1)[-1])]


def load(path=None):
    entries = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    validate(entries)
    return entries


def read_manifest(cwd):
    """<cwd>/sure.json as a dict; None when absent, unreadable, not JSON or not an object.
    utf-8-sig: a BOM (PowerShell 5.1's default) is tolerated; writers never add one."""
    try:
        d = json.loads((Path(cwd) / "sure.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):  # ValueError: bad JSON, bad UTF-8, or a NUL in cwd
        return None
    return d if isinstance(d, dict) else None


_USERINFO = re.compile(r"(?<=://)[^\s/?#]+@")  # greedy: up to the LAST @ before the host, so "p@ss@" goes too
_SECRET = re.compile(
    r"\b(?:(?:ghp_|gho_|github_pat_|sk-|glpat-)[\w-]{20,}|AKIA[0-9A-Z]{16}\b|xox[bap]-[\w-]{10,}"
    r"|AIza[\w-]{30,}|hf_\w{20,}|npm_\w{30,}|sk_live_\w{20,}|eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+)"
)
_BEARER = re.compile(r"\b((?:Bearer|Basic)\s+)[\w.~+/=-]{8,}", re.I)
_ASSIGN = re.compile(
    r"((?:pass(?:wd|word)?|token|secret|api[_-]?key|access[_-]?key|credential)s?\w*\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s&]+)",
    re.I,
)
_FLAG = re.compile(r"(--(?:password|token|secret)[= ])\S+|(\s-u\s*[^\s:]+:)\S+", re.I)


def _mask(s):
    s = _USERINFO.sub("***@", s)
    s = _FLAG.sub(lambda m: (m.group(1) or m.group(2)) + "***", _SECRET.sub("***", s))
    return _ASSIGN.sub(r"\1***", _BEARER.sub(r"\1***", s))


def clean(text, keep_newlines=False):
    """One line of honest text: whitespace controls become spaces (so "rm x" newline "ls" cannot
    glue into "rm xls"), every other Unicode category C character (control, format/bidi, zero-width, surrogate,
    private, unassigned) is dropped, runs of whitespace collapse, and URL passwords and obvious API
    tokens are masked. keep_newlines (the card's command only, shown in a <pre>): "\\n" and "\\r\\n" stay
    line breaks so a second command on its own line cannot hide; every other whitespace is a space."""
    s = str(text)
    if keep_newlines:
        s = s.replace("\r\n", "\n")
    s = "".join(ch if keep_newlines and ch == "\n" else " " if ch.isspace() else ch for ch in s
                if ch.isspace() or not unicodedata.category(ch).startswith("C"))
    if keep_newlines:
        s = "\n".join(" ".join(line.split()) for line in s.split("\n")).strip("\n")
    else:
        s = " ".join(s.split())  # also turns U+2028/U+2029/NEL (whitespace in Python) into plain spaces
    return _mask(s)


def cap(text, n):
    """Cut to n characters, saying how much was left out."""
    return text if len(text) <= n else f"{text[:n]}…(+{len(text) - n} chars)"


def sure_dir(cwd):
    """<cwd>/.sure; when this call creates it, it also gets a .gitignore of `*` so the ledger stays out of git."""
    d = Path(cwd) / ".sure"
    try:
        d.mkdir()
    except FileExistsError:
        return d
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    return d


# ---- custom rules: users may add or tighten, never loosen; they load only after `sure.py test` verified them ----
USER_PATH = Path.home() / ".sure" / "rules.json"
RANK = {"easy": 0, "hard": 1, "impossible": 2}
PROBES = ("a" * 4000 + "!", "a " * 2000 + "!", "/" * 4000 + "!", "x=" * 2000 + "!")
_TIME_PROBE = (
    "import re,sys,time\nrx=re.compile(sys.argv[1],re.I)\nt=time.perf_counter()\n"
    "for s in sys.argv[2:]: rx.search(s)\nprint(time.perf_counter()-t)"
)


def user_path(path=None):
    return Path(path or os.environ.get("SURE_RULES") or USER_PATH)


def _marker(p):
    return p.with_name(p.name + ".ok")


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def mark_verified(path=None):
    p = user_path(path)
    _marker(p).write_text(digest(p), encoding="utf-8")


def _check_user(rule, by_id):
    if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
        raise ValueError("every rule needs an id")
    base = by_id.get(rule["id"])
    if base is not None:  # extending a built-in: only matchers (and a higher tier) are read
        if rule.get("tier") is not None and rule["tier"] not in TIERS:
            raise ValueError(f"{rule['id']}: bad tier {rule['tier']!r}")
        validate([{**base, "matchers": rule.get("matchers", [])}])
    else:
        validate([rule])


def read_user(path=None, need_ok=True):
    """(rules, tests, error). A missing file is ([], [], None). Any problem returns no rules and a message:
    the built-in rules stay in force, so a broken custom file never weakens anything."""
    p = user_path(path)
    if not p.exists():
        return [], [], None
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(d, dict):
            raise ValueError("the file must be an object with rules and tests")
        rules, tests = d.get("rules", []), d.get("tests", [])
        if not isinstance(rules, list) or not isinstance(tests, list):
            raise ValueError("rules and tests must be lists")
        by_id = {e["id"]: e for e in load()}
        for r in rules:
            _check_user(r, by_id)
        for t in tests:
            if not (isinstance(t, dict) and isinstance(t.get("cmd"), str) and isinstance(t.get("expect"), str)):
                raise ValueError("every test needs cmd and expect")
    except (OSError, ValueError) as e:
        return [], [], f"custom rules {p.name}: {e}"
    trusted = os.environ.get("SURE_RULES_TRUST") == "1" and "_selftest" in sys.argv  # only `sure.py test`'s own child
    if need_ok and not trusted:
        try:
            ok = _marker(p).read_text(encoding="utf-8").strip() == digest(p)
        except OSError:
            ok = False
        if not ok:
            return [], tests, f"custom rules {p.name} not verified yet: run `sure.py test`"
    return rules, tests, None


def merge(builtin, user):
    """Built-ins first and untouched in order. A user rule with a built-in id adds matchers and may raise the tier;
    a new id is appended and always asks. Nothing here can lower a tier or make a kind quiet."""
    out = [dict(e, matchers=list(e["matchers"])) for e in builtin]
    by = {e["id"]: e for e in out}
    for r in user:
        e = by.get(r["id"])
        if e is not None:
            e["matchers"] += r.get("matchers", [])
            if RANK.get(r.get("tier"), -1) > RANK[e["tier"]]:
                e["tier"] = r["tier"]
        else:
            n = dict(r, matchers=list(r["matchers"]))
            n.pop("default", None)
            out.append(n)
            by[n["id"]] = n
    return out


def load_all():
    """(entries, error): built-ins plus verified custom rules."""
    base = load()
    rules, _tests, err = read_user()
    try:
        return merge(base, rules), err
    except Exception as e:  # never let a custom file take the hook down
        return base, f"custom rules: {e}"


def slow_pattern(rules):
    """The first user pattern taking over 50 ms (or hanging) on adversarial text, else None.
    Each runs in a child process because Python cannot interrupt a regex."""
    for r in rules:
        for m in r.get("matchers", []):
            try:
                out = subprocess.run([sys.executable, "-c", _TIME_PROBE, m["pattern"], *PROBES],
                                     capture_output=True, text=True, timeout=3)
                if float(out.stdout.strip() or 9) > 0.05:
                    return m["pattern"]
            except subprocess.TimeoutExpired:
                return m["pattern"]
    return None
