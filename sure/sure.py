"""Sure? - Claude Code hook that explains risky agent actions in plain language."""
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

try:
    import events
except Exception:  # sidecar is optional: never let it take the hook down
    events = None

try:
    import directory

    _ENTRIES = directory.load()
    RULES = [
        (e["id"], m["scope"], re.compile(m["pattern"], re.I))
        for e in _ENTRIES
        for m in e["matchers"]
        if m["tool"] == "Bash"
    ]
    _PATHS = [
        (e["id"], re.compile(m["tool"]), re.compile(m["pattern"], re.I))
        for e in _ENTRIES
        for m in e["matchers"]
        if m["scope"] == "path"
    ]
    _TOOLS = [
        (e["id"], re.compile(m["tool"]), re.compile(m["pattern"], re.I))
        for e in _ENTRIES
        for m in e["matchers"]
        if m["scope"] == "tool"
    ]
except Exception as _e:  # broken or missing directory: stay silent on stdout rather than crash the hook
    try:
        sys.stderr.write(f"sure: permission directory unusable ({type(_e).__name__}), hook disabled\n")
    except Exception:  # stderr closed or None (pythonw): still never crash the hook
        pass
    _ENTRIES, RULES, _PATHS, _TOOLS = [], [], [], []

# kind -> (tier, what, why it matters, cost, undo)
CARDS = {e["id"]: (e["tier"], e["what"], e["why"], e["cost"], e["undo"]) for e in _ENTRIES}
_BY_ID = {e["id"]: e for e in _ENTRIES}

# Every matching kind is collected; the highest tier wins, ties go to directory order.
# "whole" rules search the (capped) full command; "seg" rules are anchored to the
# start of each command segment (split on && || ; | & newline, after leading words like
# sudo/time/xargs/then are stripped) so words that merely appear in arguments or strings
# do not trigger a card.
# ponytail: regex heuristics, real shell parsing if false positives/negatives hurt.
# ponytail: patterns live in directory/permissions.json; download_and_run has no matchers (sequence check below).
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?:\n|$)", re.S)
_FEEDS = {"bash", "sh", "zsh", "pwsh", "powershell", "python", "python3", "node",
          "psql", "mysql", "sqlite3", "mongo", "mongosh"}  # a heredoc fed to these is code: read its body
