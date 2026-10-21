"""Does a PreToolUse hook 'allow' beat a permissions.deny rule? Prints ran/denied.
Usage: python verify_hook_allow.py [--no-hook]   (--no-hook = control run)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

no_hook = "--no-hook" in sys.argv
proj = tempfile.mkdtemp(prefix="sure_allow_")
os.makedirs(os.path.join(proj, ".claude"))
hook = os.path.join(proj, "allow_hook.py")
with open(hook, "w") as f:
    f.write('import json,os\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)),"fired"),"w").write("1")\nprint(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"test allow"}}))\n')
settings = {"permissions": {"deny": ["Bash(echo SUREDENY*)"]}}
if not no_hook:
    cmd = '"%s" "%s"' % (sys.executable, hook)
    settings["hooks"] = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]}]}
with open(os.path.join(proj, ".claude", "settings.json"), "w") as f:
    json.dump(settings, f)

r = subprocess.run(["claude", "-p", "Run exactly: echo SUREDENY ok", "--allowedTools", "Bash",
                    "--output-format", "json"], cwd=proj, capture_output=True, text=True, timeout=200)
line = r.stdout.strip()
try:
    d = json.loads(line)
    denials = d.get("permission_denials", [])
    print("denied" if denials else "ran")
    print("permission_denials:", json.dumps(denials))
    print("result:", d.get("result"))
except ValueError:
    print("inconclusive")
    print("stdout:", r.stdout[:500])
print("hook_fired:", os.path.exists(os.path.join(proj, "fired")))
print("raw:", line[:2000])
print("stderr:", r.stderr[:500])
shutil.rmtree(proj, ignore_errors=True)
