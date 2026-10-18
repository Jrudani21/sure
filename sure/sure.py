"""Sure? - Claude Code hook that explains risky agent actions in plain language."""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# kind -> (tier, what, why it matters, cost, undo)
CARDS = {
    "download_and_run": (
        "impossible",
        "Downloads a file and then runs it in the same command: {target}",
        "You never get to read what was downloaded before it runs.",
        "Anything on this computer, including files and passwords, could be read or changed.",
        "there is no way to un-run it; you would have to check the damage by hand.",
    ),
    "pipe_to_shell": (
        "impossible",
        "Downloads code from the internet and runs it immediately: {target}",
        "You never see what the downloaded code does before it runs.",
        "Anything on this computer, including files and passwords, could be read or changed.",
        "there is no way to un-run it; you would have to check the damage by hand.",
    ),
    "database": (
        "impossible",
        "Changes or erases database data or its structure: {target}",
        "Databases hold the real information your app depends on.",
        "Data can be lost for good, or the app can stop working with the old data.",
        "only if you have a backup of the database from before.",
    ),
    "delete": (
        "hard",
        "Permanently deletes files or discards your changes: {target}",
        "Deleted files skip the recycle bin, and discarded changes are not saved anywhere.",
        "Your work or project files could disappear.",
        "only if the files were saved in git or a backup beforehand.",
    ),
    "push": (
        "hard",
        "Sends your code to a shared online copy: {target}",
        "Other people or services may see or build on it straight away.",
        "A mistake becomes public, and a forced push can overwrite other people's work.",
        "possible, but it needs a second correcting push and others may already have your version.",
    ),
    "install": (
        "easy",
        "Adds someone else's code to your project: {target}",
        "That code runs with the same access your project has, and is only as trustworthy as its author.",
        "Your project grows, and a bad or abandoned package can bring bugs or security holes.",
        "uninstall the package; this usually restores things.",
    ),
    "config": (
        "easy",
        "Edits a file that controls logins, secrets, dependencies or the database shape: {target}",
        "A small mistake here can lock you out, leak a secret, or break the whole app.",
        "The app may stop starting or behave in unexpected ways.",
        "put the old version of the file back from git or an editor's history.",
    ),
    "script_launcher": (
        "hard",
        "Runs a script whose real actions are hidden inside it: {target}",
        "The command name looks harmless but the script can do anything, including delete or send files.",
        "You are trusting the script's contents without seeing them.",
        "depends on what the script does; open the script first to find out.",
    ),
}

