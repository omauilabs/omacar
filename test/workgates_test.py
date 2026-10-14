#!/usr/bin/env python3
"""The gates: every tool call a Work session makes is judged here.

A refusal quietly becoming an allow is the bug that matters most in Work, so
this is a table, and both halves of it are checked.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workgates as g  # noqa: E402

H = "/home/omacar"
WT = H + "/Projects/.omacar-work/omacar-fix-timeout"
CTX = g.Ctx(worktree=WT, host="tablet", home=H)
fails = 0
def check(msg, got, want):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

def bash(c): return ("Bash", {"command": c})

REFUSE = [
    ("write outside the worktree", ("Write", {"file_path": H + "/notes.txt", "content": "x"})),
    ("write via .. out of the worktree", ("Edit", {"file_path": WT + "/../omacar/lib/x.py", "old_string": "a", "new_string": "b"})),
    ("write the live checkout", ("Write", {"file_path": H + "/Projects/omacar/lib/x.py", "content": ""})),
    ("read private assets", ("Read", {"file_path": H + "/Projects/omacar/share/assets/private/vin.txt"})),
    ("read the app's state", ("Read", {"file_path": H + "/.local/state/omacar/live.json"})),
    ("git push", bash("git push origin work/fix")),
    ("git push hidden after &&", bash("cd lib && git push")),
    ("git push inside bash -c", bash('bash -c "git push -u origin HEAD"')),
    ("git push inside $()", bash('echo $(git push)')),
    ("force anything", bash("git commit --amend && git rebase --force")),
    ("checkout main", bash("git checkout main")),
    ("gh pr merge", bash("gh pr merge 41 --squash")),
    ("gh pr create (the runner does that)", bash("gh pr create --draft")),
    ("gh api write", bash("gh api -X PATCH repos/o/r/pulls/1")),
    ("ssh to the tablet", bash("ssh omacar@omacar-1 uptime")),
    ("ssh after a pipe", bash("echo hi | ssh box")),
    ("scp", bash("scp a b:")),
    ("sudo", bash("sudo true")),
    ("env then sudo", bash("env FOO=1 sudo true")),
    ("systemctl", bash("systemctl --user restart omacar-kiosk")),
    ("omacar cli", bash("omacar write --test fan_high")),
    ("serial device", bash("cat /dev/ttyUSB0")),
    ("rfcomm", bash("stty -F /dev/rfcomm0")),
    ("docker prune", bash("docker system prune -af")),
    ("pacman", bash("pacman -S foo")),
    ("pkill", bash("pkill -f serve.py")),
    ("kill", bash("kill 1234")),
    ("hosted runner added", ("Edit", {"file_path": WT + "/.github/workflows/ci.yml",
        "old_string": "runs-on: [self-hosted, Linux]", "new_string": "runs-on: ubuntu-latest"})),
    ("live checkout named in a command", bash("cat ~/Projects/omacar/lib/api.py")),
    ("live checkout via a newline", bash("true\ncd /home/omacar/Projects/omacar")),
    ("bare self-hosted added", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    runs-on: self-hosted\n"})),
    ("mcp tool", ("mcp__x__y", {})),
    ("unknown tool", ("Frobnicate", {})),
]
ALLOW = [
    ("write in the worktree", ("Write", {"file_path": WT + "/lib/x.py", "content": "x"})),
    ("edit in the worktree", ("Edit", {"file_path": WT + "/lib/x.py", "old_string": "a", "new_string": "b"})),
    ("read the worktree", ("Read", {"file_path": WT + "/README.md"})),
    ("read a sibling repo", ("Read", {"file_path": H + "/Projects/omacar-notes-public/a.md"})),
    ("run the gate", bash("test/all.sh")),
    ("git status / diff / commit", bash("git status && git diff && git commit -am 'x'")),
    ("pinned self-hosted kept", ("Edit", {"file_path": WT + "/.github/workflows/ci.yml",
        "old_string": "x", "new_string": "runs-on: [self-hosted, Linux]"})),
    ("grep", ("Grep", {"pattern": "push", "path": WT})),
    ("ask the driver", ("AskUserQuestion", {"questions": []})),
    ("todo list", ("TodoWrite", {"todos": []})),
    ("web fetch", ("WebFetch", {"url": "https://example.org", "prompt": "x"})),
    ("the word push in a string", bash("grep -rn 'git push' doc/")),
    ("a sibling folder named like the live one", bash("ls ~/Projects/omacar-work-src")),
    ("a skill", ("Skill", {"skill": "superpowers:test-driven-development"})),
]

print("\n  must be refused")
for name, (tool, inp) in REFUSE:
    allowed, why = g.judge(tool, inp, CTX)
    check(name, (allowed, bool(why)), (False, True))
print("\n  must be allowed")
for name, (tool, inp) in ALLOW:
    check(name, g.judge(tool, inp, CTX), (True, ""))
print("\n  reasons are speakable")
_, why = g.judge("Bash", {"command": "git push"}, CTX)
check("one sentence, no newline", ("\n" not in why, why.endswith(".")), (True, True))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