_DASH_C = re.compile(
    r"""^(?:\S*[\\/])?(?:bash|sh|zsh|powershell(?:\.exe)?|pwsh)(?:\s+-(?:NoProfile|NonInteractive|ExecutionPolicy\s+\w+))*\s+-(?:\w*c|command)\s+(?=['"])""",
    re.I,
)
_EVAL = re.compile(r"^eval\s+", re.I)
_QARG = re.compile(r""""(?:[^"\\]|\\.)*"?|'[^']*'?""", re.S)  # one quoted argument; a missing closing quote runs to the end
MAX_PAYLOADS = 8  # ponytail: only the first 8 quoted arguments after -c are read, bounds the recursion
_SHELL_IN = {"sh", "bash", "zsh", "iex", "invoke-expression", "python", "python3", "pwsh", "powershell", "node"}
_WORD_Q = re.compile(r"""\\(?=[A-Za-z])|(['"])([^\s'"\\]+)\1(?=\s|$)""")  # \rm or 'rm' as the command word
_DL = re.compile(r"^(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b", re.I)
_OUT = re.compile(r"\s-OutFile\s+(\S+)|\s-[oO]\s*(\S+)|\s-[A-Za-z]*[oO]\s+(\S+)|--output(?:-document)?[ =](\S+)|\s>\s*(\S+)", re.I)
_URL = re.compile(r"\S+://[^\s'\"]+")
_INTERP = {"bash", "sh", "zsh", "python", "python3", "node", "source", ".", "powershell", "pwsh"}
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_PRINT = re.compile(r"^(?:echo|printf|cat\s+<<)\b", re.I)
_WIDE = re.compile(r"(?:^|\s)(?:/|~|~/|\*|\.|\.\.|\$HOME|[A-Za-z]:\\?)(?=\s|$)")
# one leading word that is not the real command; _lead strips these repeatedly (bounded)
_LEAD = re.compile(
    r"""(?:[({][\s({]*
    |&\s+
    |(?:then|do|else|elif|time|nohup|exec)\s+
    |command(?:\s+-p)?\s+
    |timeout(?:\s+(?:-[sk]\s*\S+|--[\w-]+(?:=\S+)?|-(?![sk])\w+))*\s+\d+(?:\.\d+)?[smhd]?\s+
    |nice(?:\s+-n\s*-?\d+|\s+-\d+)?\s+
    |sudo(?:\s+(?:-[ugCDhpRTU]\s*\S+|--[\w-]+(?:=\S+)?|-(?![ugCDhpRTU])\w+))*\s+
    |env(?:\s+-\S+)*\s+
    |(?:\w+=\S*\s+)+
    |xargs(?:\s+(?:-[0prtx]+|-[IiLlnPsdaE]\s*\S+|--[\w-]+(?:=\S+)?))*\s+
    )""",
    re.X | re.I,
)
# Self-protection (best-effort, a cooperative agent's slip, not a sandbox): names are matched after any non-name
# character, so `>sure.json` and `of=sure.json` count. `mv x s*e.json`, variable-built names and quoted fragments evade it.
_SURE_CTL = re.compile(r"""(?<![\w-])\.sure[\\/]|(?<![\w.-])sure\.json\b|\bsure\.py["']?\s+(?:board|install|uninstall)\b""", re.I)
# commands that only look (or whose write is a redirect/tee, checked by _WRITES_CTL); git ones are the common
# harmless uses in a repo that tracks sure.json
_READ_ONLY = re.compile(
    r"""^(?:cat|ls|dir|type|head|tail|grep|rg|get-content|gc|
         select-string|wc|jq|less|more|bat|stat|diff|test-path|test)(?=\s|$)
     |^\[(?=\s)
     |^git(?:\s+(?:-[Cc]\s+\S+|--[\w-]+(?:=\S+)?))*\s+(?:add|diff|log|show|status|
        blame|ls-files|commit|grep|check-ignore)\b
     |^python3?(?:\.exe)?\s+\S*sure\.py["']?\s+(?:stats|report)\b""",
    re.I | re.X,
)
_WRITES_CTL = re.compile(
    r""">>?\s*["']?[^\s"']*(?:(?<![\w-])\.sure[\\/]|(?<![\w.-])sure\.json)|\btee\b""",
    re.I,
)  # a read redirected into the controls
_CD = re.compile(r"^(?:cd|pushd|chdir|set-location|sl)(?=\s|$)", re.I)
_CD_SURE = re.compile(r"""^\S+\s+.*?(?<![\w-])\.sure[\\/]?["']?\s*$""", re.I)  # cd into the .sure folder itself
_REDIRECT = re.compile(r">(?!&)")
MAX_CMD = 4000  # ponytail: only the first 4000 chars are inspected, bounds regex time
MAX_PATH = 4096  # ponytail: only the last 4096 chars of a file path are inspected
CMD_SHOWN = 2000  # characters of the command the board card shows


def _path_kind(tool, path):
    p = path.replace("\\", "/")  # directory order decides: settings_or_mode and protected paths come before config
    if len(p) > MAX_PATH:
        p = p[-MAX_PATH:]  # keep the end (file name) for matching
    return next((k for k, t, rx in _PATHS if t.fullmatch(tool) and rx.search(p)), None)


def _tool_kind(tool):
    return next((k for k, t, rx in _TOOLS if t.fullmatch(tool) and rx.search(tool)), None)


def _lead(seg, again=True):
    """The segment from its real command word on: drops (, {, then/do/else, &, time, nice, nohup,
    command [-p], exec, timeout [opts] N, sudo [opts], env, VAR=x, xargs [opts]; then \rm or 'rm' become rm
    (once, and the strip runs again). At most 16 passes keeps it linear."""
    s = seg.strip()
    for _ in range(16):
        m = _LEAD.match(s)
        if not m or not m.end():
            break
        s = s[m.end():].lstrip()
    u = _WORD_Q.match(s) if again else None
    return _lead((u.group(2) or "") + s[u.end():], False) if u else s


