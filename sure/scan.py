"""sure scan: replay Claude Code transcripts through today's rules. Read-only, masked, tolerant of junk."""
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import directory


def scan(root, days=30, now=None, classify=None):
    """Scan transcript files and classify tool calls."""
    if classify is None:
        import sure
        classify = sure.classify

    if now is None:
        now = datetime.now(timezone.utc)

    root_path = Path(root)
    if not root_path.exists():
        return _empty_summary(days)

    cutoff = now - timedelta(days=days)
    files = list(root_path.rglob("*.jsonl"))

    calls = 0
    sessions = set()
    cards = 0
    logged_only = 0
    by_kind = {}
    by_tier = {}
    kind_tier = {}
    top_tier = {}
    top_counts = {}
    directory_entries = _load_directory()

    for file_path in files:
        try:
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            mtime = now

        for line in _lines(file_path):
            try:
                obj = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(obj, dict):
                continue

            if obj.get("type") != "assistant":
                continue

            call_time = _parse_time(obj.get("timestamp"), mtime, now)
            if call_time < cutoff:
                continue

            session_id = obj.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                session_id = str(file_path)
            sessions.add(session_id)

            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            content_list = message.get("content")
            if not isinstance(content_list, list):
                continue

            for item in content_list:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "tool_use":
                    continue
                tool_name = item.get("name")
                tool_input = item.get("input")
                if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
                    continue

                calls += 1
                try:
                    risk = classify(tool_name, tool_input)
                except Exception:
                    continue
                if not isinstance(risk, dict):
                    continue

                kind = risk.get("kind")
                tier = risk.get("tier")
                command = risk.get("command")

                if not isinstance(kind, str) or not isinstance(tier, str):
                    continue

                entry = _find_entry(directory_entries, kind)
                is_quiet = entry and entry.get("default") == "quiet"

                if is_quiet:
                    logged_only += 1
                else:
                    cards += 1
                    by_kind[kind] = by_kind.get(kind, 0) + 1
                    by_tier[tier] = by_tier.get(tier, 0) + 1
                    kind_tier[kind] = _worse(kind_tier.get(kind), tier)

                    text = _mask(command) if isinstance(command, str) else ""
                    if len(text) > 140:
                        text = text[:140]
                    key = (kind, text)
                    top_counts[key] = top_counts.get(key, 0) + 1
                    top_tier[key] = _worse(top_tier.get(key), tier)

    top_list = []
    for (kind, text), count in sorted(top_counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])):
        if len(top_list) >= 10:
            break
        tier = top_tier[(kind, text)]
        top_list.append({"count": count, "kind": kind, "tier": tier, "text": text})

    return {
        "calls": calls,
        "files": len(files),
        "sessions": len(sessions),
        "cards": cards,
        "logged_only": logged_only,
        "by_kind": by_kind,
        "by_tier": by_tier,
        "kind_tier": kind_tier,
        "top": top_list,
        "days": days,
    }


def _empty_summary(days):
    return {
        "calls": 0,
        "files": 0,
        "sessions": 0,
        "cards": 0,
        "logged_only": 0,
        "by_kind": {},
        "by_tier": {},
        "kind_tier": {},
        "top": [],
        "days": days,
    }


def _load_directory():
    try:
        return directory.load()
    except Exception:
        return []


def _find_entry(entries, kind):
    for e in entries:
        if e.get("id") == kind:
            return e
    return None


_RANK = {"easy": 0, "hard": 1, "impossible": 2}


def _worse(old, new):
    if old is None or _RANK.get(new, 0) > _RANK.get(old, 0):
        return new
    return old


MAX_LINE = 2000000
MAX_FILE = 500 * 1024 * 1024

# mysql -phunter2 style; too loose for directory.clean (it would eat find -print0)
_DASH_P = re.compile(r"(\s-p)\S{3,}")


def _mask(text):
    return _DASH_P.sub(lambda m: m.group(1) + "***", directory.clean(text))[:140]


