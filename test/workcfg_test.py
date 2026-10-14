#!/usr/bin/env python3
"""work.toml: the allowlist, the host, the caps. Scratch config only."""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="omacar-workcfg-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(SCRATCH, "config")
sys.path.insert(0, os.path.join(ROOT, "lib"))
import workcfg  # noqa: E402

fails = 0
def check(msg, got, want=True):
    global fails
    if got == want: print(f"    ok  {msg}")
    else: fails += 1; print(f"    FAIL  {msg}: got {got!r}, want {want!r}")

print("\n  work.toml")
c = workcfg.load(os.path.join(SCRATCH, "missing.toml"))
check("no file: no projects", c.projects, {})
check("no file: host is box", c.host, "box")
check("no file: two at once, six a day", (c.max_sessions, c.daily_cap), (2, 6))
check("no file: quiet 21-7", c.quiet, (21, 7))

p = os.path.join(SCRATCH, "w.toml")
with open(p, "w") as f:
    f.write('host = "tablet"\ndaily_cap = 3\nquiet = [22, 6]\n'
            '[project.omacar]\nnames = ["omacar", "Car App"]\n'
            'path = "~/Projects/omacar"\ngate = "test/all.sh"\n')
c = workcfg.load(p)
check("host read", c.host, "tablet")
check("cap read", c.daily_cap, 3)
check("names lower-cased, own name included",
      c.projects["omacar"].names, ("omacar", "car app"))
check("path expanded", c.projects["omacar"].path,
      os.path.expanduser("~/Projects/omacar"))

with open(p, "w") as f:
    f.write('host = "laptop"\n')
try:
    workcfg.load(p); check("bad host refused", False)
except ValueError as e:
    check("bad host refused", "host" in str(e))

with open(p, "w") as f:
    f.write('[project.x]\nnames=["x"]\npath="/tmp"\n')
try:
    workcfg.load(p); check("project without gate refused", False)
except ValueError as e:
    check("project without gate refused", "gate" in str(e))

print(f"\n  {fails} failed" if fails else "\n  all passed")
sys.exit(1 if fails else 0)