def _code_mask(cmd):
    """live[i] is True when cmd[i] is shell code: not inside quotes and not in a # comment.
    ponytail: heredoc bodies are not tracked, so a stray quote in a body can hide a later << (that body is then kept: safe side)."""
    live, q, comment, esc, prev = [], None, False, False, "\n"
    for c in cmd:
        if esc:
            live.append(q is None and not comment)
            esc = False
        elif comment:
            comment = c != "\n"
            live.append(not comment)
        elif q:
            live.append(False)
            esc = q == '"' and c == "\\"
            q = None if c == q else q
        elif c == "\\":
            live.append(True)
            esc = True
        elif c in "'\"":
            q = c
            live.append(False)
        elif c == "#" and (prev.isspace() or prev in ";|&("):
            comment = True
            live.append(False)
        else:
            live.append(True)
        prev = c
    return live


def _payloads(seg):
    """Code a segment hands to a shell: the quoted arguments after bash/sh/pwsh -c (the payload, and the
    trailing ones that "$1" could run), or what follows eval with its quotes removed."""
    e = _EVAL.match(seg)
    if e:
        return [re.sub(r"['\"]", "", seg[e.end():])]
    m = _DASH_C.match(seg)
    if not m:
        return []
    args = [q.group() for _, q in zip(range(MAX_PAYLOADS), _QARG.finditer(seg, m.end()))]
    return [a[1:-1] if len(a) > 1 and a[-1] == a[0] else a[1:] for a in args]


def _pipes_to_shell(segs, seps):
    """A bare shell or interpreter (options only) reading a pipe whose first command downloads; no length bound."""
    start = 0
    for i in range(1, len(segs)):
        if seps[i - 1] != "|":
            start = i
            continue
        w = segs[i].split()
        if (w and _base(w[0]).lower().removesuffix(".exe") in _SHELL_IN and _DL.match(segs[start])
                and all(t.startswith("-") and t.lower() not in ("-c", "-command") for t in w[1:])):
            return True
    return False


def _split(cmd, seps=None):
    """Split on && || ; | & newline, but not inside quotes (and not the & of 2>&1, >&2, &>).
    seps, if a list, gets each separator in order ("&&", "||", ";", "|", "&" or a newline)."""
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
        elif c in ";|\n" or (c == "&" and cmd[i - 1 : i] not in ("<", ">") and cmd[i + 1 : i + 2] != ">"):
            segs.append("".join(cur))
            cur = []
            two = cmd.startswith(("&&", "||"), i)
            if seps is not None:
                seps.append(cmd[i : i + 2] if two else c)
            i += 2 if two else 1
        else:
            cur.append(c)
            i += 1
    segs.append("".join(cur))
    return segs


def _base(tok):
    return tok.strip("'\"").replace("\\", "/").split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _downloads_then_runs(segs):
    files = set()
    for s in segs:
        if _DL.match(s):
            m = _OUT.search(s)
            u = _URL.search(s) if s[:4].lower() == "wget" else None  # wget saves under the URL's last segment
            name = _base(next(g for g in m.groups() if g)) if m else _base(u.group()) if u else ""
            if name:
                files.add(name)
            continue
        t = s.split()
        via = bool(t) and _base(t[0]).lower().removesuffix(".exe") in _INTERP
        if files and t:
            if via:
                if any(_base(x) in files for x in t[1:7]):
                    return True
            elif t[0].replace("\\", "/").startswith(("./", "/", "~")) and _base(t[0]) in files:
                return True
    return False