# Order matters: first kind that matches wins, most dangerous first.
# "whole" rules search the (capped) full command; "seg" rules are anchored to the
# start of each command segment (split on && || ; | newline) so words that merely
# appear in arguments or strings do not trigger a card.
# ponytail: regex heuristics, real shell parsing if false positives/negatives hurt.
_P = r"^(?:\S*[\\/])?"  # optional path prefix, e.g. /bin/rm
_END = r"(?=\s|$)"
_RULES = [
    ("pipe_to_shell", "whole", r"\b(curl|wget|irm|iwr|Invoke-WebRequest)\b.{0,500}?\|\s*(sudo\s+)?(sh|bash|zsh|iex|python3?)(\s|$)"),
    ("pipe_to_shell", "whole", r"\b(sh|bash|zsh)\b[^\n]{0,20}[<$]\(\s*(curl|wget)"),
    ("pipe_to_shell", "whole", r"\biex\b\s*\(?\s*(iwr|irm|Invoke-WebRequest)"),
    ("database", "whole", r"\bDROP\s+(TABLE|DATABASE)\b|\bTRUNCATE\s+TABLE\b|\bDELETE\s+FROM\b"),
    ("database", "seg", r"^(npx\s+)?prisma\s+(migrate|db\s+push)|^alembic" + _END + r"|manage\.py\s+migrate|db:(migrate|drop|reset)"),
    ("delete", "seg", _P + r"(rm|del|erase|rd|rmdir|ri|unlink|shred|Remove-Item)" + _END),
    ("delete", "seg", r"^find\b.*\s-delete" + _END),
    ("delete", "seg", r"^git\s+(clean|reset\s+--hard|restore|stash\s+drop|checkout\s+--|branch\s+(?-i:-D))" + _END),
    ("push", "seg", r"^git\s+push" + _END),
    ("install", "seg", _P + r"(npm|pnpm|yarn|pip3?|cargo|winget|choco|gem|brew|apt|apt-get|go|uv)\s+(pip\s+)?(install|add|i|ci)" + _END),
    ("script_launcher", "seg", r"^(npm|pnpm|yarn)\s+run" + _END + r"|" + _P + r"make" + _END + r"|^python3?\s+setup\.py|^\./\S+\.sh|^bash\s+\S+\.sh"),
]
RULES = [(k, scope, re.compile(p, re.I)) for k, scope, p in _RULES]
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?:\n|$)", re.S)
_DASH_C = re.compile(r"""^(?:\S*[\\/])?(?:bash|sh|zsh)\s+-\w*c\s+(['"])(.*)\1\s*$""", re.S)
_DL = re.compile(r"^(?:curl|wget)\b.*?(?:\s-[A-Za-z]*[oO]\s*|--output(?:-document)?[ =]|\s>\s*)(\S+)", re.I)
_INTERP = {"bash", "sh", "zsh", "python", "python3", "node", "source", "."}
_WIDE = re.compile(r"(?:^|\s)(?:/|~|~/|\*|\.|\.\.|\$HOME|[A-Za-z]:\\?)(?=\s|$)")
_PREFIX = re.compile(r"^\s*((sudo|env)\s+(-\S+\s+)*|\w+=\S*\s+)*", re.I)
MAX_CMD = 4000  # ponytail: only the first 4000 chars are inspected, bounds regex time

CONFIG_NAME = re.compile(
    r"(package\.json|requirements\.txt|pyproject\.toml|dockerfile(\..+)?|schema\.\w+"
    r"|\.env(\.(?!example$).+)?|.*(?<![a-z])auth(?![a-z]).*|.*middleware.*|.*migration.*)$",
    re.I,
)
MIGRATIONS_DIR = re.compile(r"(^|/)migrations?/", re.I)


def _is_config(path):
    p = path.replace("\\", "/")
    return bool(CONFIG_NAME.match(p.rsplit("/", 1)[-1]) or MIGRATIONS_DIR.search(p))


