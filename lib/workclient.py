#!/usr/bin/env python3
"""The Work dashboard's window onto the Work relay (`omacar work serve`).

The relay (lib/workrelay.py, the "Work by voice" feature) is an OPTIONAL
dependency: this file never imports it, never starts it and never invents what
it would have said. It reads the relay's own bearer token from the file the
relay wrote (or work.toml's relay_token on a box-hosted relay), asks the relay
three read-only questions over loopback with a short timeout, and shapes the
answers for the screen.

    omacar workdash status     what /api/workdash answers right now
    omacar workdash sim [s] [--while-moving]
                               a SIMULATED relay's answers, written to
                               workdash-sim.json so the screen can be seen
                               (the relay's while_moving choice on request);
                               the screen says "Simulated", every time

WHAT THE RELAY ANSWERS (read from lib/workrelay.py, do not guess beyond it):
  GET /health        host, while_moving, asking, parked, today, cap, and per
                     session: id, project, state, pr (a number), repo, line
  GET /events?after  the log: kind, session, project, at, line, text
  GET /diff/<id>     {stat, summary}: a diffstat and (once "read me the diff"
                     ran) a short spoken summary. NOT a patch: the relay has no
                     route that returns file contents, so neither does this.
  POST /utterance    a voice clip. Nothing else is writable.

So the dashboard has NO session actions: the relay cannot pause or stop a
session over HTTP, and a clip of "stop the car session" is something only a
voice can send. /api/workdash/action says so instead of pretending.

What the relay does NOT report, and the screen therefore does not show: a
session's title (only its project and its last spoken line), its branch,
progress steps, or the PR's CI state. "Gate passed / failing" is the relay's
own pre-push check on the tablet, not CI, and is labelled that way.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

STATE_DIR = os.path.expanduser(
    os.environ.get("XDG_STATE_HOME", "~/.local/state") + "/omacar")
CONFIG = os.path.join(os.path.expanduser(
    os.environ.get("XDG_CONFIG_HOME", "~/.config") + "/omacar"), "work.toml")
SIM = os.path.join(STATE_DIR, "workdash-sim.json")

DEFAULT_URL = "http://127.0.0.1:7570"
TIMEOUT = 2.0           # the page polls; a hung relay must not hang the server
SIM_STALE = 15.0        # a sim file nobody is refreshing is not a running sim
EVENTS_KEEP = 600
NOT_RUNNING = "Work relay not running — omacar work serve"

LIVE = ("starting", "running", "question", "gate", "pushing")
# The relay's states, grouped for the screen. "stopped" is the owner's own stop;
# it is shown as it is, under done, rather than being called a failure.
GROUPS = {
    "starting": "running", "running": "running", "gate": "running", "pushing": "running",
    "question": "waiting",
    "finished": "done", "stopped": "done",
    "failed": "failed", "stuck": "failed", "interrupted": "failed",
}
LABELS = {
    "starting": "Starting", "running": "Running", "gate": "Checking", "pushing": "Pushing",
    "question": "Waiting for you", "finished": "Done", "stopped": "Stopped",
    "failed": "Failed", "stuck": "Stuck", "interrupted": "Interrupted",
}
ORDER = {"waiting": 0, "running": 1, "failed": 2, "done": 3}


# ---- the relay, over loopback ---------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A relay never redirects; following one could carry the token elsewhere."""
    def redirect_request(self, *a, **kw):
        return None


_opener = urllib.request.build_opener(NoRedirect)


class RelayDown(Exception):
    """The relay cannot be asked: not running, no token, or it said no."""


def _config():
    """(url, token_from_config) from work.toml; defaults when it is absent."""
    url, tok, host = DEFAULT_URL, "", "tablet"
    try:
        import tomllib
        with open(CONFIG, "rb") as f:
            raw = tomllib.load(f)
        host = str(raw.get("host", "tablet"))
        url = str(raw.get("relay_url", DEFAULT_URL))
        tok = str(raw.get("relay_token", ""))
    except (ImportError, OSError, ValueError):
        pass
    if not url.startswith("http://"):
        url = DEFAULT_URL
    return url.rstrip("/"), (tok if host == "box" else "")


