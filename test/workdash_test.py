#!/usr/bin/env python3
"""The Work dashboard's client (lib/workclient.py) and its /api/workdash routes.

A tiny FAKE RELAY (stdlib http.server) stands in for `omacar work serve`: it
speaks only the routes lib/workrelay.py really has (/health, /events, /diff/<id>)
and demands the bearer token, as the real one does. Nothing here touches the
real relay, state or config: everything runs in a scratch HOME.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

fails = 0


def check(msg, cond):
    global fails
    if cond:
        print(f"    ok  {msg}")
    else:
        fails += 1
        print(f"    FAIL  {msg}")


TOKEN = "t0ken-for-tests"
NOW = 1_000_000.0


def fake_relay(health, events, diffs, seen):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                return self._j({"error": "not allowed"}, 403)
            if self.path == "/health":
                return self._j(dict(health, ok=True))
            if self.path.startswith("/events"):
                return self._j({"events": events})
            if self.path.startswith("/diff/"):
                d = diffs.get(self.path[6:])
                return self._j(d, 200) if d else self._j({"error": "no such diff"}, 404)
            return self._j({"error": "no such endpoint"}, 404)

        def _j(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-workdash-")
    os.environ["XDG_STATE_HOME"] = os.path.join(scratch, "state")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
    os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "omacar", "work"))
    os.makedirs(os.path.join(os.environ["XDG_CONFIG_HOME"], "omacar"))
    import workclient as wc
    import api
    try:
        doc = wc.sim_doc(NOW)

        print("\n  shaping (pure)")
        agg = wc.shape(doc["health"], doc["events"], NOW)
        check("three sessions, one in each of three states",
              sorted(s["group"] for s in agg["sessions"]) == ["done", "running", "waiting"])
        check("counts by group", agg["counts"] == {"running": 1, "waiting": 1, "done": 1, "failed": 0})
        check("waiting first, then running, then done",
              [s["group"] for s in agg["sessions"]] == ["waiting", "running", "done"])
        w = agg["sessions"][0]
        check("the question comes from the relay's question event",
              w["question"] == "keep the old timeout as a fallback?")
        check("the waiting block is the relay's `asking` session",
              agg["waiting"]["id"] == "a1b2c3d4" and agg["waiting"]["project"] == "omacar")
        check("elapsed of a live session is now - started", abs(w["elapsed_s"] - 1520) < 1)
        d = [s for s in agg["sessions"] if s["group"] == "done"][0]
        check("elapsed of a finished session stops at its last event", abs(d["elapsed_s"] - 2700) < 1)
        check("PR url built from the repo the relay reported",
              d["pr"]["url"] == "https://github.com/frogbark/finances-tracker/pull/41")
        check("the relay's pre-push gate is 'gate', never CI", d["pr"]["gate"] == "passed" and "ci" not in d["pr"])
        check("a session with no PR has none", agg["sessions"][1]["pr"] is None)
        check("no branch is invented", all(s["branch"] is None for s in agg["sessions"]))
        check("no actions are offered", agg["actions"] == [])
        check("while_moving is false unless the relay says true", agg["while_moving"] is False)
        h2 = dict(doc["health"], while_moving=True)
        check("while_moving true is carried", wc.shape(h2, [], NOW)["while_moving"] is True)
        check("while_moving 'yes' (not a bool) is not true",
              wc.shape(dict(doc["health"], while_moving="yes"), [], NOW)["while_moving"] is False)
        bare = wc.shape({"sessions": [{"id": "ab12", "state": "running"}]}, [], NOW)["sessions"][0]
        check("no started event means elapsed None, not 0", bare["elapsed_s"] is None)
        check("unknown state does not crash", wc.shape({"sessions": [{"id": "ab12", "state": "weird"}]}, [], NOW)["counts"]["running"] == 1)
        check("junk rows are skipped", wc.shape({"sessions": [None, 3, {"id": 7}]}, "nope", NOW)["sessions"] == [])
        failed = wc.shape({"sessions": [{"id": "ab12", "state": "stuck"}]}, [], NOW)
        check("stuck counts as failed", failed["counts"]["failed"] == 1)

        print("\n  relay down")
        # Nothing listens here; work.toml points at it and a token exists.
        cfg = os.path.join(os.environ["XDG_CONFIG_HOME"], "omacar", "work.toml")
        tokf = os.path.join(os.environ["XDG_STATE_HOME"], "omacar", "work", "token")
        wc.CONFIG, wc.STATE_DIR = cfg, os.path.join(os.environ["XDG_STATE_HOME"], "omacar")
        wc.SIM = os.path.join(wc.STATE_DIR, "workdash-sim.json")
        with open(cfg, "w") as f:
            f.write('relay_url = "http://127.0.0.1:1"\n')
        with open(tokf, "w") as f:
            f.write(TOKEN)
        out = wc.read_public(NOW)
        check("not running says exactly what to run", out["error"] == "Work relay not running — omacar work serve")
        check("not running shows no sessions and is not present",
              out["present"] is False and out["sessions"] == [] and out["simulated"] is False)
        os.unlink(tokf)
        seen = []
        srv = fake_relay(doc["health"], doc["events"], doc["diffs"], seen)
        port = srv.server_address[1]
        with open(cfg, "w") as f:
            f.write(f'relay_url = "http://127.0.0.1:{port}"\n')
        out = wc.read_public(NOW)
        check("no token: absent, and NOTHING is sent to the relay", not out["present"] and seen == [])

        print("\n  relay up")
        with open(tokf, "w") as f:
            f.write(TOKEN + "\n")
        out = wc.read_public(NOW)
        check("live relay: present, source relay, not simulated",
              out["present"] and out["source"] == "relay" and not out["simulated"])
        check("it sent the bearer token to /health and /events",
              {p.split("?")[0] for p, a in seen} == {"/health", "/events"} and all(a == "Bearer " + TOKEN for _, a in seen))
        check("three sessions arrive", len(out["sessions"]) == 3)
        with open(tokf, "w") as f:
            f.write("wrong")
        out = wc.read_public(NOW)
        check("a refused token reads as not running with the reason", not out["present"] and "403" in out["detail"])
        with open(tokf, "w") as f:
            f.write(TOKEN)

        print("\n  diff and action routes")
        st, body = api.handle_get("/api/workdash/diff", "id=9c0d1e2f")
        check("a diff passes through as {stat, summary}",
              st == 200 and "fastbaud" in body["stat"] and body["summary"].startswith("Reads") and body["simulated"] is False)
        st, body = api.handle_get("/api/workdash/diff", "id=ffffffff")
        check("an unknown session is 404", st == 404)
        st, body = api.handle_get("/api/workdash/diff", "id=../../etc/passwd")
        check("a path-shaped id is refused before any request",
              st == 400 and not any("passwd" in p for p, _ in seen))
        st, body = api.handle_post("/api/workdash/action", json.dumps({"action": "stop", "id": "a1b2c3d4"}))
        check("action: none are offered, nothing is sent to the relay",
              st == 400 and body["actions"] == [] and not any(p.startswith("/stop") for p, _ in seen))
        st, body = api.handle_get("/api/workdash", "")
        check("/api/workdash answers through the router", st == 200 and body["present"])
        srv.shutdown()
        srv.server_close()
        st, body = api.handle_get("/api/workdash/diff", "id=9c0d1e2f")
        check("a diff with the relay down is 503 'not running'", st == 503 and "not running" in body["error"])

        print("\n  simulator")
        with open(wc.SIM, "w") as f:
            json.dump(wc.sim_doc(time.time()), f)
        out = wc.read_public()
        check("sim fallback is labelled Simulated", out["simulated"] is True and out["source"] == "sim")
        check("sim shows three sessions, three states", sorted(s["group"] for s in out["sessions"]) == ["done", "running", "waiting"])
        check("sim elapsed keeps moving between rewrites",
              wc.read_public(time.time() + 10)["sessions"][0]["elapsed_s"] > out["sessions"][0]["elapsed_s"] + 8)
        st, body = api.handle_get("/api/workdash/diff", "id=9c0d1e2f")
        check("sim diff is served and says simulated", st == 200 and body["simulated"] is True)
        with open(wc.SIM, "w") as f:
            json.dump(wc.sim_doc(time.time() - 60), f)
        check("a stale sim file is not a running sim", wc.read_public()["present"] is False)
        with open(wc.SIM, "w") as f:
            f.write("{nope")
        check("a broken sim file is ignored", wc.read_public()["present"] is False)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print()
    print("workdash: all passed" if not fails else f"workdash: {fails} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
