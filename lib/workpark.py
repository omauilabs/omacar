"""Is the car parked? Work's relay asks this on the tablet, once a second.

The page's rule (store.moving in share/js/core.js) in Python: moving while the
speed is over 3 km/h, during any hand-off of the adapter, and until the car
has sat stopped and connected for SETTLE_S without a break.

Unlike the page, the relay starts latched: it cannot know whether the car was
moving just before it started (a crash and a restart at a red light), so after
any relay start the car must be seen stopped for SETTLE_S before it counts as
parked.

Stricter than the page in one way: not knowing is not parked. No daemon, no
link, no speed -- the relay treats each as moving.

INTERIM, like the page's: the safety/parked-confirm branch replaces this with
a Park confirmed from frame 0x191. When it merges, this class reads that.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SETTLE_S = 60.0


class Park:
    def __init__(self, read=None, clock=time.time):
        if read is None:
            import records
            read = records.live
        self.read, self.clock = read, clock
        self.ever_moved = True       # latched: a restart proves nothing about Park
        self.still_since = None
        self.parked = False

    def update(self):
        now = self.clock()
        try:
            s = self.read() or {}
        except Exception:                                   # noqa: BLE001
            s = {}
        speed = (s.get("values") or {}).get("SPEED")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            speed = None                                    # unknown, not stopped
        known_stop = bool(s.get("connected")) and not s.get("handover") \
            and speed is not None and speed <= 3
        # A hand-off latches motion, like the page (share/js/core.js).
        if s.get("handover") or (s.get("connected") and speed is not None and speed > 3):
            self.ever_moved = True
        if not known_stop:
            self.still_since = None
            self.parked = False
            return False
        if self.still_since is None:
            self.still_since = now
        self.parked = (not self.ever_moved) or (now - self.still_since >= SETTLE_S)
        return self.parked