def _lines(path):
    """Yield lines of a file without loading it whole; skip huge files and oversized lines."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            return
        with open(path, encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline(MAX_LINE + 1)
                if not line:
                    return
                if len(line) > MAX_LINE:
                    while line and not line.endswith("\n"):
                        line = f.readline(MAX_LINE)
                    continue
                yield line
    except OSError:
        return


def _parse_time(ts_str, fallback, now):
    if not isinstance(ts_str, str) or not ts_str:
        return fallback
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return fallback


def render_text(summary):
    """Plain text report."""
    if summary["files"] == 0:
        return "No Claude Code transcripts found"

    lines = []
    lines.append(
        f"Scanned {summary['files']} transcript files, "
        f"{summary['sessions']} sessions, "
        f"{summary['calls']} tool calls in the last {summary['days']} days."
    )
    lines.append(f"Cards: {summary['cards']}  Logged only: {summary['logged_only']}")
    lines.append("")

    kind_items = sorted(summary["by_kind"].items(), key=lambda kv: (-kv[1], kv[0]))
    for kind, count in kind_items:
        tier = summary.get("kind_tier", {}).get(kind, "easy")
        lines.append(f"{kind} ({tier}): {count}")

    lines.append("")
    lines.append("Top commands:")
    for item in summary["top"]:
        lines.append(f"  {item['count']}x  {item['kind']} ({item['tier']})  {item['text']}")

    lines.append("")
    lines.append("This shows what would match now, not what was blocked then.")
    return "\n".join(lines)


def render_html(summary):
    """Self-contained HTML page."""
    escaped = html.escape
    parts = []
    parts.append("<!doctype html>")
    parts.append("<html><head><meta charset=\"utf-8\">")
    parts.append("<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">")
    parts.append("<title>Sure? Scan Report</title>")
    parts.append("<style>")
    parts.append("body{font-family:system-ui,sans-serif;margin:2rem;line-height:1.5;max-width:900px}")
    parts.append("h1{font-size:1.5rem;margin-bottom:.5rem}")
    parts.append(".meta{color:#555;margin-bottom:1rem}")
    parts.append("table{border-collapse:collapse;width:100%;margin:1rem 0}")
    parts.append("th,td{border:1px solid #ddd;padding:.5rem;text-align:left}")
    parts.append("th{background:#f5f5f5}")
    parts.append(".top-table td{font-family:monospace;font-size:.9rem}")
    parts.append(".note{margin-top:1rem;color:#666;font-style:italic}")
    parts.append("</style></head><body>")
    parts.append("<h1>Sure? Scan Report</h1>")

    if summary["files"] == 0:
        parts.append("<p>No Claude Code transcripts found</p>")
    else:
        parts.append(
            f"<p class=\"meta\">Scanned {escaped(str(summary['files']))} "
            f"transcript files, {escaped(str(summary['sessions']))} sessions, "
            f"{escaped(str(summary['calls']))} tool calls in the last "
            f"{escaped(str(summary['days']))} days.</p>"
        )
        parts.append(
            f"<p class=\"meta\">Cards: {escaped(str(summary['cards']))}  "
            f"Logged only: {escaped(str(summary['logged_only']))}</p>"
        )

        parts.append("<h2>By Kind</h2>")
        parts.append("<table><tr><th>Kind</th><th>Tier</th><th>Count</th></tr>")
        for kind, count in sorted(summary["by_kind"].items(), key=lambda kv: (-kv[1], kv[0])):
            tier = summary.get("kind_tier", {}).get(kind, "easy")
            parts.append(f"<tr><td>{escaped(kind)}</td><td>{escaped(tier)}</td><td>{escaped(str(count))}</td></tr>")
        parts.append("</table>")

        parts.append("<h2>Top Commands</h2>")
        parts.append("<table class=\"top-table\"><tr><th>Count</th><th>Kind</th><th>Tier</th><th>Command</th></tr>")
        for item in summary["top"]:
            parts.append(f"<tr><td>{escaped(str(item['count']))}</td><td>{escaped(item['kind'])}</td><td>{escaped(item['tier'])}</td><td>{escaped(item['text'])}</td></tr>")
        parts.append("</table>")

    parts.append("<p class=\"note\">This shows what would match now, not what was blocked then.</p>")
    parts.append("</body></html>")
    return "".join(parts)


def main(argv):
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(prog="sure scan", add_help=False)
    parser.add_argument("--dir", default="~/.claude/projects")
    parser.add_argument("--days", default="30")
    parser.add_argument("--html", default=None)
    args = parser.parse_args(argv)

    root = os.path.expanduser(args.dir)
    try:
        days = int(args.days)
        if days <= 0:
            days = 30
        days = min(days, 36500)
    except ValueError:
        days = 30

    summary = scan(root, days=days)
    text = render_text(summary)

    if args.html:
        try:
            page = render_html(summary)
            target = os.path.expanduser(args.html)
            with open(target, "w", encoding="utf-8") as f:
                f.write(page)
            text += f"\nHTML written to {target}"
        except OSError as e:
            text += f"\nFailed to write HTML: {e}"

    return text


if __name__ == "__main__":
    print(main(sys.argv[1:]))
