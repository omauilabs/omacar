#!/usr/bin/env python3
"""The gates: every tool call a Work session makes is judged here.

A refusal quietly becoming an allow is the bug that matters most in Work, so
this is a table, and both halves of it are checked.
"""
import os, sys, tempfile
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
    ("read gh credentials", ("Read", {"file_path": H + "/.config/gh/hosts.yml"})),
    ("read git-credentials", ("Read", {"file_path": H + "/.git-credentials"})),
    ("read claude credentials", ("Read", {"file_path": H + "/.claude/.credentials.json"})),
    ("cat gh credentials", bash("cat ~/.config/gh/hosts.yml")),
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
    ("web fetch (F4a: no web tools)", ("WebFetch", {"url": "https://example.org", "prompt": "x"})),
    ("web search (F4a: no web tools)", ("WebSearch", {"query": "x"})),
    ("unknown tool", ("Frobnicate", {})),
    # fix round 1: shell keywords, wrappers, shell -c, other spellings
    ("push in an if", bash("if git push; then :; fi")),
    ("ssh in a for loop", bash("for r in a; do ssh $r; done")),
    ("push in braces", bash("{ git push; }")),
    ("negated push", bash("! git push")),
    ("timeout ssh", bash("timeout 5 ssh box")),
    ("env -i ssh", bash("env -i ssh box")),
    ("bash -lc", bash("bash -lc 'git push'")),
    ("/bin/bash -c", bash("/bin/bash -c 'git push'")),
    ("git -C push", bash("git -C . push")),
    ("git --no-pager push", bash("git --no-pager push")),
    ("command git push", bash("command git push")),
    ("absolute git push", bash("/usr/bin/git push")),
    ("xargs git push", bash("xargs git push")),
    ("time git push", bash("time git push")),
    ("eval git push", bash("eval 'git push'")),
    ("nohup ssh", bash("nohup ssh x")),
    ("python inline push", bash("python3 -c 'import os; os.system(\"git push\")'")),
    ("find -exec push", bash("find . -exec git push \\;")),
    ("unbalanced quote hides a push", bash("true\ngit push\necho don't")),
    ("hash inside a word is no comment", bash("echo a#; git push")),
    ("$HOME spelling", bash("cat $HOME/Projects/omacar/lib/api.py")),
    ("${HOME} ssh key", bash("cat ${HOME}/.ssh/id_ed25519")),
    ("relative path to the live checkout", bash("cat ../../omacar/lib/api.py")),
    ("gh api --method PUT", bash("gh api --method PUT repos/o/r/pulls/1/merge")),
    ("gh api -f fields", bash("gh api repos/o/r/pulls -f title=x -f base=main")),
    ("gh api graphql", bash("gh api graphql -f query='mutation{x}'")),
    ("bare self-hosted in a list", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    runs-on: [self-hosted]\n"})),
    ("bare self-hosted with a comment", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    runs-on: self-hosted # x\n"})),
    ("hosted runner in a block list", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    runs-on:\n      - ubuntu-latest\n"})),
    ("hosted runner via matrix", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest]\n"
                   "    runs-on: ${{ matrix.os }}\n"})),
    ("edit the shell onto a workflow", bash("sed -i s/x/y/ .github/workflows/ci.yml")),
    ("serial by-id", bash("cat /dev/serial/by-id/usb-FTDI_x")),
    ("device glob", bash("ls /dev/ttyU*")),
    ("read a device", ("Read", {"file_path": "/dev/ttyUSB0"})),
    ("glob into the live checkout", ("Glob", {"pattern": "~/Projects/omacar/**"})),
    ("grep glob into .ssh", ("Grep", {"pattern": "x", "glob": "~/.ssh/*"})),
    ("grep from an ancestor", ("Grep", {"pattern": "x", "path": "~/Projects"})),
    ("pip as a module", bash("python3 -m pip install --user foo")),
    ("npm global", bash("npm install -g foo")),
    ("checkout main with a flag", bash("git checkout -q main")),
    ("switch -C main", bash("git switch -C main")),
    ("docker builder prune", bash("docker builder prune -af")),
    ("systemd-run", bash("systemd-run --user x")),
    ("a hash inside quotes is no comment", bash('echo "# not a comment"; git push')),
    ("hash after a continued line is mid-word", bash("echo a\\\n#; git push")),
    ("hash after a continued line hides a read", bash("echo a\\\n#; ls ~/.ssh")),
    # final wave D1: the text checks also run before the comment stripper,
    # and ANSI-C / locale quoting is refused outright.
    ("comment naming a protected place (checked before stripping too)", bash("echo hi # see ~/.ssh")),
    ("device named only in a comment", bash("ls # /dev/ttyUSB0")),
    ("workflow named only in a comment", bash("ls # .github/workflows/ci.yml")),
    ("ANSI-C quoting", bash("echo $'\\x67it' push")),
    ("locale quoting", bash('echo $"hi"')),
    ("matrix with a bare self-hosted", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    strategy:\n      matrix:\n        os: [[self-hosted]]\n"
                   "    runs-on: ${{ matrix.os }}\n"})),
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
    ("the word push in a string", bash("grep -rn 'git push' doc/")),
    ("a sibling folder named like the live one", bash("ls ~/Projects/omacar-work-src")),
    ("a skill", ("Skill", {"skill": "superpowers:test-driven-development"})),
    ("a comment mentioning push", bash("echo hi # git push")),
    ("a sibling named like the live one, relative", bash("cat ../../omacar-work-src/x")),
    ("pinned self-hosted written", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    runs-on: [self-hosted, Linux]\n"})),
    ("gh api read", bash("gh api repos/o/r/pulls")),
    ("grep inside the worktree", ("Grep", {"pattern": "x", "path": WT})),
    ("local npm install", bash("npm install")),
    ("uv pip install", bash("uv pip install -r requirements.txt")),
    ("new branch", bash("git checkout -b work/x")),
    ("a continued command", bash("ls \\\n  -la")),
    ("comment with an apostrophe", bash("ls # it's fine")),
    ("comment line first", bash("# don't do this\nls")),
    ("pinned matrix, inline", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    strategy:\n      matrix:\n"
                   "        os: [[self-hosted, Linux], [self-hosted, macOS, ARM64]]\n"
                   "    runs-on: ${{ matrix.os }}\n"})),
    ("pinned matrix, block", ("Write", {"file_path": WT + "/.github/workflows/x.yml",
        "content": "jobs:\n  a:\n    strategy:\n      matrix:\n        os:\n"
                   "          - [self-hosted, Linux]\n          - [self-hosted, macOS]\n"
                   "    runs-on: ${{ matrix.os }}\n"})),
]

