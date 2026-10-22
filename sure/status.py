"""sure status: is Sure? installed and healthy, what is this project set to, what happened lately."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def render_status(cwd, settings_path=None, runtime_path=None):
    """Plain text status report."""
    lines = []
    lines.append("Sure?")
    lines.append("")

    # Section 1: Hook
    lines.append("Hook")
    settings_file = Path(settings_path) if settings_path else Path.home() / ".claude" / "settings.json"
    hook_result = _check_hook(settings_file)
    lines.extend(hook_result)
    lines.append("")

    # Section 2: Rules
    lines.append("Rules")
    try:
        import sure
        entries = sure.directory.load()
    except Exception:
        entries = []
    n = len(entries)
    impossible = sum(1 for e in entries if e.get("tier") == "impossible")
    hard = sum(1 for e in entries if e.get("tier") == "hard")
    easy = sum(1 for e in entries if e.get("tier") == "easy")
    lines.append(f"{n} kinds ({impossible} impossible, {hard} hard, {easy} easy)")
    lines.append("")

    # Section 3: This project
    lines.append("This project")
    try:
        import sure
        manifest = sure.directory.read_manifest(cwd)
    except Exception:
        manifest = None

    if manifest is None:
        manifest = {}

    board_enabled = False
    if isinstance(manifest.get("board"), dict):
        board_enabled = manifest["board"].get("enabled") is True
    lines.append(f"Board: {'on' if board_enabled else 'off'}")

    save_points_enabled = False
    if isinstance(manifest.get("save_points"), dict):
        save_points_enabled = manifest["save_points"].get("enabled") is True
    lines.append(f"Save points: {'on' if save_points_enabled else 'off'}")

    auto_allow = manifest.get("auto_allow")
    if isinstance(auto_allow, list) and auto_allow:
        lines.append("Auto-allow: " + _names(auto_allow))
    else:
        lines.append("Auto-allow: none")

    ask_on = manifest.get("ask_on")
    if isinstance(ask_on, list) and ask_on:
        lines.append("Ask on: " + _names(ask_on))
    else:
        lines.append("Ask on: none")
    lines.append("")

    # Section 4: Board process
    lines.append("Board process")
    rt_path = Path(runtime_path) if runtime_path else _default_runtime_path()
    board_line = _check_board_process(rt_path)
    lines.append(board_line)
    lines.append("")

    # Section 5: Recent
    lines.append("Recent")
    try:
        import events
        evs = events.read(cwd, days=7)
    except Exception:
        evs = []

    if not evs:
        lines.append("No decisions yet in the last 7 days.")
    else:
        for ev in evs[-5:]:
            ts = ev.get("ts", "")
            verb = _clean(ev.get("verb"))
            kind = _clean(ev.get("kind"))
            tier = _clean(ev.get("tier"))
            if isinstance(ts, str) and ts and verb and kind and tier:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts_short = dt.strftime("%m-%d %H:%M")
                except ValueError:
                    ts_short = ts[:16]
                lines.append(f"{ts_short}  {verb}  {kind} ({tier})")

        # Most common kind
        kind_counts = {}
        for ev in evs:
            k = _clean(ev.get("kind"))
            if k:
                kind_counts[k] = kind_counts.get(k, 0) + 1
        if kind_counts:
            top_kind = max(kind_counts.items(), key=lambda kv: kv[1])
            lines.append(f"Most often: {top_kind[0]} ({top_kind[1]} times)")

    return "\n".join(lines)


def _clean(value):
    """Repo files are untrusted: strings only, control characters and secrets removed."""
    if not isinstance(value, str):
        return ""
    import directory
    return " ".join(directory.clean(value).split())[:60]


def _names(items):
    return ", ".join(n for n in (_clean(i) for i in items) if n) or "none"


def _default_runtime_path():
    env = os.environ.get("SURE_RUNTIME_FILE")
    if env and os.path.isabs(env):
        return Path(env)
    return Path.home() / ".sure" / "board.json"


def _check_hook(settings_file):
    """Check hook installation and return lines."""
    lines = []

    if not settings_file.exists():
        lines.append("NOT INSTALLED")
        lines.append(f"Run: python {_sure_py_path()} install")
        return lines

    try:
        data = json.loads(settings_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        lines.append("PROBLEM: settings could not be read")
        return lines

    import sure
    our_hooks = []
    hooks_map = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks_map, dict):
        hooks_map = {}
    for event_name, entries in hooks_map.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                continue
            for hook in hooks:
                if sure._ours(hook):
                    our_hooks.append((event_name, entry, hook))

    if not our_hooks:
        lines.append("NOT INSTALLED")
        lines.append(f"Run: python {_sure_py_path()} install")
        return lines

    events_found = sorted(set(e for e, _, _ in our_hooks))
    lines.append(f"Hook: installed ({', '.join(events_found)})")

    problems = []
    script_path = _sure_py_path()
    script_resolved = Path(script_path).resolve()

    for event_name, entry, hook in our_hooks:
        cmd = hook.get("command", "")
        # Extract script path from command
        m = sure._OURS.fullmatch(cmd)
        if m:
            hook_script = m.group(1)
            hook_path = Path(hook_script)
            if not hook_path.exists():
                problems.append(f"PROBLEM: script path from the command (the quoted path) does not exist -> BROKEN {hook_script}")
            else:
                try:
                    if hook_path.resolve() != script_resolved:
                        problems.append("PROBLEM: the script exists but differs from this installation's sure.py -> different copy")
                except OSError:
                    pass

        if event_name == "PreToolUse":
            matcher = entry.get("matcher")
            if matcher != sure._MATCHER:
                problems.append(
                    "PROBLEM: the PreToolUse entry matcher is not equal to "
                    "sure._MATCHER -> matcher (re-run the install command to refresh it)"
                )

            timeout = hook.get("timeout")
            if isinstance(timeout, (int, float)) and timeout < 130:
                problems.append("PROBLEM: the PreToolUse timeout is below 130 -> timeout (re-run the install command to refresh it)")

    if problems:
        lines.extend(problems)
        lines.append(f"Run: python {_sure_py_path()} install")
    else:
        lines.append("No problems found.")

    return lines


def _sure_py_path():
    import sure
    return Path(sure.__file__).resolve().as_posix()


def _check_board_process(runtime_path):
    """Check if board server is running."""
    try:
        data = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return "Board server: not running"

    if not isinstance(data, dict):
        return "Board server: not running"
    pid = data.get("pid")
    port = data.get("port")
    if isinstance(pid, bool) or isinstance(port, bool) or not isinstance(pid, int) or not isinstance(port, int):
        return "Board server: not running"

    try:
        import board
        if board.pid_alive(pid):
            return f"Board server: running on port {port}"
    except Exception:
        pass
    return "Board server: not running"


def main(argv):
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(prog="sure status", add_help=False)
    parser.add_argument("--settings", default=None)
    args = parser.parse_args(argv)

    settings_path = args.settings
    if settings_path:
        settings_path = os.path.expanduser(settings_path)

    return render_status(os.getcwd(), settings_path=settings_path)


if __name__ == "__main__":
    print(main(sys.argv[1:]))