def _risk(kind, target, keep_end=False):
    tier, what, why, cost, undo = CARDS[kind]
    if kind == "delete" and _WIDE.search(target):
        cost = "Targets your whole home, drive or project folder. " + cost
    e = _BY_ID.get(kind, {})
    # B1: a quiet-by-default kind with ask_tokens (MCP tools) asks only when the name holds one of them
    words = set(directory.name_tokens(target))
    quiet = e.get("default") == "quiet" and not set(e.get("ask_tokens") or ()) & words
    if e.get("app_prefix") and target.startswith(e["app_prefix"]):
        # the desktop app's own tools are gated by the app itself: quiet except the risky verbs
        quiet = not set(e["app_ask_tokens"]) & words
    # Slice before cleaning: commands keep first MAX_CMD; paths keep last MAX_PATH (filename at end).
    if keep_end:  # file paths: the file name is at the end
        target = target[-MAX_PATH:]
    else:  # commands, URLs, tool names: the start is what the owner reads
        target = target[:MAX_CMD]
    full = directory.clean(target)  # no bidi/zero-width/control chars, secrets masked
    short = full if len(full) <= 80 else full[:77] + "..."
    return {
        "quiet": quiet,
        "kind": kind,
        "tier": tier,
        "what": what.format(target=short),
        # the board shows this in full (line breaks kept), so a cut or one-line title cannot hide the rest
        "command": directory.cap(directory.clean(target, keep_newlines=True), CMD_SHOWN),
        "why": why,
        "cost": cost,
        "undo": f"{tier.capitalize()}: {undo}",
    }


def _heredoc(m):
    """Keep the rest of the << line; keep the body only if the segment that owns the heredoc feeds
    it to a shell, interpreter or database client (cat > f.sh <<EOF stays unread)."""
    head = m.string[m.string.rfind("\n", 0, m.start()) + 1 : m.start()]
    seps = []
    rest = _split(m.group(3), seps)  # cat <<EOF | bash: the rest of the << line pipes the body into a shell
    piped = []
    for s, sep in zip(rest[1:], seps):
        if sep != "|":
            break
        piped.append(s)
    feeds = any(_feeds(s) for s in [_split(head)[-1]] + piped)
    return f"{m.group(3)}\n{m.group(4) if feeds else ''}\n"


def _feeds(seg):
    words = _lead(seg).split()
    return bool(words) and _base(words[0]).lower().removesuffix(".exe") in _FEEDS


_RANK = {"easy": 0, "hard": 1, "impossible": 2}
_ORDER = {e["id"]: i for i, e in enumerate(_ENTRIES)}


def _bash_kind(cmd):
    """The matching kind with the highest tier; ties keep directory order."""
    found = _bash_kinds(cmd)
    return min(found, key=lambda k: (-_RANK[CARDS[k][0]], _ORDER[k])) if found else None


def _touches_controls(raw, segs):
    """Does any segment write, delete, move or run Sure?'s own controls? Looks and git bookkeeping stay quiet.
    After a cd into the .sure folder, any later segment that is not a plain look counts (board.json has no folder in its name)."""
    in_dir = False
    for r, s in zip(raw, segs):
        if _CD.match(s):
            in_dir = bool(_CD_SURE.match(s))
            continue
        look = _PRINT.match(s) or _READ_ONLY.match(s)
        if look:  # a look is a hit only when it writes: unblanked, so a quoted redirect target counts
            t = _QUOTED.sub(lambda q: " " * len(q.group()), r) if s[:4].lower() == "git " else r
            if (_SURE_CTL.search(t) and _WRITES_CTL.search(t)) or (in_dir and _REDIRECT.search(t)):
                return True
        elif in_dir or _SURE_CTL.search(r):
            return True
    return False