# An Edit is judged on the file as it will be, so the fixture is a real file.
TMP = tempfile.mkdtemp()
os.makedirs(TMP + "/.github/workflows")
with open(TMP + "/.github/workflows/ci.yml", "w") as f:
    f.write("jobs:\n  a:\n    runs-on: [self-hosted, Linux]\n")
CTX2 = g.Ctx(worktree=TMP, host="tablet", home=H)

print("\n  must be refused")
for name, (tool, inp) in REFUSE:
    allowed, why = g.judge(tool, inp, CTX)
    check(name, (allowed, bool(why)), (False, True))
print("\n  must be allowed")
for name, (tool, inp) in ALLOW:
    check(name, g.judge(tool, inp, CTX), (True, ""))
print("\n  an edit is judged against the file on disk")
wf = {"file_path": TMP + "/.github/workflows/ci.yml"}
a, why = g.judge("Edit", dict(wf, old_string="[self-hosted, Linux]", new_string="[self-hosted]"), CTX2)
check("edit down to a bare self-hosted", (a, bool(why)), (False, True))
check("edit that keeps the pin", g.judge("Edit", dict(wf, old_string="a:", new_string="b:"), CTX2), (True, ""))

print("\n  D1: quoting the stripper does not model is unreadable")
check("$' gives the unreadable sentence", g.judge("Bash", {"command": "echo $'a'"}, CTX), (False, "That command could not be read safely."))
check('$" gives the unreadable sentence', g.judge("Bash", {"command": 'echo $"a"'}, CTX), (False, "That command could not be read safely."))

print("\n  F4a: sessions have no web tools")
for t in ("WebFetch", "WebSearch"):
    check(t + " refused with its sentence", g.judge(t, {}, CTX), (False, "Work sessions have no web access."))

print("\n  reasons are speakable")
_, why = g.judge("Bash", {"command": "git push"}, CTX)
check("one sentence, no newline", ("\n" not in why, why.endswith(".")), (True, True))
for name, (tool, inp) in REFUSE:
    _, why = g.judge(tool, inp, CTX)
    check("reason for " + name, ("\n" not in why, why.endswith(".")), (True, True))

import shutil; shutil.rmtree(TMP, ignore_errors=True)
print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
