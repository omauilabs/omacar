#!/usr/bin/env python3
"""The back-seat screen (lib/rear.py, serve.py --rear, share/rear.html).

The things that must hold:
  * a rear token reaches an explicit allow-list of routes and NOTHING else --
    every other route the app has, every file under share/, and every odd path
    answers 403 (and with no token or the wrong one, 401);
  * the music route does not exist until the owner turns it on;
  * a note to the driver is rate-limited, never sent while an alert is up, and
    is a fixed phrase, never typed words;
  * "How long?" is an ETA only when navigation said so a moment ago, otherwise
    the time driven, otherwise nothing -- never a guess.

Real server, scratch folders, a held clock. No car, no network beyond loopback.
"""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

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


def head(t):
    print(f"\n  {t}")


class Fake:
    """Stand-ins for the four things the rear screen reads."""
    def __init__(self, nav=None, started=None, sim=False, cabin=None, alert=False, imperial=False):
        self._nav, self._started, self._sim = nav, started, sim
        self._cabin, self._alert, self._imp = cabin, alert, imperial

    def nav(self): return self._nav
    def drive(self, now): return self._started, self._sim
    def cabin(self): return self._cabin
    def unit(self): return "imperial" if self._imp else "metric"
    def alert(self, now):
        if self._alert == "boom":
            raise RuntimeError("unreadable")
        return self._alert


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    scratch = tempfile.mkdtemp(prefix="omacar-rear-")
    env = dict(os.environ, HOME=scratch, XDG_STATE_HOME=scratch + "/state",
               XDG_CONFIG_HOME=scratch + "/config", XDG_DATA_HOME=scratch + "/data",
               XDG_CACHE_HOME=scratch + "/cache")
    os.environ.update(env)
    import rear
    srv = None
    try:
        # ---------------------------------------------------------------- the allow-list, as a function
        head("the allow-list")
        off, on = {"music_control": False}, {"music_control": True}
        check("the state document is a GET", rear.route_allowed("GET", "/api/rear/state", off))
        check("ask is a POST", rear.route_allowed("POST", "/api/rear/ask", off))
        check("the page and its three files are GETs",
              all(rear.route_allowed("GET", p, off) for p in
                  ("/rear.html", "/css/rear.css", "/js/rear.js", "/js/rearlogic.js")))
        check("state is not a POST, ask is not a GET",
              not rear.route_allowed("POST", "/api/rear/state", off)
              and not rear.route_allowed("GET", "/api/rear/ask", off))
        check("music is refused while it is off", not rear.route_allowed("POST", "/api/rear/music", off))
        check("music is allowed once the owner turns it on", rear.route_allowed("POST", "/api/rear/music", on))
        check("music is never a GET", not rear.route_allowed("GET", "/api/rear/music", on))
        check("PUT and DELETE are nothing", not any(rear.route_allowed(m, "/api/rear/ask", on) for m in ("PUT", "DELETE", "PATCH")))
        check("no wildcard: a path under an allowed one is not allowed",
              not rear.route_allowed("GET", "/api/rear/state/x", on) and not rear.route_allowed("GET", "/api/rear/", on))
        check("the front screen's routes are not on the rear list",
              not any(rear.route_allowed(m, p, on) for m in ("GET", "POST") for p in
                      ("/api/rear", "/api/rear/dismiss", "/api/rear/nowplaying", "/api/rear/music/done")))

        # ---------------------------------------------------------------- the token
        head("the token")
        check("nothing is paired at first", rear.token() is None and not rear.valid("x"))
        t = rear.pair()
        check("pairing makes a token and keeps it", t and rear.pair() == t and rear.valid(t))
        check("the token file is private", oct(os.stat(os.path.join(scratch, "state/omacar/rear-token")).st_mode & 0o777) == "0o600")
        t2 = rear.pair(new=True)
        check("--new replaces it, and the old one stops working", t2 != t and not rear.valid(t) and rear.valid(t2))
        check("a Bearer, ?k= or the cookie each carry it",
              rear.token_from("Bearer " + t2, "", "") == t2 and rear.token_from(None, "k=" + t2, "") == t2
              and rear.token_from(None, "", f"a=b; {rear.COOKIE}={t2}") == t2)
        check("a stale cookie does not hide a good ?k=", rear.token_from(None, "k=" + t2, f"{rear.COOKIE}=old") == t2)
        check("a wrong one carries nothing", rear.token_from("Bearer nope", "k=nope", f"{rear.COOKIE}=nope") is None)
        check("revoke removes it", rear.revoke() and rear.token() is None and not rear.valid(t2))
        check("revoking again says nothing was there", rear.revoke() is False)
        t = rear.pair()

        # ---------------------------------------------------------------- the real server
        head("a real --rear server: every other route is 403")
        port = free_port()
        srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "lib/serve.py"), str(port),
                                os.path.join(ROOT, "share"), "--host", "127.0.0.1", "--rear"],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        def req(method, path, token=None, body=None, hdr=None, cookie=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h = dict(hdr or {})
            if token:
                h["Authorization"] = "Bearer " + token
            if cookie:
                h["Cookie"] = cookie
            if body is not None:
                h["Content-Type"] = "application/json"
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            data = r.read()
            out = (r.status, dict(r.getheaders()), data)
            c.close()
            return out

        # Every /api path anything in the app answers or calls.
        paths = set()
        for fn in os.listdir(os.path.join(ROOT, "lib")):
            if fn.endswith(".py"):
                with open(os.path.join(ROOT, "lib", fn), encoding="utf-8") as f:
                    paths |= set(re.findall(r'"(/api/[A-Za-z0-9_/\-]+)"', f.read()))
        for fn in os.listdir(os.path.join(ROOT, "share/js")):
            if fn.endswith(".js"):
                with open(os.path.join(ROOT, "share/js", fn), encoding="utf-8") as f:
                    paths |= set(re.findall(r'"(/api/[A-Za-z0-9_/\-]+)"', f.read()))
        paths |= {"/api/cams/front/live", "/api/cams/clip/front/x.mp4", "/api/phone/video", "/api/roadcams/img",
                  "/api/rear", "/api/rear/dismiss", "/api/rear/nowplaying", "/api/rear/music/done", "/api/rear/state/x",
                  "/api/", "/api", "/api/snapshot?x=1", "/report.html", "/export.csv", "/.mark", "/app.html",
                  "/index.html", "/../lib/rear.py", "/%2e%2e/lib/rear.py", "/js/../app.html", "//app.html",
                  "/REAR.HTML", "/rear.html/", "/rear.htm", "/api/REAR/state", "/css/app.css", "/lib/api.py"}
        # Every file under share/ that is not on the rear list.
        for d, _, files in os.walk(os.path.join(ROOT, "share")):
            for f in files:
                p = "/" + os.path.relpath(os.path.join(d, f), os.path.join(ROOT, "share")).replace(os.sep, "/")
                if p not in rear.REAR_STATIC:
                    paths.add(p)
        allowed_get = set(rear.REAR_GET)
        allowed_post = set(rear.REAR_POST)
        bad = []
        n = 0
        for p in sorted(paths):
            bare = p.split("?")[0]
            if bare in allowed_get and p == bare:
                continue
            n += 1
            st = req("GET", p, token=t)[0]
            if st != 403:
                bad.append(("GET", p, st))
        for p in sorted(paths):
            bare = p.split("?")[0]
            if bare in allowed_post:
                continue
            st = req("POST", p, token=t, body="{}")[0]
            if st != 403:
                bad.append(("POST", p, st))
        # the music route, still off
        st = req("POST", "/api/rear/music", token=t, body='{"cmd":"next"}')[0]
        if st != 403:
            bad.append(("POST", "/api/rear/music (off)", st))
        for p in sorted(allowed_get):
            if p != "/api/rear/state":
                continue
        for m in ("GET",):
            st = req(m, "/api/rear/ask", token=t)[0]
            if st != 403:
                bad.append((m, "/api/rear/ask", st))
        st = req("POST", "/api/rear/state", token=t, body="{}")[0]
        if st != 403:
            bad.append(("POST", "/api/rear/state", st))
        check(f"{n} GET routes and files, and every POST, outside the list all answer 403", not bad)
        for b in bad[:12]:
            print("          ", b)
        check("the sweep really covered the app (api routes, camera streams, files)",
              n > 200 and "/api/clear" in paths and "/api/cams" in paths and "/app.html" in paths)
        for sensitive in ("/api/live", "/api/cams/front/live", "/api/history", "/api/trips", "/api/vehicles",
                          "/api/nursery", "/api/drowsy", "/api/roadcams", "/api/snapshot", "/api/env", "/api/fridge"):
            check(f"{sensitive} is 403 with a valid rear token", req("GET", sensitive, token=t)[0] == 403)

        head("authentication comes first")
        check("no token: 401 on the page", req("GET", "/rear.html")[0] == 401)
        check("no token: 401 on a forbidden route, not 403", req("GET", "/api/live")[0] == 401)
        check("a wrong token: 401", req("GET", "/api/rear/state", token="nope")[0] == 401)
        cockpit_token = "cockpit-token-not-the-rear-one"
        check("the cockpit's token does not open this door", req("GET", "/api/rear/state", token=cockpit_token)[0] == 401)
        st, hd, body = req("GET", "/rear.html", token=t)
        check("the page opens with the token", st == 200 and b"How long?" in body)
        check("and sets an HttpOnly, SameSite cookie for its own files",
              "HttpOnly" in hd.get("Set-Cookie", "") and "SameSite=Strict" in hd.get("Set-Cookie", "") and rear.COOKIE in hd.get("Set-Cookie", ""))
        ck = hd["Set-Cookie"].split(";")[0]
        check("the cookie opens its stylesheet and script",
              all(req("GET", p, cookie=ck)[0] == 200 for p in ("/css/rear.css", "/js/rear.js", "/js/rearlogic.js")))
        check("a ?k= opens the page too", req("GET", "/rear.html?k=" + t)[0] == 200)
        check("HEAD answers like GET without a body", req("HEAD", "/rear.html", token=t)[0] == 200 and req("HEAD", "/api/live", token=t)[0] == 403)
        check("the page itself does not carry the token", t.encode() not in body)
        st, _, body = req("GET", "/api/rear/state", token=t)
        doc = json.loads(body)
        check("the state is served", st == 200 and set(doc) == {"at", "how_long", "music", "control", "cabin", "ask"})
        check("a cross-site POST is refused", req("POST", "/api/rear/ask", token=t, body='{"what":"break"}',
                                                  hdr={"Sec-Fetch-Site": "cross-site"})[0] == 403)
        check("an oversized body is refused", req("POST", "/api/rear/ask", token=t, body="x" * 5000)[0] == 413)
        check("revoking stops a paired screen at its next request",
              (rear.revoke(), req("GET", "/api/rear/state", token=t)[0])[1] == 401)
        t = rear.pair()

        head("music: off until the owner says so")
        rear.publish_music({"playing": True, "title": "Wheels on the Bus", "artist": "Simulated",
                            "can_skip": True, "source": "Simulated"})
        doc = json.loads(req("GET", "/api/rear/state", token=t)[2])
        check("the song shows, with no skip keys", doc["music"]["title"] == "Wheels on the Bus"
              and doc["music"]["can_skip"] is False and doc["control"]["music"] is False)
        check("the skip route 403s", req("POST", "/api/rear/music", token=t, body='{"cmd":"next"}')[0] == 403)
        rear.set_music_control(True)
        doc = json.loads(req("GET", "/api/rear/state", token=t)[2])
        check("turned on: skip keys appear", doc["music"]["can_skip"] is True and doc["control"]["music"] is True)
        check("a skip is accepted", req("POST", "/api/rear/music", token=t, body='{"cmd":"next"}')[0] == 200)
        check("the front screen gets it once", (rear.pending_music_command() or {}).get("cmd") == "next")
        check("two in a second: the second waits", req("POST", "/api/rear/music", token=t, body='{"cmd":"next"}')[0] == 429)
        check("anything but next/previous is refused", (time.sleep(1.05), req("POST", "/api/rear/music", token=t, body='{"cmd":"rm -rf"}')[0])[1] == 400)
        c = rear.pending_music_command()
        check("done clears it", rear.finish_music_command(c["id"]) and rear.pending_music_command() is None)
        rear.publish_music({"playing": True, "title": "A live stream", "can_skip": False})
        check("a player that cannot skip is not asked to", req("POST", "/api/rear/music", token=t, body='{"cmd":"next"}')[0] == 409)
        rear.set_music_control(False)
        rear.publish_music({"playing": True, "title": "x", "can_skip": True})
        check("turning it off again takes the route away", req("POST", "/api/rear/music", token=t, body='{"cmd":"next"}')[0] == 403
              and rear.pending_music_command() is None)
        check("a config of 'yes' is not true", (open(os.path.join(scratch, "config/omacar/rear.json"), "w").write('{"music_control": "yes"}'),
                                               rear.config()["music_control"])[1] is False)

        # ---------------------------------------------------------------- the note
        head("ask the driver")
        now = 1_000_000.0
        clear = Fake()
        check("a choice is a fixed list", set(rear.WANTS) == {"break", "snack", "drink", "bathroom"})
        check("typed words are refused", rear.ask("a break please", now, clear)[0] == 400
              and rear.ask({"x": 1}, now, clear)[0] == 400 and rear.ask(None, now, clear)[0] == 400)
        check("a note is not on the front screen yet", rear.note_for_front(now) is None)
        st, body = rear.ask("break", now, clear)
        check("the first goes", st == 200 and body["wait"] == rear.ASK_EVERY)
        n1 = rear.note_for_front(now + 1)
        check("the front screen gets 'a break'", n1 and n1["label"] == "a break")
        st, body = rear.ask("snack", now + 10, clear)
        check("a second 10 s later is held (429)", st == 429 and 100 <= body["wait"] <= 110)
        check("and does not replace the first", rear.note_for_front(now + 11)["label"] == "a break")
        check("119 s later it is still held", rear.ask("snack", now + 119, clear)[0] == 429)
        check("120 s later it goes", rear.ask("snack", now + 120, clear)[0] == 200)
        check("the new one replaced the old", rear.note_for_front(now + 121)["label"] == "a snack")
        rear.dismiss_note(rear.note_for_front(now + 121)["id"])
        check("dismissed: gone", rear.note_for_front(now + 122) is None)
        check("dismissing the wrong note does nothing", rear.dismiss_note(999) is False)
        rear.ask("drink", now + 300, clear)
        check("an unread note lapses by itself", rear.note_for_front(now + 300 + rear.NOTE_LIFE + 1) is None)
        busy = Fake(alert=True)
        st, body = rear.ask("break", now + 1000, busy)
        check("never while an alert is active (409)", st == 409 and body.get("busy"))
        check("and nothing was queued", rear.note_for_front(now + 1001) is None)
        check("a blocked ask does not start the clock", rear.ask("break", now + 1001, clear)[0] == 200)
        check("an alert source that cannot be read counts as an alert",
              rear.ask("break", now + 2000, Fake(alert="boom"))[0] == 409)
        check("the state tells the page it is busy, with no countdown", rear.ask_status(now + 3000, busy)["busy"]
              and not rear.ask_status(now + 3000, busy)["ok"])
        check("the front screen is told about an alert too",
              (rear.ask("break", now + 5000, clear), rear.front_get(now + 5001))[1]["note"] is not None)

        # ---------------------------------------------------------------- how long
        head("How long? is never invented")
        T = 5_000_000.0
        hl = rear.how_long
        check("no navigation, no drive: unknown, and no minutes at all",
              hl(T, None, None) == {"kind": "unknown", "simulated": False})
        check("no navigation: the time driven", hl(T, None, T - 25 * 60)["kind"] == "elapsed"
              and hl(T, None, T - 25 * 60)["minutes"] == 25)
        check("navigation's ETA, a moment old, wins",
              hl(T, {"at": T - 5, "eta_at": T + 12 * 60}, T - 3600) == {"kind": "eta", "minutes": 12, "simulated": False})
        check("rounded up to the minute", hl(T, {"at": T, "eta_at": T + 61}, None)["minutes"] == 2)
        check("an ETA two minutes old is not trusted: back to the time driven",
              hl(T, {"at": T - 121, "eta_at": T + 600}, T - 600)["kind"] == "elapsed")
        check("a stale ETA and no drive is unknown", hl(T, {"at": T - 500, "eta_at": T + 600}, None)["kind"] == "unknown")
        check("an ETA with no timestamp is not trusted", hl(T, {"eta_at": T + 600}, T - 60)["kind"] == "elapsed")
        check("an ETA that is not a number is ignored", all(
            hl(T, {"at": T, "eta_at": v}, T - 60)["kind"] == "elapsed" for v in (True, "soon", None, float("nan"), float("inf"))))
        check("an ETA a day away is ignored", hl(T, {"at": T, "eta_at": T + 3 * 86400}, None)["kind"] == "unknown")
        check("arrived is 0 minutes, never negative", hl(T, {"at": T, "eta_at": T - 50}, None)["minutes"] == 0)
        check("a simulated drive says so", hl(T, None, T - 600, True)["simulated"] is True)
        check("drive_start: a steady run", rear.drive_start([T - i * 5 for i in range(121)], T + 1) == T - 600)
        check("drive_start: a long hole ends the drive", rear.drive_start([T, T - 1, T - 1000, T - 1001], T) == T - 1)
        check("drive_start: not driving now", rear.drive_start([T - 400, T - 401], T) is None)
        check("drive_start: no samples", rear.drive_start([], T) is None)

        # ---------------------------------------------------------------- the state document
        head("the state document")
        s = rear.state_public(T, Fake(started=T - 3000), {"music_control": False})
        check("no navigation: it is the elapsed time", s["how_long"]["kind"] == "elapsed" and s["how_long"]["minutes"] == 50)
        s = rear.state_public(T, Fake(nav={"at": T, "eta_at": T + 900}, started=T - 3000), {"music_control": False})
        check("with navigation: the ETA", s["how_long"] == {"kind": "eta", "minutes": 15, "simulated": False})
        s = rear.state_public(T, Fake(), {"music_control": False})
        check("nothing known: unknown, no cabin card", s["how_long"]["kind"] == "unknown" and s["cabin"] is None)
        s = rear.state_public(T, Fake(cabin=(22.0, False), imperial=True), {"music_control": False})
        check("a cabin sensor: degrees in the owner's units, and a word",
              s["cabin"] == {"degrees": 72, "unit": "F", "word": "just right", "simulated": False})
        s = rear.state_public(T, Fake(cabin=(31.0, True)), {"music_control": False})
        check("metric, hot, simulated", s["cabin"] == {"degrees": 31, "unit": "C", "word": "hot", "simulated": True})
        check("comfort words", [rear.comfort(x) for x in (10, 18, 22, 27, 33, None)] == ["chilly", "cool", "just right", "warm", "hot", None])

        class Broken(Fake):
            def drive(self, now): raise OSError("db gone")
            def nav(self): raise ValueError("bad json")
            def cabin(self): raise OSError("port gone")
        s = rear.state_public(T, Broken(), {"music_control": False})
        check("a source that fails is 'unknown', not a crash or a zero", s["how_long"]["kind"] == "unknown" and s["cabin"] is None)
        def keys(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield k
                    yield from keys(v)
            elif isinstance(o, list):
                for v in o:
                    yield from keys(v)
        ks = set(keys(rear.state_public(T, Fake(started=T - 60, cabin=(21.0, False)), {"music_control": True})))
        check("every field it carries is on the list (no name, place, speed, vehicle or trip)",
              ks <= {"at", "how_long", "kind", "minutes", "simulated", "music", "state", "can_skip", "title", "artist",
                     "control", "cabin", "degrees", "unit", "word", "ask", "ok", "wait", "busy", "choices", "id", "label"})
        rear.publish_music({"playing": True, "title": "T" * 500, "artist": "A\n\nB", "can_skip": True}, T)
        m = rear.music(T + 1, {"music_control": True})
        check("a title is clipped and flattened", len(m["title"]) == 90 and "\n" not in m["artist"])
        check("a player that stopped publishing is 'off'", rear.music(T + 100, {"music_control": True})["state"] == "off")

        # ---------------------------------------------------------------- the front screen's side
        head("the front screen's routes")
        import api
        st, doc = api.handle_get("/api/rear", "")
        check("GET /api/rear is the front's", st == 200 and set(doc) == {"note", "alert", "music_cmd", "paired"})
        st, out = api.handle_post("/api/rear/nowplaying", json.dumps({"playing": True, "title": "Song", "can_skip": False}))
        check("the front publishes what is playing", st == 200 and rear.music(time.time(), {})["title"] == "Song")
        check("a body that is not an object is refused", api.handle_post("/api/rear/dismiss", "[1]")[0] == 400)
        check("a body that is not JSON is refused", api.handle_post("/api/rear/dismiss", "{")[0] == 400)
        import serve
        check("none of them is a cockpit write", not any(p.startswith("/api/rear") for p in serve.COCKPIT_WRITES))
        check("the demo answers them with stubs", all(serve.demo_route(p) == "stub" for p in
              ("/api/rear", "/api/rear/dismiss", "/api/rear/nowplaying", "/api/rear/music/done")))

        # ---------------------------------------------------------------- the page itself
        head("the page")
        with open(os.path.join(ROOT, "share/rear.html"), encoding="utf-8") as f:
            page = f.read()
        with open(os.path.join(ROOT, "share/js/rear.js"), encoding="utf-8") as f:
            js = f.read()
        check("it names no camera, diagnostic, Work, settings, location or history route",
              not re.search(r'/api/(?!rear/)', page + js))
        check("it calls only the three rear routes", set(re.findall(r'"(/api/[a-z/]+)"', js)) <=
              {"/api/rear/state", "/api/rear/ask", "/api/rear/music"})
        check("it loads only its own files", set(re.findall(r'(?:href|src)="([^"]+)"', page)) <=
              {"icon.svg", "css/rear.css", "js/rear.js"})
        check("it has no sound", not re.search(r"<audio|<video|new Audio|AudioContext|\.play\(", page + js))
        check("nothing it loads is off the car", not re.search(r"https?://", page + js))
    finally:
        if srv is not None:
            srv.terminate()
            try:
                srv.wait(5)
            except subprocess.TimeoutExpired:
                srv.kill()
        shutil.rmtree(scratch, ignore_errors=True)
    print()
    if fails:
        print(f"  {fails} FAILED")
        return 1
    print("  rear screen: every check holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