def _split(cmd):
    """Split on && || ; | newline, but not inside quotes."""
    segs, cur, q, i = [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and q != "'" and i + 1 < len(cmd):
            cur.append(cmd[i : i + 2])
            i += 2
        elif q:
            q = None if c == q else q
            cur.append(c)
            i += 1
        elif c in "'\"":
            q = c
            cur.append(c)
            i += 1
        elif c in ";|\n" or cmd.startswith("&&", i):
            segs.append("".join(cur))
            cur = []
            i += 2 if cmd.startswith(("&&", "||"), i) else 1
        else:
            cur.append(c)
            i += 1
    segs.append("".join(cur))
    return segs


def _downloads_then_runs(segs):
    files = set()
    for s in segs:
        m = _DL.match(s)
        if m:
            files.add(m.group(1).strip("'\"").replace("\\", "/").rsplit("/", 1)[-1])
            continue
        t = s.split()
        ran_via_interp = bool(t) and t[0] in _INTERP
        t = t[1:] if ran_via_interp else t
        if files and t:
            tok = t[0].strip("'\"").replace("\\", "/")
            if tok.rsplit("/", 1)[-1] in files and (ran_via_interp or tok.startswith(("./", "/", "~"))):
                return True
    return False


def _risk(kind, target):
    tier, what, why, cost, undo = CARDS[kind]
    if kind == "delete" and _WIDE.search(target):
        cost = "Targets your whole home, drive or project folder. " + cost
    target = " ".join(target.split())[:80]
    return {
        "kind": kind,
        "tier": tier,
        "what": what.format(target=target),
        "why": why,
        "cost": cost,
        "undo": f"{tier.capitalize()}: {undo}",
    }


def _bash_kind(cmd, depth=0):
    cmd = _HEREDOC.sub("\n", cmd)
    segs = [_PREFIX.sub("", s).strip() for s in _split(cmd)]
    if _downloads_then_runs(segs):  # checked first: more specific than the script launcher rule
        return "download_and_run"
    for kind, scope, rx in RULES:
        if any(rx.search(t) for t in ([cmd] if scope == "whole" else segs)):
            return kind
    if depth < 3:  # bash -c "..." payloads are inspected too
        for s in segs:
            m = _DASH_C.match(s)
            if m and (k := _bash_kind(m.group(2), depth + 1)):
                return k
    return None


def classify(tool_name, tool_input):
    """Return a risk dict for a risky tool call, else None."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")[:MAX_CMD]
        kind = _bash_kind(cmd)
        if kind:
            return _risk(kind, cmd)
    elif tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("notebook_path" if tool_name == "NotebookEdit" else "file_path", "")
        if _is_config(path):
            return _risk("config", path)
    return None


def render_card(risk):
    return "\n".join(
        [
            f"WHAT: {risk['what']}",
            f"WHY IT MATTERS: {risk['why']}",
            f"COST: {risk['cost']}",
            f"UNDO: {risk['undo']}",
        ]
    )


def log(cwd, line):
    try:
        d = Path(cwd) / ".sure"
        d.mkdir(exist_ok=True)
        with open(d / "ledger.md", "a", encoding="utf-8") as f:
            f.write(f"- {datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def load_manifest(cwd):
    try:
        m = json.loads((Path(cwd) / "sure.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)


def save_point(cwd):
    """Snapshot tracked changes under refs/sure/<n>; None if not possible."""
    # ponytail: stash-create skips untracked files and undo won't delete new ones;
    # upgrade: snapshot through a temporary index with `add -A`.
    try:
        sha = _git(cwd, "stash", "create").stdout.strip()
        if not sha:
            return None
        names = _git(cwd, "for-each-ref", "--format=%(refname)", "refs/sure").stdout.split()
        ref = f"refs/sure/{max((int(r.rsplit('/', 1)[1]) for r in names), default=0) + 1}"
        _git(cwd, "update-ref", ref, sha)
        return ref
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def undo(cwd, n):
    try:
        return _git(cwd, "checkout", f"refs/sure/{n}", "--", ".").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def stats(cwd):
    """Approval rate per risk kind, read from the ledger."""
    counts = {}
    try:
        text = (Path(cwd) / ".sure" / "ledger.md").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for verb, tag in re.findall(r"^- \S+ (asked|ran) \[([a-z_]+/[a-z]+)\]", text, re.M):
        counts.setdefault(tag, {"asked": 0, "ran": 0})[verb] += 1
    rows = ["KIND/TIER  ASKED  RAN  APPROVED%"]
    for tag, c in sorted(counts.items()):
        if c["asked"]:
            rows.append(f"{tag}  {c['asked']}  {c['ran']}  {round(100 * c['ran'] / c['asked'])}%")
    return "\n".join(rows)


def report(cwd, days=7, now=None):
    """Plain-language summary of recent decisions, with a nudge when approvals look automatic."""
    now = now or datetime.now()
    cutoff = now - timedelta(days=days)
    try:
        text = (Path(cwd) / ".sure" / "ledger.md").read_text(encoding="utf-8")
    except OSError:
        text = ""
    kinds = {}
    for ts, verb, kind, tier in re.findall(r"^- (\S+) (asked|ran|auto) \[([a-z_]+)/([a-z]+)\]", text, re.M):
        try:
            if datetime.fromisoformat(ts) < cutoff:
                continue
        except ValueError:
            continue
        c = kinds.setdefault(kind, {"tier": tier, "asked": 0, "ran": 0, "auto": 0})
        c[verb] += 1
    if not kinds:
        return f"Nothing logged in the last {days} days."
    rows, nudges = [f"Last {days} days:"], []
    for kind, c in sorted(kinds.items()):
        ok = min(c["ran"], c["asked"])
        rows.append(f"- {kind} ({c['tier']}): asked {c['asked']}, you approved {ok} of {c['asked']}, auto-allowed {c['auto']}")
        if c["tier"] != "easy" and c["asked"] >= 5 and ok / c["asked"] >= 0.9:
            nudges.append(f"Heads up: you approved {ok} of {c['asked']} {kind} prompts. Make sure you are actually reading the cards.")
    return "\n".join(rows + nudges)


_EVENTS = {"PreToolUse": "pre", "PostToolUse": "post", "PostToolUseFailure": "failed"}
_MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit"


def _strip_ours(data):
    hooks = data.get("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [e for e in hooks[ev] if "sure.py" not in json.dumps(e)]
        if not hooks[ev]:
            del hooks[ev]
    if "hooks" in data and not hooks:
        del data["hooks"]


def _write_settings(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".sure.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _load_settings(p):
    if not p.exists():
        return {}
    bak = p.with_name(p.name + ".sure.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
    return json.loads(p.read_text(encoding="utf-8"))


def install(path=None):
    """Add Sure? hooks to a Claude Code settings.json, keeping everything else."""
    p = Path(path) if path else Path.home() / ".claude" / "settings.json"
    data = _load_settings(p)
    _strip_ours(data)
    script = Path(__file__).resolve().as_posix()
    for ev, arg in _EVENTS.items():
        entry = {"matcher": _MATCHER, "hooks": [{"type": "command", "command": f'python "{script}" {arg}', "timeout": 10}]}
        data.setdefault("hooks", {}).setdefault(ev, []).append(entry)
    _write_settings(p, data)


def uninstall(path=None):
    p = Path(path) if path else Path.home() / ".claude" / "settings.json"
    data = _load_settings(p)
    _strip_ours(data)
    _write_settings(p, data)


def main(argv, stdin_text, cwd):
    """Hook entry. Returns stdout text; never raises."""
    try:
        data = json.loads(stdin_text)
        if isinstance(data.get("cwd"), str) and data["cwd"].strip():
            cwd = data["cwd"]
        risk = classify(data["tool_name"], data["tool_input"])
        if not risk:
            return ""
        tag = f"[{risk['kind']}/{risk['tier']}]"
        if argv[0] in ("post", "failed"):
            log(cwd, f"{'ran' if argv[0] == 'post' else 'failed'} {tag}")
            return ""
        auto = load_manifest(cwd).get("auto_allow")
        if risk["tier"] == "easy" and isinstance(auto, list) and risk["kind"] in auto:
            log(cwd, f"auto {tag}")  # only easy-undo kinds can ever be auto-allowed
            return ""
        log(cwd, f"asked {tag} {risk['what']}")
        if load_manifest(cwd).get("save_points", {}).get("enabled"):
            ref = save_point(cwd)
            if ref:
                log(cwd, f"save point {ref}")
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": render_card(risk),
                }
            }
        )
    except Exception as e:
        log(cwd, " ".join(f"error {type(e).__name__}: {e}".split())[:120])
        return ""


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["stats"]:
            print(stats(str(Path.cwd())))
            sys.exit(0)
        if sys.argv[1:2] == ["report"]:
            print(report(str(Path.cwd()), int(sys.argv[2]) if len(sys.argv) > 2 else 7))
            sys.exit(0)
        if sys.argv[1:2] in (["install"], ["uninstall"]):
            (install if sys.argv[1] == "install" else uninstall)(*sys.argv[2:3])
            print("done")
            sys.exit(0)
        if sys.argv[1:2] == ["undo"]:
            print("restored" if undo(str(Path.cwd()), int(sys.argv[2])) else "failed")
            sys.exit(0)
        sys.stdout.write(main(sys.argv[1:], sys.stdin.buffer.read().decode("utf-8", "replace"), str(Path.cwd())))
    except Exception:
        pass
    sys.exit(0)