def read_token():
    """The relay's token: work.toml's on a box-hosted relay, else the file the
    tablet's relay wrote. "" when there is none (then nothing is sent)."""
    url, tok = _config()
    if tok:
        return tok
    try:
        with open(os.path.join(STATE_DIR, "work", "token"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _get(path, base=None, token=None):
    url, _ = _config()
    base = (base or url).rstrip("/")
    token = read_token() if token is None else token
    if not token:
        raise RelayDown("no relay token")        # nothing is sent without one
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
    try:
        with _opener.open(req, timeout=TIMEOUT) as r:
            body = r.read(2_000_000)
    except urllib.error.HTTPError as e:
        raise RelayDown(f"relay said {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise RelayDown("relay unreachable") from None
    try:
        out = json.loads(body)
    except ValueError:
        raise RelayDown("relay answered nonsense") from None
    if not isinstance(out, (dict, list)):
        raise RelayDown("relay answered nonsense")
    return out


# ---- shaping: pure, no clock, no network -----------------------------------------

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v, cap=240):
    return v.strip()[:cap] if isinstance(v, str) else ""


def shape(health, events, now, source="relay"):
    """The relay's /health and /events, as the screen reads them.

    Reports only what the relay said. A session with no `started` event has
    no elapsed time (None), never zero; a session with no PR has pr None."""
    health = health if isinstance(health, dict) else {}
    by = {}
    for e in events if isinstance(events, list) else []:
        if isinstance(e, dict) and isinstance(e.get("session"), str):
            by.setdefault(e["session"], []).append(e)
    rows, counts = [], {"running": 0, "waiting": 0, "done": 0, "failed": 0}
    for s in health.get("sessions") or []:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        state = s.get("state") if s.get("state") in GROUPS else "running"
        group = GROUPS[state]
        evs = by.get(s["id"], [])
        started = next((_num(e.get("at")) for e in evs if e.get("kind") == "started"), None)
        last_at = max([_num(e.get("at")) or 0 for e in evs], default=0) or None
        if started is None:
            elapsed = None
        elif state in LIVE:
            elapsed = max(0.0, now - started)
        else:
            elapsed = max(0.0, (last_at or now) - started)
        question = next((_text(e.get("question") or e.get("text")) for e in reversed(evs)
                         if e.get("kind") == "question"), "")
        gate = next(({"pr_opened": "passed", "gate_failed": "failing"}[e["kind"]]
                     for e in reversed(evs) if e.get("kind") in ("pr_opened", "gate_failed")), None)
        n, repo = s.get("pr"), _text(s.get("repo"), 120)
        pr = None
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            pr = {"number": n, "repo": repo or None,
                  "url": f"https://github.com/{repo}/pull/{n}" if "/" in repo else None,
                  "gate": gate}
        rows.append({
            "id": s["id"], "project": _text(s.get("project"), 80) or "session",
            "repo": repo or None, "branch": _text(s.get("branch"), 120) or None,
            "state": state, "group": group, "label": LABELS[state],
            "milestone": _text(s.get("line")) or None,
            "question": question or None,
            "started_at": started, "elapsed_s": elapsed, "pr": pr,
            "hasDiff": bool(pr or s.get("imported")),
        })
        counts[group] += 1
    rows.sort(key=lambda r: (ORDER[r["group"]], -(r["started_at"] or 0)))
    waiting = next((r for r in rows if r["group"] == "waiting"), None)
    asking = health.get("asking")
    if isinstance(asking, str):                       # the relay's own choice wins
        waiting = next((r for r in rows if r["id"] == asking), waiting)
    return {
        "present": True, "source": source, "simulated": source == "sim",
        "while_moving": health.get("while_moving") is True,
        "host": _text(health.get("host"), 12) or None,
        "today": _num(health.get("today")), "cap": _num(health.get("cap")),
        "counts": counts, "sessions": rows,
        "waiting": ({"id": waiting["id"], "project": waiting["project"],
                     "question": waiting["question"]} if waiting else None),
        "actions": [],            # the relay offers none over HTTP; see the docstring
        "at": now,
    }


def absent(reason=NOT_RUNNING, detail=None):
    return {"present": False, "source": "none", "simulated": False,
            "error": reason, "detail": detail, "sessions": [], "actions": [],
            "counts": {"running": 0, "waiting": 0, "done": 0, "failed": 0},
            "waiting": None, "while_moving": False}


# ---- what the API serves -----------------------------------------------------------

def _sim():
    """The simulator's fixture while something is refreshing it, else None."""
    try:
        with open(SIM, encoding="utf-8") as f:
            doc = json.load(f)
        if time.time() - float(doc.get("at", 0)) <= SIM_STALE:
            return doc
    except (OSError, ValueError, TypeError):
        pass
    return None


def read_public(now=None, get=_get):
    """/api/workdash. The real relay if it answers; else a live simulator,
    labelled Simulated; else a plain "not running" -- never invented sessions."""
    now = time.time() if now is None else now
    try:
        health = get("/health")
        try:
            ev = get("/events?after=0").get("events") or []
        except (RelayDown, AttributeError):
            ev = []                       # the log is garnish; the lamps still work
        return shape(health, ev[-EVENTS_KEEP:], now)
    except RelayDown as e:
        why = str(e)
    sim = _sim()
    if sim:
        # Event times are absolute (written at the fixture's `at`), so the
        # elapsed times keep advancing between the simulator's rewrites.
        evs = [e for e in sim.get("events", []) if isinstance(e, dict)]
        return shape(sim.get("health"), evs, now, "sim")
    return absent(NOT_RUNNING, why)


def read_diff(sid, get=_get):
    """/api/workdash/diff?id=: the relay's {stat, summary}, or why not."""
    if not isinstance(sid, str) or not sid or len(sid) > 32 or not all(
            c in "0123456789abcdef" for c in sid):
        return 400, {"error": "that is not a session id"}
    try:
        got = get("/diff/" + sid)
    except RelayDown as e:
        sim = _sim()
        if sim and sid in (sim.get("diffs") or {}):
            return 200, dict(sim["diffs"][sid], simulated=True)
        msg = str(e)
        return (404, {"error": "no diff for that session"}) if msg == "relay said 404" \
            else (503, {"error": NOT_RUNNING, "detail": msg})
    if not isinstance(got, dict):
        return 502, {"error": "relay answered nonsense"}
    return 200, {"stat": _text(got.get("stat"), 20000), "summary": _text(got.get("summary"), 600) or None,
                 "simulated": False}


def act(data):
    """/api/workdash/action. The relay has no session actions to forward."""
    return 400, {"error": "the Work relay offers no session actions over HTTP "
                          "(pausing or stopping is done by voice); nothing was sent",
                 "actions": []}


# ---- the simulator -------------------------------------------------------------------

SIM_STAT = """ lib/fastbaud.py          | 38 +++++++++++++++++++++++++++-----
 test/fastbaud_test.py    | 61 +++++++++++++++++++++++++++++++++++++++++
 doc/obd-timeouts.md      |  9 ++++++
 3 files changed, 101 insertions(+), 7 deletions(-)"""


def sim_doc(now=None, t0=None, while_moving=False):
    """A believable relay: three sessions, three different states. Offsets in
    `events` are absolute times taken from `at`."""
    now = time.time() if now is None else now
    t0 = now if t0 is None else t0          # when the simulated sessions began
    ev = lambda sid, p, kind, ago, **kw: dict(  # noqa: E731
        kind=kind, session=sid, project=p, at=t0 - ago, **kw)
    return {
        "at": now, "simulated": True,
        "health": {
            "host": "tablet", "while_moving": while_moving, "asking": "a1b2c3d4", "parked": True,
            "quiet": [21, 7], "today": 3, "cap": 6,
            "sessions": [
                {"id": "a1b2c3d4", "project": "omacar", "state": "question",
                 "pr": None, "repo": "omauilabs/omacar",
                 "line": "omacar has a question: keep the old timeout as a fallback?"},
                {"id": "5e6f7a8b", "project": "littleprints", "state": "running",
                 "pr": None, "repo": "frogbark/littleprints",
                 "line": "littleprints session started."},
                {"id": "9c0d1e2f", "project": "finances-tracker", "state": "finished",
                 "pr": 41, "repo": "frogbark/finances-tracker",
                 "line": "finances-tracker: gate passed, draft PR 41 opened."},
            ]},
        "events": [
            ev("a1b2c3d4", "omacar", "started", 1520),
            ev("a1b2c3d4", "omacar", "question", 95,
               question="keep the old timeout as a fallback?"),
            ev("5e6f7a8b", "littleprints", "started", 410),
            ev("9c0d1e2f", "finances-tracker", "started", 3300),
            ev("9c0d1e2f", "finances-tracker", "pr_opened", 640),
            ev("9c0d1e2f", "finances-tracker", "finished", 600),
        ],
        "diffs": {"9c0d1e2f": {"stat": SIM_STAT,
                               "summary": "Reads the fast-baud timeout from config instead of a constant, "
                                          "and adds a test for the retry path."}},
    }


def sim(seconds=None, while_moving=False):
    """Rewrite the fixture every 2 s so it stays fresh; Ctrl-C ends it."""
    os.makedirs(STATE_DIR, exist_ok=True)
    start = time.time()
    try:
        while seconds is None or time.time() - start < seconds:
            tmp = SIM + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sim_doc(t0=start, while_moving=while_moving), f)
            os.replace(tmp, SIM)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.unlink(SIM)
        except OSError:
            pass
    return 0


def main(argv):
    what = argv[0] if argv else "status"
    if what in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if what == "status":
        print(json.dumps(read_public(), indent=2))
        return 0
    if what == "sim":
        rest = [a for a in argv[1:] if a != "--while-moving"]
        sim(float(rest[0]) if rest else None, "--while-moving" in argv)
        return 0
    print(f"  omacar workdash: no such thing as '{what}'.\n  status | sim [s] [--while-moving]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
