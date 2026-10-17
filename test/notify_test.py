#!/usr/bin/env python3
"""The owner's outbox (lib/notify.py): once per condition, in order, capped."""
import os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
fails = 0
def check(msg, cond):
    global fails
    print(f"    {'ok' if cond else 'FAIL'}  {msg}")
    fails += 0 if cond else 1
def main():
    scratch = tempfile.mkdtemp(prefix="omacar-notify-")
    os.environ["XDG_STATE_HOME"] = scratch
    import notify
    try:
        a = notify.send("battery-low", "12 V low", "11.9 V parked", "warn", key="batt", now=1000)
        check("an event is numbered from 1", a["id"] == 1)
        check("the same key inside 10 minutes is not sent twice",
              notify.send("battery-low", "12 V low", "11.8 V", "warn", key="batt", now=1300) is None)
        check("after 10 minutes it is", notify.send("battery-low", "12 V low", "11.7 V", "warn", key="batt", now=1700)["id"] == 2)
        check("since() gives only newer events, oldest first", [e["id"] for e in notify.since(1)] == [2])
        try:
            notify.send("x", "t", "b", "loud")
            check("an unknown urgency is refused", False)
        except ValueError:
            check("an unknown urgency is refused", True)
        for i in range(250):
            notify.send("test", "t", "b", now=2000 + i)
        rows = notify.since(0, limit=1000)
        check(f"the outbox keeps the last {notify.KEEP}", len(rows) == notify.KEEP and rows[-1]["id"] == 252)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n  {'all good' if not fails else f'{fails} failed'}\n")
    return 1 if fails else 0
if __name__ == "__main__":
    sys.exit(main())