def _bash_kinds(cmd, depth=0):
    """Every kind that matches anywhere: segments, whole text, download-then-run, bash -c payloads."""
    if "<<" in cmd and cmd.count("<<") <= 16:
        # a << inside quotes or a # comment is text, not a heredoc;
        # more than 16 are left raw (safe side, bounds the scan)
        live = _code_mask(cmd)
        cmd = _HEREDOC.sub(lambda m: _heredoc(m) if live[m.start()] else m.group(), cmd)
    seps = []
    raw = _split(cmd, seps)
    segs = [_lead(s) for s in raw]
    whole, pos = cmd, 0
    for r, s in zip(raw, segs):  # text that is only printed must not trip whole-command rules
        i = cmd.find(r, pos)
        pos = i + len(r)
        if _PRINT.match(s) and cmd[pos : pos + 1] != "|":

            def _unblank(q):
                g = q.group()
                if g[0] == '"' and ("$(" in g or "`" in g):
                    return g
                return " " * len(g)

            r = _QUOTED.sub(_unblank, r)
            whole = whole[:i] + r + whole[pos:]  # keeps redirects like > /etc/sudoers
    found = ["download_and_run"] if _downloads_then_runs(segs) else []
    if "pipe_to_shell" in CARDS and _pipes_to_shell(segs, seps):
        found.append("pipe_to_shell")
    if "settings_or_mode" in CARDS and _touches_controls(raw, segs):
        found.append("settings_or_mode")  # B9.3: Sure?'s own controls (.sure/, sure.json, sure.py board), not mere reads
    found += [kind for kind, scope, rx in RULES if any(rx.search(t) for t in ([whole] if scope == "whole" else segs))]
    if depth < 3:  # bash -c "..." payloads are inspected too
        for s in segs:
            for p in _payloads(s):
                found += _bash_kinds(p, depth + 1)
    return found


def classify(tool_name, tool_input):
    """Return a risk dict for a risky tool call, else None."""
    if tool_name in ("Bash", "PowerShell"):  # same rules; the PowerShell input key is also "command"
        cmd = tool_input.get("command", "")
        kind = _bash_kind(cmd[:MAX_CMD])
        if not kind and len(cmd) > MAX_CMD and "unreadable_command" in CARDS:
            kind = "unreadable_command"  # the unread tail could hold anything: ask, never stay silent
        if kind:
            return _risk(kind, cmd)
    elif tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("notebook_path" if tool_name == "NotebookEdit" else "file_path", "")
        kind = _path_kind(tool_name, path)
        if kind:
            return _risk(kind, path, keep_end=True)
    elif kind := _tool_kind(tool_name):
        t = tool_input
        target = {"WebFetch": t.get("url"), "WebSearch": t.get("query")}.get(tool_name)
        if kind == "external_tool":
            target = tool_name
        elif kind == "subagent":
            target = t.get("description") or t.get("prompt")
        return _risk(kind, str(target or tool_name))
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
    """Append one ledger line: always a single clean line, secrets masked. Never raises."""
    try:
        with open(directory.sure_dir(cwd) / "ledger.md", "a", encoding="utf-8") as f:
            f.write(f"- {datetime.now().isoformat(timespec='seconds')} {directory.clean(line)}\n")
    except Exception:  # OSError, a NUL in cwd (ValueError), or no directory module: the hook goes on
        pass


def load_manifest(cwd):
    try:
        return directory.read_manifest(cwd) or {}
    except Exception:  # no directory module: run as if there were no sure.json
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
# the extra tools are quiet by default (see "default" in permissions.json), so hooking them adds no prompts
_MATCHER = "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|WebFetch|WebSearch|Task|Agent|mcp__.*"


_OURS = re.compile(r'python "((?:[^"]*/)?[^"/]*)" (?:pre|post|failed)')


def _ours(hook):
    """Exactly the command shape install() writes, with a script file named exactly sure.py."""
    m = isinstance(hook, dict) and isinstance(hook.get("command"), str) and _OURS.fullmatch(hook["command"])
    return bool(m) and m.group(1).rsplit("/", 1)[-1] == "sure.py"


