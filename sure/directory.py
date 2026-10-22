"""Permission directory: card wording and detection rules, read from directory/permissions.json.
Also the text and folder hygiene every writer of card text, ledger lines and events shares."""
import json
import re
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
