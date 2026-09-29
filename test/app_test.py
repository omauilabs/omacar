#!/usr/bin/env python3
"""The app actually starts in a browser.

THE BUG THIS WOULD HAVE CAUGHT, AND DID NOT.

`share/js/main.js` called maskVin() and imported it under a different name.
Nothing in this suite noticed, because nothing in this suite had ever RUN the
application -- the tests check that every file an import names exists, that the
stylesheet declares its tokens, that the shaders compile. Every one of those
passed while the app was dead on arrival: a red card reading "OmaCar could not
start · maskVin is not defined", found by plugging a tablet into a car.

Existing checks are static and this class of fault is not. A name that is used
but never bound is perfectly good syntax; `node --check` passes it, an asset
test passes it, and it fails the moment the line executes -- which, for that
line, meant the moment a real vehicle appeared and the bar painted.

So this one starts the server, loads the page in a headless browser, and asks
two questions a person would ask: did anything throw, and is the application
on the screen.

SKIPPED LOUDLY WITHOUT A BROWSER, like the shader test without a compiler.
Chromium is not a dependency of this project -- the app is meant to run in
whatever the owner has -- so a machine without one says so and passes. A test
that cannot run is not a failure; a test that silently does nothing is.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARE = os.path.join(ROOT, "share")

fails = 0

# Appended to a COPY of app.html. It drives the store directly rather than
# faking a feed, because what is being measured is layout under a speed value,
# not the network.
# A RAW STRING: the probe is JavaScript, and `\d` in a plain Python string is
# an invalid escape that only survives by accident. Python has warned about it
# for years and has said it will stop accepting it.
GEOMETRY_PROBE = r"""
<script type="module">
import { store } from "./js/core.js";
window.__st = store;
</script>
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const rect = () => {
    const el = document.querySelector(".drive-exit");
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [Math.round(r.x * 10) / 10, Math.round(r.y * 10) / 10,
            Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10];
  };
  await wait(3500);
  // A READING TO COMPARE, BECAUSE THE DEFECT IS A DISAGREEMENT AND NOT A GAP.
  //
  // What this pair of checks was written to catch is one screen showing raw
  // Celsius while another shows Fahrenheit for the same instant. With no car
  // plugged in both screens correctly show a dash, `agree` requires digits,
  // and the check failed on every machine that was not sitting in a car --
  // which is every machine the suite normally runs on. A test that can only
  // pass with a vehicle attached is a test nobody can act on, so the probe
  // supplies the instant itself.
  //
  // The poller is silenced FIRST for the same reason it is silenced below: it
  // overwrites store.live every 250 ms, and an injected value that is gone
  // before it is read reports a fault that is not there.
  window.__st.refreshLive = async () => {};
  window.__st.live = Object.assign({}, window.__st.live || {}, {
    t: Date.now() / 1000, connected: true,
    values: Object.assign({}, (window.__st.live && window.__st.live.values) || {},
                          { COOLANT_TEMP: 89, SPEED: 0, RPM: 0 }) });
  window.__st.emit("live");
  await wait(400);
  // THE SCREEN THE TABLET PAINTS WHEN SOMEBODY GETS IN. Read before anything
  // navigates away from it: HOME is the arrival screen, and the coolant number
  // here used to be raw Celsius under a bare degree sign while two other
  // screens showed the same instant in Fahrenheit.
  // HOME'S COOLANT TILE. Value and unit are separate elements, for the reason
  // above: textContent of the whole tile would satisfy a unit regex by
  // accident.
  const cool = document.querySelector('.sig[data-reading="coolant"]');
  const vitalV = cool ? (cool.querySelector(".sig-v") || {}).textContent || "" : "";
  const vitalU = cool ? (cool.querySelector(".sig-u") || {}).textContent || "" : "";
  const vital = vitalV;
  location.hash = "#drive";
  await wait(2500);
  const tile = [...document.querySelectorAll(".drive-tile")]
    .map((el) => el.textContent.trim())
    .find((t) => t.includes("Coolant")) || "";
  const digits = (t) => (t.match(/-?\d+/) || [""])[0];
  const s = window.__st;
  // SILENCE THE FAST POLLER FIRST. It calls refreshLive() every 250 ms and
  // overwrites store.live with whatever the server says, so an injected speed
  // survived for less time than it took to measure it -- and the probe
  // reported "no movement" for a screen that was moving plenty. A test that
  // cannot fail is worse than no test, so this one was checked by putting the
  // old stylesheet back and watching it go red.
  s.refreshLive = async () => {};   // already silenced above; kept so this
                                    // block still reads as self-contained
  const at = async (kph) => {
    s.live = Object.assign({}, s.live || {}, {
      t: Date.now() / 1000, connected: true,
      values: Object.assign({}, (s.live && s.live.values) || {},
                            { SPEED: kph, RPM: 1800 }) });
    s.emit("live");
    await wait(220);
    return rect();
  };
  const out = { viewport: [innerWidth, innerHeight],
                homeCoolant: vital, homeUnit: vitalU, driveCoolant: tile,
                agree: !!digits(vital) && digits(vital) === digits(tile),
                unit: vitalU === "\u00b0F" || vitalU === "\u00b0C" };
  out.stopped = await at(0);
  out.rolling = await at(70);
  out.creep = [];
  for (const v of [3, 4, 3, 4, 3, 4]) out.creep.push(await at(v));
  document.title = "GEOM " + JSON.stringify(out);
})();
</script>
"""

# Appended to a second COPY of app.html, in its own run. This suite's own
# opening check loads app.html with no hash, and main.js's arrival screen is
# Home -- so until this probe existed, nothing in this file (or any other
# suite) had ever mounted share/js/views/vehicle.js. A renamed export, a
# selector that no longer matches a class the CSS still carries, any of the
# ordinary ways a view goes wrong on its way to the screen would have passed
# every check above: main.js's own try/catch around view.mount() turns a
# throwing view into a "That view failed to draw" card, not a blank page, and
# nothing here had ever looked. So this navigates to #vehicle for real and
# asks for content only a correctly-mounted screen produces -- the headline
# and one row per system, named -- not just that a container exists.
VEHICLE_PROBE = r"""
<script type="module">
import { ICONS } from "./js/icons.js";
window.__icons = ICONS;
</script>
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  await wait(3500);
  location.hash = "#vehicle";
  await wait(2000);
  const titleEl = document.querySelector(".vh-title");
  const rows = [...document.querySelectorAll(".vh-sys-row")];
  const ico = document.querySelector(".vh-icon");
  const insight = document.querySelector(".vh-insight");
  const out = {
    headline: titleEl ? titleEl.textContent : null,
    rowCount: rows.length,
    rows: rows.map((r) => r.textContent.replace(/\s+/g, " ").trim()),
    tone: ico ? ico.dataset.tone : null,
    icon: ico ? [...ico.querySelectorAll("path")].map((p) => p.getAttribute("d")) : [],
    // Which ICONS entry the headline's glyph is, by its paths.
    iconKey: ico ? Object.keys(window.__icons).find((k) =>
      JSON.stringify([].concat(window.__icons[k]))
      === JSON.stringify([...ico.querySelectorAll("path")].map((p) => p.getAttribute("d")))) || null
      : null,
    insight: insight && !insight.hidden ? insight.textContent : null,
  };
  // WHAT THE ADVISOR HAS SAID, asked of the server directly, so the check
  // below knows whether the two agent cards have anything to show on this
  // machine rather than assuming either way.
  try {
    const r = await fetch("/api/ai/history", { cache: "no-store" });
    const recs = ((await r.json()) || {}).records || [];
    const first = recs.find((x) => x.payload && x.payload.headline);
    out.said = first ? first.payload.headline : null;
  } catch { out.said = null; }
  location.hash = "#home";
  await wait(2000);
  const agent = document.querySelector(".hc-agent .ag-a");
  out.homeAgent = agent && !agent.hidden ? agent.textContent : null;
  document.title = "VEHICLE " + JSON.stringify(out);
})();
</script>
"""


# Appended to a third COPY of app.html, served by a real OmaCar server that the
# test STOPS partway through. The dead-server check below only ever covered a
# server that was absent from the start; this is the other half. The snapshot
# has arrived and says the car is connected, then every request starts
# failing -- and the app used to go on drawing that snapshot's numbers as live,
# under a badge that said LIVE (or SIMULATED), for as long as the server stayed
# down.
#
# The snapshot's own copy of the sample is marked connected before the server
# goes, so the check means the same thing on a machine with no car behind its
# server: that copy is exactly what the app fell back to.
#
# %(kill)d is the port of a one-request helper in this file that terminates
# the server process and only then answers, so by the time the page moves on
# the server really is gone.
LOST_PROBE = r"""
<script type="module">
import { store } from "./js/core.js";
import { READINGS } from "./js/readings.js";
window.__st = store;
window.__rd = READINGS;
</script>
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const txt = (sel) => { const el = document.querySelector(sel); return el ? el.textContent : null; };
  const read = () => ({
    badge: txt(".tb-src"),
    // [state, src, whether it reads a PID]. A reading with no PID (economy
    // today, from the snapshot's own record) may go on drawing that record;
    // one that reads a PID is only ever the current sample.
    readings: [...document.querySelectorAll(".home [data-state]")].map((el) => {
      const id = el.dataset.reading
        || (el.classList.contains("dial-rpm") ? "rpm" : "speed");
      return [el.dataset.state, el.dataset.src || "",
              !!(window.__rd[id] && window.__rd[id].pid)];
    }),
    rpm: txt(".dial-rpm"),
    foot: txt(".home-prov"),
  });
  await wait(3500);
  const s = window.__st;
  s.car.live = Object.assign({}, s.car.live || {}, {
    connected: true, supported: ["SPEED", "RPM", "COOLANT_TEMP"],
    values: Object.assign({}, (s.car.live && s.car.live.values) || {},
                          { SPEED: 0, RPM: 800, COOLANT_TEMP: 90 }) });
  s.emit("car");
  await wait(400);
  const out = { before: read() };
  await fetch("http://127.0.0.1:%(kill)d/kill", { mode: "no-cors" }).catch(() => {});
  await wait(2500);
  out.home = read();
  // GAUGES, the screen the launcher hands over to for the whole drive.
  location.hash = "#drive";
  await wait(1500);
  out.gauges = { badge: txt(".tb-src"), state: (document.querySelector(".drive") || {}).dataset
                   ? document.querySelector(".drive").dataset.state : null,
                 speed: txt(".drive-speed") };
  // A SCREEN WITH NO FAST CLOCK. Only the twenty-second snapshot poll runs
  // here, so what was already learned must not be forgotten on the way in.
  location.hash = "#service";
  await wait(1500);
  out.service = { badge: txt(".tb-src") };
  document.title = "LOST " + JSON.stringify(out);
})();
</script>
"""

# THE TWO SCREENS A DRIVE IS SPENT ON, MEASURED AT THE TABLET'S OWN SIZES.
#
# Home, then the launcher handing over to Gauges exactly the way launcher.js
# does it -- a hash write, not a tap -- because that is the path the tablet
# takes on Wednesday. Gauges moved into Vehicle -> Live, which shows the
# segment row, and nothing measured it after the move: it came out 73 px
# taller than its stage, with its bottom buttons below the fold, and the chip
# row offered "<- Begin", one tap from re-running `omacar begin` (which stops
# the drive recorder) mid-drive.
#
# Run at both orientations with a COARSE pointer emulated, because the tablet
# is a touch screen and the coarse block in app.css makes the tab bar and the
# chip rows taller there -- the case with the least room.
FIT_PROBE = r"""
<script type="module">
import { store } from "./js/core.js";
window.__st = store;
</script>
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const stage = () => {
    const st = document.getElementById("stage");
    return { scroll: st.scrollHeight, client: st.clientHeight,
             bottom: Math.round(st.getBoundingClientRect().bottom) };
  };
  const bottom = (sel) => {
    const el = document.querySelector(sel);
    return el ? Math.round(el.getBoundingClientRect().bottom) : null;
  };
  await wait(3500);
  const out = { viewport: [innerWidth, innerHeight],
                coarse: matchMedia("(pointer: coarse)").matches };
  out.home = stage();
  out.homeFoot = bottom(".home-foot");
  // The cards are measured against the footer, not just the stage: the grid
  // overflowing its own box lands on the footer inside the stage's bottom
  // padding, where no scroll height ever sees it.
  const foot = document.querySelector(".home-foot");
  out.footTop = foot ? Math.round(foot.getBoundingClientRect().top) : null;
  out.cardsEnd = Math.max(0, ...[...document.querySelectorAll(".home-grid > *")]
    .map((el) => Math.round(el.getBoundingClientRect().bottom)));
  // And no card cut short to get there: a tile whose content is taller than
  // the tile has lost its bottom line to overflow:hidden.
  out.clipped = [...document.querySelectorAll(".home-grid > *")]
    .filter((el) => el.scrollHeight > el.clientHeight + 1)
    .map((el) => `${el.dataset.card} ${el.scrollHeight}/${el.clientHeight}`);
  // THE DIAL WITH BEGIN OFFERED: the adapter gone, the car last seen still.
  // Begin's slot is always reserved, so offering it must neither move the
  // gauge nor push anything out of the bottom of the card.
  const s = window.__st, poll = s.refreshLive;
  const dialCard = document.querySelector(".hc-dial");
  const gaugeTop = () => Math.round(document.querySelector(".hc-dial .g-svg").getBoundingClientRect().top);
  const top0 = gaugeTop();
  // Silenced, then a beat for a poll already in flight to land, or it lands
  // after the injected sample and puts "connected" back.
  s.refreshLive = async () => {};
  await wait(600);
  // Seen stopped first (the simulator may be mid-drive), then gone.
  s.live = { connected: true, values: { SPEED: 0, RPM: 0 } };
  s.emit("live");
  await wait(200);
  s.live = { connected: false, values: {} };
  s.emit("live");
  await wait(400);
  out.dial = { top0, top1: gaugeTop(),
               begin: getComputedStyle(document.querySelector(".dial-begin")).visibility,
               clipped: dialCard.scrollHeight > dialCard.clientHeight + 1 };
  s.refreshLive = poll;
  await wait(800);
  location.hash = "#launcher";
  await wait(1200);
  location.hash = "#drive";
  await wait(2000);
  const lead = document.querySelector(".chip-lead");
  out.lead = { vis: getComputedStyle(lead).visibility, text: lead.textContent,
               go: lead.dataset.go || "" };
  out.seg = !document.getElementById("segbar").hidden;
  out.drive = stage();
  out.controls = bottom(".drive-controls");
  document.title = "FIT " + JSON.stringify(out);
})();
</script>
"""

# THE X-RAY'S CALLOUTS, which are only drawn when the private render is
# installed (share/assets/private/, never in git). In portrait the stage is
# wider and shorter, and "Hybrid system" sat on top of "Tyres".
CALLOUT_PROBE = r"""
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  await wait(3000);
  location.hash = "#vehicle";
  await wait(2500);
  const out = {};
  for (const c of document.querySelectorAll(".vh-hero .callout")) {
    if (c.hidden) continue;
    const r = c.getBoundingClientRect();
    out[c.dataset.sys] = [r.left, r.top, r.right, r.bottom].map(Math.round);
  }
  document.title = "CALLOUTS " + JSON.stringify(out);
})();
</script>
"""

# THE AGENT TAB WITHOUT AI. The tab button used to go to its first view, the
# advisor, which the navigation hides when there is no AI -- so it opened a
# screen the chip row did not list, drew no chips, and left Work unreachable.
AGENT_PROBE = r"""
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  await wait(3500);
  const tabs = [...document.querySelectorAll("#tabbar .tab")];
  tabs[tabs.length - 1].click();
  await wait(1500);
  const wrap = document.querySelector("#stage .wrap");
  let ai = null;
  try { ai = (await (await fetch("/api/ai/available", { cache: "no-store" })).json()).available; }
  catch { ai = null; }
  const out = { ai, view: wrap ? wrap.dataset.view : null,
                text: wrap ? wrap.textContent.replace(/\s+/g, " ").trim().slice(0, 200) : "",
                chips: [...document.querySelectorAll("#subbar-chips .chip[data-key]")]
                  .map((c) => c.textContent.trim()) };
  document.title = "AGENT " + JSON.stringify(out);
})();
</script>
"""

# ROAD CAMERAS, at the tablet's own sizes, against a stubbed API. The list is
# built by lib/roadcams.py itself from test/fixtures/d5-cctv.json, so the shape
# the screen is tested against is the server's real one; the stills are drawn
# here and the construction cameras point at about:blank. Nothing reaches
# Caltrans or TrueLook. %(listing)s is that JSON, set by roadcams_listing().
ROADCAMS_STUB = r"""
<script>
window.__rcErrs = [];
addEventListener("error", (e) => window.__rcErrs.push(`${e.message} @${e.lineno}`));
addEventListener("unhandledrejection", (e) => window.__rcErrs.push(String(e.reason)));
(() => {
  const listing = %(listing)s;
  const now = Date.now() / 1000;
  listing.feed.fetched_at = now - 7200;
  listing.net = { ok_at: now - 5, fail_at: null, error: null };
  const json = (o) => Promise.resolve(new Response(JSON.stringify(o),
    { headers: { "Content-Type": "application/json" } }));
  const __f3 = window.fetch;
  window.__rcAsked = [];
  window.fetch = (u, o) => {
    const p = String(u);
    const m = p.match(/\/api\/roadcams\/([a-z0-9-]+)\/image/);
    if (m) {
      window.__rcAsked.push(m[1]);
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="260">`
        + `<rect width="320" height="260" fill="#2a3a44"/><text x="12" y="30" fill="#fff" `
        + `font-family="sans-serif" font-size="16">STUB ${m[1]}</text></svg>`;
      return Promise.resolve(new Response(new Blob([svg], { type: "image/svg+xml" }), {
        headers: { "X-Roadcam-Age": "120", "X-Roadcam-Modified": String(Math.round(now - 120)),
                   "X-Roadcam-Source": "caltrans" } }));
    }
    if (p.includes("/api/roadcams")) return json(listing);
    return __f3(u, o);
  };
})();
</script>
"""

ROADCAMS_PROBE = r"""
<script type="module">
import { store } from "./js/core.js";
window.__st = store;
</script>
<script>
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const stage = () => {
    const st = document.getElementById("stage");
    return { scroll: st.scrollHeight, client: st.clientHeight };
  };
  // The shortest visible button on the screen: every one is a thumb's target.
  const shortest = () => {
    const hs = [...document.querySelectorAll(".rc button")]
      .filter((b) => b.offsetParent !== null)
      .map((b) => Math.round(b.getBoundingClientRect().height));
    return hs.length ? Math.min(...hs) : null;
  };
  await wait(3500);
  const out = { viewport: [innerWidth, innerHeight],
                coarse: matchMedia("(pointer: coarse)").matches };
  // Home's Navigation card, on the arrival screen: it names the road cameras
  // and opens them, not the maps placeholder.
  const card = document.querySelector('.home-grid [data-card="nav"]');
  out.card = card ? [".hc-ph-t", ".hc-ph-s"].map((k) => (card.querySelector(k) || {}).textContent) : null;
  if (card) card.click();
  await wait(1500);
  const via = document.querySelector("#stage .wrap");
  out.cardOpens = via ? via.dataset.view : null;
  location.hash = "#home";
  await wait(1200);
  const tabs = [...document.querySelectorAll("#tabbar .tab")];
  tabs[1].click();
  await wait(2500);
  const wrap = document.querySelector("#stage .wrap");
  out.view = wrap ? wrap.dataset.view : null;
  out.chips = [...document.querySelectorAll("#subbar-chips .chip[data-key]")].map((c) => c.textContent.trim());
  const pinned = document.querySelector(".rc-sec[data-sec='pinned'] .rc-grid");
  out.pinned = pinned ? [...pinned.children].map((t) => t.dataset.id) : null;
  out.groups = [...document.querySelectorAll(".rc-groups .rc-sec-t")].map((e) => e.textContent);
  out.pictures = [...document.querySelectorAll(".rc-sec[data-sec='pinned'] .rc-tile[data-kind='still']")]
    .map((t) => [t.dataset.has, t.querySelector(".rc-age").textContent]);
  // Stills only: a construction camera's player may say "live embed".
  out.live = [...document.querySelectorAll(".rc-tile[data-kind='still']")]
    .some((el) => /\blive\b/i.test(el.textContent));
  out.parkedTap = shortest();
  out.asked = [...new Set(window.__rcAsked)].length;
  // Moving: the poller silenced first, or it puts "no car" straight back.
  const s = window.__st;
  s.refreshLive = async () => {};
  await wait(400);
  s.live = { connected: true, values: { SPEED: 55, RPM: 2400 } };
  s.emit("live");
  await wait(1200);
  out.drive = {
    parkedHidden: document.querySelector(".rc-parked").hidden,
    tiles: [...document.querySelectorAll(".rc-driving .rc-tile")].map((t) => [t.dataset.id, t.dataset.has]),
    width: Math.round((document.querySelector(".rc-driving .rc-tile") || { getBoundingClientRect: () => ({ width: 0 }) })
      .getBoundingClientRect().width),
    frames: document.querySelectorAll(".rc iframe").length,
    edit: [document.querySelector(".rc-editbtn").disabled, document.querySelector(".rc-editbtn").title],
    stage: stage(),
    tap: shortest(),
  };
  // A red light: stopped, and 40 s on it is still the one tile (store.moving
  // waits a minute). The store's clock is moved rather than waited out.
  let ahead = 0;
  s.clock = () => Date.now() + ahead;
  const stopped = () => { s.live = { connected: true, values: { SPEED: 0, RPM: 800 } }; s.emit("live"); };
  stopped();
  ahead = 40000;
  stopped();
  await wait(300);
  out.redLight = document.querySelector(".rc-parked").hidden;
  // A minute stopped: parked again, and the grid comes back.
  ahead = 61000;
  stopped();
  await wait(800);
  out.parkedAgain = !document.querySelector(".rc-parked").hidden;
  out.errs = window.__rcErrs;
  document.title = "ROADCAMS " + JSON.stringify(out);
})();
</script>
"""


def roadcams_listing():
    """The listing lib/roadcams.py would answer for the fixture, with the
    default pins, and the construction cameras pointed at about:blank."""
    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import roadcams
    doc = json.load(open(os.path.join(ROOT, "test", "fixtures", "d5-cctv.json"), encoding="utf-8"))
    cams = roadcams.cameras(doc)
    embs = [dict(e, url="about:blank#" + e["id"]) for e in roadcams.embeds()]
    by_name = {c["name"]: c["id"] for c in cams}
    known = {c["id"] for c in cams + embs}
    pins = [p if p in known else by_name.get(p)
            for p in roadcams.data_file().get("default_pins", [])]
    groups = []
    for c in cams:
        if not groups or groups[-1]["id"] != c["route"]:
            groups.append({"id": c["route"], "label": c["route"], "ids": []})
        groups[-1]["ids"].append(c["id"])
    groups.append({"id": "imjin", "label": "Imjin Parkway", "ids": [e["id"] for e in embs]})
    return {"cameras": cams + embs, "groups": groups, "pins": [p for p in pins if p],
            "pins_default": True, "pins_missing": [], "default_pins": [p for p in pins if p],
            "feed": {"source": roadcams.SOURCE, "fetched_at": 0, "age": 7200,
                     "from_cache": True, "error": None, "offline": False, "count": len(cams)},
            "net": {}, "image_ttl": 60, "max_pins": 12}


def roadcams_check(exe):
    """Navigation opens Road cameras: pinned first, the roads in order, and one
    fixed still with nothing to scroll once the car moves -- at both sizes."""
    stub = ROADCAMS_STUB % {"listing": json.dumps(roadcams_listing())}
    for orient, (want, size) in TABLET.items():
        g = run_probe(exe, stub + ROADCAMS_PROBE, "ROADCAMS", flags=(size, COARSE), budget=14000)
        if not g:
            bad(f"{orient}: the road cameras probe returned nothing")
            continue
        check(f"{orient}: the viewport is the tablet's, touch pointer "
              f"(got {g.get('viewport')}, coarse={g.get('coarse')})",
              g.get("viewport") == list(want) and g.get("coarse") is True)
        check(f"{orient}: Home's Navigation card names the road cameras and opens them "
              f"(card {g.get('card')!r}, opens {g.get('cardOpens')!r})",
              g.get("card") == ["Road cameras", "Your commute: SR-1 at Imjin, Lightfighter, SR-68"]
              and g.get("cardOpens") == "roadcams")
        check(f"{orient}: the Navigation tab opens Road cameras, with Maps beside it "
              f"(view {g.get('view')!r}, chips {g.get('chips')})",
              g.get("view") == "roadcams" and g.get("chips") == ["Road cameras", "Maps"])
        check(f"{orient}: the commute is pinned first, then the Imjin Parkway cameras "
              f"(got {g.get('pinned')})",
              g.get("pinned") == ["sr1imjinparkway", "sr1lightfighterdrive",
                                  "sr68reservationroadriverroad", "imjin-1", "imjin-2", "imjin-3"])
        check(f"{orient}: then every road in order, then Imjin Parkway (got {g.get('groups')})",
              g.get("groups") == ["US-101", "SR-1", "SR-68", "SR-156", "SR-183", "Imjin Parkway"])
        check(f"{orient}: each pinned still shows its picture and its age "
              f"(got {g.get('pictures')})",
              g.get("pictures") == [["1", "2 min ago"]] * 3)
        check(f"{orient}: and no Caltrans still says live", g.get("live") is False)
        check(f"{orient}: every button parked is at least 56 px (shortest {g.get('parkedTap')})",
              (g.get("parkedTap") or 0) >= 56)
        check(f"{orient}: only cameras on screen were asked for "
              f"({g.get('asked')} of 8)", 0 < (g.get("asked") or 0) < 8)
        d = g.get("drive") or {}
        st = d.get("stage") or {}
        check(f"{orient}: moving, the grid is replaced by one tile, the first pinned "
              f"Caltrans camera, with its picture (got {d.get('tiles')})",
              d.get("parkedHidden") is True and d.get("tiles") == [["sr1imjinparkway", "1"]])
        check(f"{orient}: small, but a picture you can read at a glance: wider than "
              f"Caltrans' own 320 px and no wider than 520 (got {d.get('width')})",
              320 <= (d.get("width") or 0) <= 520)
        check(f"{orient}: with nothing to scroll (content {st.get('scroll')} in "
              f"{st.get('client')} px)", st.get("scroll", 1e9) <= st.get("client", 0) + 1)
        check(f"{orient}: no construction camera is framed (got {d.get('frames')})",
              d.get("frames") == 0)
        check(f"{orient}: Edit pins greys out with the reason (got {d.get('edit')})",
              d.get("edit") == [True, "Available when you stop"])
        check(f"{orient}: and every button is still at least 56 px (shortest {d.get('tap')})",
              (d.get("tap") or 0) >= 56)
        check(f"{orient}: a 40 s red light is still the one tile", g.get("redLight") is True)
        check(f"{orient}: a minute stopped brings the grid back", g.get("parkedAgain") is True)
        check(f"{orient}: and nothing threw on the way (got {g.get('errs')})", g.get("errs") == [])


# Emulates the Surface's touch screen: `pointer: coarse` matches, `hover` does
# not. Checked on Chromium 151 by reading both media queries back.
COARSE = ("--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
          "primaryHoverType=1,availableHoverTypes=1")

# --window-size is the OUTER size; the inner viewport comes back shorter by
# the headless frame. These give the tablet's CSS viewports on Chromium 151,
# and the probe reports the viewport it really got, so a drift says so.
TABLET = {"landscape": ((1368, 912), "--window-size=1368,1055"),
          "portrait": ((912, 1368), "--window-size=912,1511")}


def ok(msg):
    print(f"    ok  {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"    FAIL  {msg}")


def check(msg, cond):
    (ok if cond else bad)(msg)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def browser():
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return None


def python_for_server():
    venv = os.path.join(os.path.expanduser("~"), ".local", "share", "omacar",
                        "venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


def run_probe(exe, probe, tag, flags=(), budget=12000):
    """Serve a copy of share/ with `probe` appended to app.html, from a real
    OmaCar server, open it once (onboarding already done) and return the JSON
    the probe put in the title after `tag`, or None."""
    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy)
    with open(os.path.join(copy, "app.html"), "a", encoding="utf-8") as f:
        f.write(probe)
    with open(os.path.join(copy, "_seed.html"), "w", encoding="utf-8") as f:
        f.write('<script>localStorage.setItem("omacar.onboarded","1")</script>ok')
    port = free_port()
    srv = subprocess.Popen(
        [python_for_server(), os.path.join(ROOT, "lib", "serve.py"), str(port), copy],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    base = [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--user-data-dir={prof}", "--hide-scrollbars",
            "--force-device-scale-factor=1", *flags]
    try:
        for _ in range(40):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        subprocess.run(base + ["--virtual-time-budget=2000", "--dump-dom",
                               f"http://127.0.0.1:{port}/_seed.html"],
                       capture_output=True, timeout=120)
        r = subprocess.run(base + [f"--virtual-time-budget={budget}", "--dump-dom",
                                   f"http://127.0.0.1:{port}/app.html"],
                           capture_output=True, text=True, timeout=180)
        m = re.search(r"<title>" + tag + r" (\{.*?\})</title>", r.stdout or "", re.S)
        return json.loads(m.group(1).replace("&quot;", '"')) if m else None
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def fit_check(exe):
    """Home and Gauges fit the tablet's screen at both orientations, and the
    handover from Begin leaves no way back to it on the drive screen."""
    for orient, (want, size) in TABLET.items():
        g = run_probe(exe, FIT_PROBE, "FIT", flags=(size, COARSE))
        if not g:
            bad(f"{orient}: the fit probe returned nothing")
            continue
        check(f"{orient}: the viewport is the tablet's, touch pointer "
              f"(got {g.get('viewport')}, coarse={g.get('coarse')})",
              g.get("viewport") == list(want) and g.get("coarse") is True)
        h, d = g.get("home") or {}, g.get("drive") or {}
        check(f"{orient}: Home fits its stage without scrolling "
              f"(content {h.get('scroll')} in {h.get('client')} px, "
              f"footer ends at {g.get('homeFoot')}, stage at {h.get('bottom')})",
              h.get("scroll", 1e9) <= h.get("client", 0) + 1
              and (g.get("homeFoot") or 1e9) <= h.get("bottom", 0))
        check(f"{orient}: and its cards end above the provenance line "
              f"(cards end at {g.get('cardsEnd')}, footer starts at {g.get('footTop')})",
              0 < (g.get("cardsEnd") or 1e9) <= (g.get("footTop") or 0))
        check(f"{orient}: without cutting any card short (clipped: {g.get('clipped')})",
              g.get("clipped") == [])
        dial = g.get("dial") or {}
        check(f"{orient}: with the adapter gone, the dial offers Begin without "
              f"moving the gauge or clipping the card ({dial})",
              dial.get("begin") == "visible" and dial.get("top0") == dial.get("top1")
              and dial.get("clipped") is False)
        check(f"{orient}: Gauges is shown with its segment row", g.get("seg") is True)
        check(f"{orient}: and fits under it without scrolling "
              f"(content {d.get('scroll')} in {d.get('client')} px, "
              f"buttons end at {g.get('controls')}, stage at {d.get('bottom')})",
              d.get("scroll", 1e9) <= d.get("client", 0) + 1
              and (g.get("controls") or 1e9) <= d.get("bottom", 0))
        lead = g.get("lead") or {}
        check(f"{orient}: after Begin hands over, Gauges offers no way back to "
              f"Begin (lead chip {lead})",
              lead.get("go") != "launcher" and "Begin" not in (lead.get("text") or ""))
        check(f"{orient}: nor any lead chip at all -- Gauges is a car screen",
              lead.get("vis") == "hidden")


def callout_check(exe):
    """No two X-ray callouts overlap, at either orientation."""
    for orient, (_, size) in TABLET.items():
        got = run_probe(exe, CALLOUT_PROBE, "CALLOUTS", flags=(size, COARSE))
        if got is None:
            bad(f"{orient}: the callout probe returned nothing")
            continue
        if not got:
            ok(f"({orient}: the X-ray render is not installed here, so it draws no "
               "callouts to check -- `omacar assets status`)")
            continue
        ids = sorted(got)
        hit = [f"{a}/{b}" for i, a in enumerate(ids) for b in ids[i + 1:]
               if got[a][0] < got[b][2] and got[b][0] < got[a][2]
               and got[a][1] < got[b][3] and got[b][1] < got[a][3]]
        check(f"{orient}: no two X-ray callouts overlap ({len(ids)} drawn; "
              f"overlapping: {hit})", len(ids) == 5 and hit == [])


# The server's answer to "is there an advisor?" replaced by "no" before the
# app boots (this runs ahead of main.js, which is a deferred module), so the
# no-AI state is exercised on a machine that has the CLI.
NO_AI = r"""
<script>
const __fetch = window.fetch;
window.fetch = (u, o) => String(u).includes("/api/ai/available")
  ? Promise.resolve(new Response('{"available": false}', { headers: { "Content-Type": "application/json" } }))
  : __fetch(u, o);
</script>
"""


# A LIGHT DESKTOP THEME, then the Omarchy look chosen after boot. The theme
# routes are answered here, ahead of main.js, because the box's own desktop
# theme is whatever it is today. The day/night button was painted before the
# theme had loaded, with no mode, so it offered "day" on a desktop already in
# its light theme.
LIGHT_DESKTOP = r"""
<script>
const __f1 = window.fetch;
const __json = (o) => Promise.resolve(new Response(JSON.stringify(o),
  { headers: { "Content-Type": "application/json" } }));
window.fetch = (u, o) => {
  const p = String(u);
  if (p.includes("/api/themes")) return __json({ active: "paper", themes: [
    { id: "paper", mode: "light" }, { id: "ink", mode: "dark" }] });
  if (p.includes("/api/theme")) return __json({ stamp: 7, vars: { mode: "light" } });
  return __f1(u, o);
};
</script>
<script type="module">
import { saveLook, applyLook } from "./js/looks.js";
setTimeout(() => { saveLook("omarchy"); applyLook("omarchy"); }, 3500);
</script>
<script>
setTimeout(() => {
  const b = document.getElementById("btn-daynight");
  document.title = "DAYNIGHT " + JSON.stringify({ title: b ? b.title : null,
    hidden: b ? b.hidden : null, mode: document.documentElement.dataset.mode || null,
    look: document.documentElement.getAttribute("data-look") });
}, 6000);
</script>
"""

# A SPOKEN REQUEST FOR "hub", the address Home had for a year and still has in
# ALIASES for bookmarks and the dock card. The request is answered here, ahead
# of main.js, as lib/screen.py would answer it.
HUB_ASK = r"""
<script>
const __f2 = window.fetch;
window.__ask = null;
window.fetch = (u, o) => (String(u).includes("/api/screen") && !(o && o.method === "POST"))
  ? Promise.resolve(new Response(JSON.stringify({ ask: window.__ask }),
      { headers: { "Content-Type": "application/json" } }))
  : __f2(u, o);
setTimeout(() => { location.hash = "#vehicle"; }, 3000);
setTimeout(() => { window.__ask = { view: "hub", fresh: true, at: Date.now() / 1000,
                                    who: "the assistant" }; }, 4500);
setTimeout(() => {
  document.title = "HUBASK " + JSON.stringify({ hash: location.hash,
    toasts: [...document.querySelectorAll("#toasts .toast")].map((t) => t.textContent) });
}, 8500);
</script>
"""


def daynight_check(exe):
    """The day/night button after choosing the Omarchy look on a light desktop."""
    g = run_probe(exe, LIGHT_DESKTOP, "DAYNIGHT")
    if not g:
        bad("the day/night probe returned nothing")
    else:
        check(f"switching into the Omarchy look on a light desktop theme, the "
              f"day/night button offers night ({g})",
              g.get("look") == "omarchy" and g.get("hidden") is False
              and g.get("title") == "Switch to the night palette")


def hub_ask_check(exe):
    """A spoken request for the old 'hub' address opens Home."""
    h = run_probe(exe, HUB_ASK, "HUBASK")
    if not h:
        bad("the hub-request probe returned nothing")
    else:
        check(f"a spoken request for 'hub' opens Home, and says so ({h})",
              h.get("hash") == "#home"
              and any("opened Home" in t for t in h.get("toasts") or []))


def agent_check(exe):
    """The Agent tab opens a screen it lists, and says so when AI is not set up."""
    for label, prefix in (("as this server reports it", ""), ("with no AI", NO_AI)):
        g = run_probe(exe, prefix + AGENT_PROBE, "AGENT")
        if not g:
            bad(f"{label}: the agent probe returned nothing")
            continue
        check(f"{label}: the Agent tab opens a screen its chip row lists, with "
              f"Work reachable (chips {g.get('chips')}, opened {g.get('view')!r})",
              g.get("chips") == ["Car", "Work"] and g.get("view") == "advisor")
        if prefix or g.get("ai") is False:
            check(f"{label}: and it says the advisor is not set up "
                  f"(got {g.get('text')[:90]!r})",
                  "needs the Claude CLI" in (g.get("text") or ""))


def lost_server_check(exe):
    """Stop the OmaCar server mid-run and read what the app then claims."""
    import http.server
    import threading

    work = tempfile.mkdtemp()
    copy = os.path.join(work, "share")
    shutil.copytree(SHARE, copy)
    kport = free_port()
    with open(os.path.join(copy, "app.html"), "a", encoding="utf-8") as f:
        f.write(LOST_PROBE % {"kill": kport})
    port = free_port()
    srv = subprocess.Popen(
        [python_for_server(), os.path.join(ROOT, "lib", "serve.py"), str(port), copy],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    killed = []

    class Kill(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not killed:
                srv.terminate()
                try:
                    srv.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    srv.kill()
                    srv.wait(timeout=5)
                killed.append(srv.returncode)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def log_message(self, *_):
            pass

    helper = http.server.ThreadingHTTPServer(("127.0.0.1", kport), Kill)
    threading.Thread(target=helper.serve_forever, daemon=True).start()
    prof = tempfile.mkdtemp()
    try:
        for _ in range(40):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={prof}", "--virtual-time-budget=15000",
             "--dump-dom", f"http://127.0.0.1:{port}/app.html"],
            capture_output=True, text=True, timeout=180)
        m = re.search(r"<title>LOST (\{.*?\})</title>", r.stdout or "", re.S)
        check("the server really was stopped partway through the run", bool(killed))
        if not m:
            bad("the lost-server probe returned nothing")
            return
        g = json.loads(m.group(1).replace("&quot;", '"'))
        before, home = g.get("before") or {}, g.get("home") or {}
        check(f"before it went, Home had painted its readings "
              f"(badge {before.get('badge')!r}, readings {before.get('readings')})",
              any(pid for _, _, pid in before.get("readings") or [])
              and before.get("badge") != "NO SERVER")
        check(f"once it has gone, the badge says NO SERVER (got {home.get('badge')!r})",
              home.get("badge") == "NO SERVER")
        rd = home.get("readings") or []
        check(f"and no reading of the car stays live on Home (got {rd})",
              any(pid for _, _, pid in rd)
              and not any(st == "live" for st, _, pid in rd if pid))
        check("while one drawn from a record says it is recorded or simulated, "
              "never OBD",
              all(src in ("recorded", "sim") for st, src, pid in rd
                  if st == "live" and not pid))
        check(f"the dial names the server (got {home.get('rpm')!r})",
              home.get("rpm") == "No server")
        check(f"and so does Home's footer (got {home.get('foot')!r})",
              "cannot reach its own server" in (home.get("foot") or ""))
        gg = g.get("gauges") or {}
        check(f"Gauges follows the same rule: NO SERVER, no link, no speed "
              f"(got {gg})",
              gg.get("badge") == "NO SERVER" and gg.get("state") == "offline"
              and not re.search(r"\d", gg.get("speed") or ""))
        check(f"and a screen with no fast clock still knows "
              f"(got {(g.get('service') or {}).get('badge')!r})",
              (g.get("service") or {}).get("badge") == "NO SERVER")
    finally:
        helper.shutdown()
        if srv.poll() is None:
            srv.kill()
        shutil.rmtree(prof, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def main():
    print("\n  The app starts in a browser\n")
    exe = browser()
    if not exe:
        print("    (skipping: no chromium here. `sudo pacman -S chromium` to\n"
              "     have this test mean something.)\n")
        return 0

    port = free_port()
    profile = tempfile.mkdtemp()
    server = subprocess.Popen(
        [python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
         str(port), SHARE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/app.html"
        for _ in range(60):
            time.sleep(0.25)
            try:
                with socket.create_connection(("127.0.0.1", port), 0.25):
                    break
            except OSError:
                continue
        else:
            bad("the loopback server never came up")
            return 1
        ok(f"server up on {port}")

        r = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={profile}", "--virtual-time-budget=9000",
             "--enable-logging=stderr", "--dump-dom", url],
            capture_output=True, text=True, timeout=120)
        dom, log = r.stdout or "", r.stderr or ""

        check("the page returned a document", len(dom) > 2000)

        # 1. Nothing threw. The browser prints an uncaught error to stderr and
        #    the app draws its own card, so both are worth asking about.
        throws = re.findall(r"(?:Reference|Type|Syntax|Range)Error[^\n]{0,120}",
                            log + "\n" + dom)
        if throws:
            for t in throws[:4]:
                bad(f"the page threw: {t.strip()}")
        else:
            ok("nothing threw while the app started")

        card = "OmaCar could not start" in dom
        if card:
            why = re.search(r"OmaCar could not start.{0,200}", dom, re.S)
            bad("the app drew its own failure card: "
                + re.sub(r"<[^>]+>", " ", why.group(0) if why else "").strip()[:160])
        else:
            ok("the app did not draw a failure card")

        # 2. It is actually on the screen, not merely silent.
        for what, needle in (("the vehicle bar", "vbar"),
                             ("the navigation", "<nav"),
                             ("a mounted view", "data-view")):
            check(f"{what} rendered", needle in dom)

        # 3. THE BOOT SCREEN LET GO. This is the check the three above cannot
        #    make: a splash stuck at full opacity over a perfectly healthy app
        #    leaves every one of them passing, because the DOM is all there --
        #    it is simply behind a sheet nobody can tap through. So the test
        #    asks for the one thing that means it finished, which is that the
        #    element removed itself.
        check("the boot screen is drawn before anything can load it",
              'id="boot"' in open(os.path.join(SHARE, "app.html"),
                                  encoding="utf-8").read())
        check("and it let go of the screen once the app was up",
              'id="boot"' not in dom)

        # 4. EVERY DRAWN NUMBER SAYS WHERE IT CAME FROM. A sig tile carries
        #    data-state (live, waiting, absent) on itself; so does the dial
        #    card's speed gauge and its RPM figure, the same way, since a
        #    number drawn on a needle or in a plain div is exactly as capable
        #    of lying as one drawn in a box. Scoped to data-state itself
        #    rather than to a tile's class, so a future card is caught by
        #    adopting the shared marker, not by guessing its markup in
        #    advance. One that says "live" carries data-src (sim, bench, obd,
        #    recorded); no other state may. Home is the arrival screen, so
        #    this reads what it painted.
        #
        #    A COUNT ALONE CANNOT CATCH LOSING THE DIAL'S OWN MARKING. If
        #    share/js/views/home.js stopped setting g.el/rpm's data-state --
        #    a plain revert, no new card involved -- the sig tiles alone would
        #    still clear any floor this used to check against, silently
        #    reproducing the exact defect this section exists for. So the
        #    dial's two elements are named and required by what identifies
        #    them in home.js (the arc gauge's own SVG class, the RPM div's
        #    own class), not just counted, and the total is exact against
        #    that breakdown rather than a floor that stops meaning anything
        #    once the healthy number changes.
        tiles = re.findall(r'<[^>]*\bdata-state="[a-z]+"[^>]*>', dom)
        sig_tiles = [t for t in tiles if re.search(r'class="sig(?=[\s"])', t)]
        dial_speed = [t for t in tiles if 'class="g-svg g-arc' in t]
        dial_rpm = [t for t in tiles if 'class="dial-rpm' in t]
        check(f"Home drew readings that name their state (found {len(tiles)}: "
              f"{len(sig_tiles)} sig tiles, {len(dial_speed)} dial speed, "
              f"{len(dial_rpm)} dial rpm)",
              len(sig_tiles) >= 4
              and len(tiles) == len(sig_tiles) + len(dial_speed) + len(dial_rpm))
        check("the dial's speed gauge is among the readings checked",
              len(dial_speed) == 1)
        check("and so is its RPM figure",
              len(dial_rpm) == 1)
        live_tiles = [t for t in tiles if 'data-state="live"' in t]
        check("every reading drawing a number names its source",
              all(re.search(r'data-src="[a-z]+"', t) for t in live_tiles))
        check("and no reading that is not live claims one",
              not any('data-src=' in t for t in tiles if t not in live_tiles))
        # ---- and again with no server behind it at all -------------------
        #
        # THE FAILURE PATH IS THE ONE THAT LIED. With the OmaCar server down,
        # every /api/ call 404s -- and the app said "connecting..." in the bar
        # and "Not connected" with a Connect button on the hub, which is what
        # it says when the CAR has not answered. Those two have completely
        # different fixes: one is a cable in a footwell, the other is a daemon
        # that is not running, and sending somebody to the wrong one in a car
        # park at night is exactly the kind of small lie this tool exists not
        # to tell.
        static = free_port()
        plain = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(static),
             "--bind", "127.0.0.1"], cwd=SHARE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        prof2 = tempfile.mkdtemp()
        try:
            for _ in range(40):
                time.sleep(0.25)
                try:
                    with socket.create_connection(("127.0.0.1", static), 0.25):
                        break
                except OSError:
                    continue
            r2 = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={prof2}", "--virtual-time-budget=12000",
                 "--dump-dom", f"http://127.0.0.1:{static}/app.html"],
                capture_output=True, text=True, timeout=120)
            dead = r2.stdout or ""
            check("with no server, the boot screen still lets go",
                  'id="boot"' not in dead)
            check("and the app names the server rather than the car",
                  "cannot reach the OmaCar server" in dead
                  and "cannot reach its own server" in dead)
            check("and does not send anybody to the OBD cable",
                  "Not connected" not in dead)
            # THE DAY/NIGHT BUTTON ALSO HAS TO SURVIVE THIS. /api/themes is one
            # more route this bare static server does not have, and outside the
            # Omarchy look the button needs no theme pair at all -- so it must
            # still be visible here, not just once the car is reachable.
            btn = re.search(r'<button[^>]*id="btn-daynight"[^>]*>', dead)
            check("and the day/night button is not stuck hidden without a "
                  "theme endpoint",
                  btn is not None and "hidden" not in btn.group(0))
        finally:
            plain.terminate()
            try:
                plain.wait(timeout=5)
            except subprocess.TimeoutExpired:
                plain.kill()
            shutil.rmtree(prof2, ignore_errors=True)
        # ---- and the geometry a thumb has to hit ---------------------------
        #
        # MEASURED, NOT READ. The drive screen's exit button used to grow from
        # 552 to 1125 CSS pixels the instant the car crept past 1.9 mph,
        # sliding its label 286 pixels -- 54 mm -- sideways and covering the
        # spot the other button had occupied. Dithering the speed across the
        # threshold flipped it 4.2 times a second, so one fixed point on the
        # glass meant two different actions at that rate. No amount of reading
        # the stylesheet finds that; it is a rendered number.
        #
        # The page is measured in a COPY of share/, so nothing here can leave
        # a probe behind in the repository.
        probe = tempfile.mkdtemp()
        copy = os.path.join(probe, "share")
        shutil.copytree(SHARE, copy)
        with open(os.path.join(copy, "app.html"), "a", encoding="utf-8") as f:
            f.write(GEOMETRY_PROBE)
        with open(os.path.join(copy, "_seed.html"), "w", encoding="utf-8") as f:
            f.write('<script>localStorage.setItem("omacar.onboarded","1")</script>ok')
        gport = free_port()
        gsrv = subprocess.Popen(
            [python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
             str(gport), copy],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        gprof = tempfile.mkdtemp()
        try:
            for _ in range(40):
                time.sleep(0.25)
                try:
                    with socket.create_connection(("127.0.0.1", gport), 0.25):
                        break
                except OSError:
                    continue
            subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={gprof}", "--virtual-time-budget=2000",
                 "--dump-dom", f"http://127.0.0.1:{gport}/_seed.html"],
                capture_output=True, timeout=120)
            # 1368x912 is the INNER viewport of a Surface Pro 7 at scale 2, and
            # --window-size sets the OUTER one: asking for 1368,912 gives an
            # inner height of 769 and every number measured in it is wrong.
            #
            # WHAT IS NOT KNOWN, and it is worth writing down rather than
            # leaving the next person to rediscover it. Asking for this exact
            # size, on this exact profile, the seed page comes back with an
            # inner height of 912 and app.html comes back with 968 — same
            # flags, same run, same browser. So the 56px is something the app's
            # own page does, not the browser chrome, and a calibration measured
            # on the seed page measures the wrong page. Chromium 151, Sep 2026.
            rg = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={gprof}", "--hide-scrollbars",
                 "--force-device-scale-factor=1", "--window-size=1368,1055",
                 "--virtual-time-budget=20000", "--dump-dom",
                 # No hash: the probe reads the arrival screen first,
                 # then navigates. Loading straight into #drive would skip the
                 # screen the tablet actually paints when somebody gets in.
                 f"http://127.0.0.1:{gport}/app.html"],
                capture_output=True, text=True, timeout=180)
            m = re.search(r"<title>GEOM (\{.*?\})</title>", rg.stdout, re.S)
            if not m:
                bad("the geometry probe returned nothing")
            else:
                g = json.loads(m.group(1).replace("&quot;", '"'))
                vp = g.get("viewport") or []
                check(f"the viewport really is the tablet's (got {vp[:2]})",
                      vp[:2] == [1368, 912])
                check(f"the exit button does not move when the car starts "
                      f"rolling (stopped {g.get('stopped')}, "
                      f"rolling {g.get('rolling')})",
                      g.get("stopped") == g.get("rolling")
                      and g.get("stopped") is not None)
                check(f"Home and the drive screen agree about the coolant "
                      f"({g.get('homeCoolant')!r} vs {g.get('driveCoolant')!r})",
                      bool(g.get("agree")))
                check(f"and Home says which scale it is in "
                      f"(unit element reads {g.get('homeUnit')!r})",
                      bool(g.get("unit")))
                widths = sorted({r[2] for r in (g.get("creep") or []) if r})
                check(f"nor flicker across the threshold in creeping traffic "
                      f"(widths seen: {widths})", len(widths) == 1)
        finally:
            gsrv.terminate()
            try:
                gsrv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gsrv.kill()
            shutil.rmtree(gprof, ignore_errors=True)
            shutil.rmtree(probe, ignore_errors=True)
        # ---- and the Vehicle screen actually mounts -------------------
        vprobe = tempfile.mkdtemp()
        vcopy = os.path.join(vprobe, "share")
        shutil.copytree(SHARE, vcopy)
        with open(os.path.join(vcopy, "app.html"), "a", encoding="utf-8") as f:
            f.write(VEHICLE_PROBE)
        vport = free_port()
        vsrv = subprocess.Popen(
            [python_for_server(), os.path.join(ROOT, "lib", "serve.py"),
             str(vport), vcopy],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        vprof = tempfile.mkdtemp()
        try:
            for _ in range(40):
                time.sleep(0.25)
                try:
                    with socket.create_connection(("127.0.0.1", vport), 0.25):
                        break
                except OSError:
                    continue
            rv = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 f"--user-data-dir={vprof}", "--virtual-time-budget=12000",
                 "--dump-dom", f"http://127.0.0.1:{vport}/app.html"],
                capture_output=True, text=True, timeout=120)
            mv = re.search(r"<title>VEHICLE (\{.*?\})</title>", rv.stdout, re.S)
            if not mv:
                bad("the vehicle probe returned nothing")
            else:
                v = json.loads(mv.group(1).replace("&quot;", '"'))
                check(f"the Vehicle screen draws a headline (got {v.get('headline')!r})",
                      bool((v.get("headline") or "").strip()))
                check(f"and one row per system (got {v.get('rowCount')})",
                      v.get("rowCount") == 5)
                rows_text = " | ".join(v.get("rows") or [])
                for label in ("Engine", "Hybrid system", "Brakes", "Electrical",
                              "All other systems"):
                    check(f"the systems list names {label!r}", label in rows_text)
                # THE GLYPH SAYS WHAT THE WORDS SAY. A tick beside "4 systems
                # need attention" was only recoloured, never redrawn.
                tone, key = v.get("tone"), v.get("iconKey")
                want = {"ok": "check", "warn": "codes", "bad": "codes"}.get(tone)
                check(f"the headline's icon matches it ({v.get('headline')!r}, "
                      f"tone {tone!r}, icon {key!r})",
                      key == want if want else key not in ("check", "codes"))
                said = v.get("said")
                if said:
                    check(f"Vehicle's insight card shows the advisor's last headline "
                          f"(got {v.get('insight')!r})", said in (v.get("insight") or ""))
                    check(f"and so does Home's Oma Agent card (got {v.get('homeAgent')!r})",
                          v.get("homeAgent") == said)
                else:
                    ok("(the advisor has said nothing on this machine: the agent "
                       "cards' reading is covered by test/js/advice.test.js)")
                    check("and with nothing said, Vehicle draws no insight card",
                          v.get("insight") is None)
        finally:
            vsrv.terminate()
            try:
                vsrv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vsrv.kill()
            shutil.rmtree(vprof, ignore_errors=True)
            shutil.rmtree(vprobe, ignore_errors=True)
        # ---- and the server going away after the first paint ------------
        lost_server_check(exe)
        # ---- and the drive's two screens fit the tablet ------------------
        fit_check(exe)
        # ---- and the X-ray's labels stay apart --------------------------
        callout_check(exe)
        # ---- and the Agent tab with or without AI -----------------------
        agent_check(exe)
        # ---- and the day/night button on a light desktop theme ----------
        daynight_check(exe)
        # ---- and a spoken request by Home's old name ---------------------
        hub_ask_check(exe)
        # ---- and the road cameras, parked and moving ---------------------
        roadcams_check(exe)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print()
    if fails:
        print(f"  {fails} failed\n")
        return 1
    print("  the app starts\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