def _strip_ours(data):
    """Drop only our hook objects; an entry or event list goes only if that removal emptied it."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    for ev in list(hooks):
        if not isinstance(hooks[ev], list):
            continue
        kept = []
        for e in hooks[ev]:
            hs = e.get("hooks") if isinstance(e, dict) else None
            if isinstance(hs, list) and any(_ours(h) for h in hs):
                e = {**e, "hooks": [h for h in hs if not _ours(h)]}
                if not e["hooks"]:
                    continue
            kept.append(e)
        if len(kept) != len(hooks[ev]) and not kept:
            del hooks[ev]
        else:
            hooks[ev] = kept
    if not hooks:
        del data["hooks"]


def _write_settings(p, data):
    """Write only if the bytes change; the one backup always holds the content from before the latest write."""
    new = json.dumps(data, indent=2) + "\n"
    if p.exists():
        if p.read_text(encoding="utf-8") == new:  # text mode: the CRLF that write_text puts on Windows reads back as \n
            return
        # Atomic backup: write to temp then os.replace
        bak = p.with_name(p.name + ".sure.bak")
        bak_tmp = p.with_name(p.name + ".sure.bak.tmp")
        try:
            bak_tmp.write_bytes(p.read_bytes())
            os.replace(bak_tmp, bak)
        except Exception:
            if bak_tmp.exists():
                bak_tmp.unlink(missing_ok=True)
            raise
        orig = p.with_name(p.name + ".sure.orig")  # the file as it was before Sure? first changed it; never overwritten
        if not orig.exists():
            orig_tmp = p.with_name(p.name + ".sure.orig.tmp")
            try:
                orig_tmp.write_bytes(p.read_bytes())
                os.replace(orig_tmp, orig)
            except Exception:
                if orig_tmp.exists():
                    orig_tmp.unlink(missing_ok=True)
                raise
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".sure.tmp")
    try:
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def _load_settings(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def install(path=None, board=False):
    """Add Sure? hooks to a Claude Code settings.json, keeping everything else.
    PreToolUse always gets timeout 130 (the hook returns in milliseconds when the board is off), so
    `board on` after a plain install still works; `board` is kept for compatibility and changes nothing."""
    p = Path(path) if path else Path.home() / ".claude" / "settings.json"
    data = _load_settings(p)
    _strip_ours(data)
    script = Path(__file__).resolve().as_posix()
    for ev, arg in _EVENTS.items():
        timeout = 130 if ev == "PreToolUse" else 10
        entry = {
            "matcher": _MATCHER,
            "hooks": [{"type": "command", "command": f'python "{script}" {arg}', "timeout": timeout}],
        }
        data.setdefault("hooks", {}).setdefault(ev, []).append(entry)
    _write_settings(p, data)


def uninstall(path=None):
    p = Path(path) if path else Path.home() / ".claude" / "settings.json"
    data = _load_settings(p)
    _strip_ours(data)
    _write_settings(p, data)


def _board_on(man):
    b = man.get("board")
    return isinstance(b, dict) and b.get("enabled") is True


def set_board(cwd, on):
    """Toggle board.enabled in cwd/sure.json (atomic, key order kept). Returns an error string, else None."""
    import trust  # the one reader and atomic writer that "Always allow" also uses

    p = Path(cwd) / "sure.json"
    data = {}
    if p.exists():
        data = directory.read_manifest(cwd)  # tolerates a BOM; written back without one
        if data is None:
            return f"sure: {p} is not a valid JSON object; fix it first, nothing was changed"
    elif not on:
        return None
    b = data.get("board")
    if "board" in data and not isinstance(b, dict):
        return f"sure: the \"board\" value in {p} is not an object; fix it first, nothing was changed"
    data["board"] = {**(b or {}), "enabled": on}
    trust._write(cwd, data)


def _silent(risk, man):
    """"quiet", "auto", or None when a card is due. ask_on always wins (user call, B6): a kind it lists
    gets a card even when it is quiet by default or also in auto_allow. Only easy kinds can be auto."""
    ask_on = man.get("ask_on")
    if isinstance(ask_on, list) and risk["kind"] in ask_on:
        return None
    if risk["quiet"]:
        return "quiet"
    auto = man.get("auto_allow")
    return "auto" if risk["tier"] == "easy" and isinstance(auto, list) and risk["kind"] in auto else None


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
        sid = data.get("session_id")
        sid = sid if isinstance(sid, str) else None
        k, t = risk["kind"], risk["tier"]

        def rec(verb, via=None, what=None):
            if events:
                events.record(cwd, verb, k, t, via=via, session=sid, what=what)

        tid = data.get("tool_use_id")
        tid = tid if isinstance(tid, str) and 0 < len(tid) <= 200 else None
        man = load_manifest(cwd)
        how = _silent(risk, man)
        if argv[0] in ("post", "failed"):
            verb = "ran" if argv[0] == "post" else "failed"
            log(cwd, f"{verb} {tag}")
            rec(verb)
            if tid and not how and _board_on(man):  # quiet/auto kinds never posted a card: nothing to withdraw
                try:
                    import board  # lazy: only projects that opted in pay for it

                    board.withdraw(tid)
                except Exception:
                    pass
            return ""
        if how:  # quiet: logged, never shown; auto: an easy kind the project always allows
            log(cwd, f"{how} {tag}")
            rec(how, "rule")
            return ""
        log(cwd, f"asked {tag} {risk['what']}")
        rec("asked", what=risk["what"])
        sp = man.get("save_points")
        if isinstance(sp, dict) and sp.get("enabled"):
            ref = save_point(cwd)
            if ref:
                log(cwd, f"save point {ref}")
        out = {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": render_card(risk)}
        if _board_on(man):
            rid, got = tid or uuid.uuid4().hex, None
            try:
                import board  # lazy: only projects that opted in pay for it

                got = board.ask_board({"tool": data["tool_name"], "kind": k, "tier": t, "what": risk["what"],
                                       "command": risk["command"],
                                       "why": risk["why"], "cost": risk["cost"], "undo": risk["undo"],
                                       "session": sid or "", "cwd": cwd, "request_id": rid}, board.WAIT_SECONDS)
            except Exception:
                pass  # fail open: today's ask
            # Task 0 (spec "Hook allow vs deny", task-0-report.md): a hook "allow" never beat the user's
            # deny rule (8 hook runs, 6 marker-proven, all denied), so Allow once sends "allow" directly.
            # Caveat: the no-hook control denied only 5 of 6; auto/bypass modes and "ask" rules untested.
            if got in ("allow_once", "block", "ask"):  # None (no board, timeout, error) is not a decision
                log(cwd, f"decided {tag} {got}")
                if events:
                    events.record(cwd, "decided", k, t, via="board", session=sid, request_id=rid,
                                  decision=got, what=risk["what"])
                if got == "allow_once":
                    out.update(permissionDecision="allow", permissionDecisionReason="Allowed once on the Sure? board.")
                elif got == "block":
                    out["permissionDecision"] = "deny"  # "ask" keeps the plain card: Claude's own prompt decides
        return json.dumps({"hookSpecificOutput": out})
    except Exception as e:
        log(cwd, " ".join(f"error {type(e).__name__}: {e}".split())[:120])
        return ""


def _cli_fail(what, e):
    try:
        sys.stderr.write(f"sure: {what} failed: {type(e).__name__}: {' '.join(str(e).split())[:200]}\n")
    except Exception:
        pass  # stderr closed or None: still exit 1
    sys.exit(1)


def _cli(args):
    """install / uninstall / board: report failures on stderr with exit 1. Returns only if args is not one of them."""
    try:
        if args[:1] == ["install"]:
            install(*args[1:2], board="--board" in sys.argv)
        elif args[:1] == ["uninstall"]:
            uninstall(*args[1:2])
        elif args[:1] == ["board"] and args[1:2] in (["on"], ["off"]):
            err = set_board(Path.cwd(), args[1] == "on")
            if err:
                sys.stderr.write(err + "\n")
                sys.exit(1)
            print("board " + args[1])
            sys.exit(0)
        elif args[:1] == ["board"]:
            import board

            board.serve()
            sys.exit(0)
        else:
            return
    except Exception as e:
        _cli_fail(args[0], e)
    print("done")
    sys.exit(0)


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["stats"]:
            print(stats(str(Path.cwd())))
            sys.exit(0)
        if sys.argv[1:2] == ["report"]:
            print(report(str(Path.cwd()), int(sys.argv[2]) if len(sys.argv) > 2 else 7))
            sys.exit(0)
        _cli([a for a in sys.argv[1:] if a != "--board"])
        if sys.argv[1:2] == ["undo"]:
            print("restored" if undo(str(Path.cwd()), int(sys.argv[2])) else "failed")
            sys.exit(0)
        sys.stdout.write(main(sys.argv[1:], sys.stdin.buffer.read().decode("utf-8", "replace"), str(Path.cwd())))
    except Exception:
        pass
    sys.exit(0)
