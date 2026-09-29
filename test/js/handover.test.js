// A HAND-OFF IS NOT NOW.
//
// The drive recorder borrows the OBD adapter for a CAN capture every few
// minutes, and the DTC sweep, a scan or a reset do the same. While it is lent
// out the daemon publishes its last complete sample again -- a fresh `t`, the
// last values it read, connected: false -- and records.live() marks it
// handover: true (lib/daemon.py yield_snapshot(), lib/records.py live()).
//
// On the 29 September drive that was about 142 s in every 3.7 min, and every
// screen drew those last values as if they were current: 60 mph at a stop, and
// the IMA state saying "charging" while the motor assisted. These are the
// screens, mounted for real and fed the daemon's own shapes.
import { eq, ok } from "./assert.js";
import { store, api } from "../js/core.js";
import * as R from "../js/readings.js";
import { makeSignalTile } from "../js/sigtile.js";
import home from "../js/views/home.js";
import drive from "../js/views/drive.js";
import cluster from "../js/views/live.js";
import music from "../js/views/music.js";
import { gaugeRail } from "../js/omaplay/rail.js";
import ima from "../js/views/ima.js";

const { READINGS, readingState } = R;
// Looked up at call time, so this file still loads against a readings.js that
// has no such function and each test below fails on its own assertion.
const pausedNote = (...a) => R.pausedNote(...a);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Date.now() / 1000;

const SUPPORTED = ["SPEED", "RPM", "COOLANT_TEMP", "CONTROL_MODULE_VOLTAGE",
                   "FUEL_LEVEL", "HYBRID_BATTERY_REMAINING", "ENGINE_LOAD"];

// A connected sample as the daemon publishes it: 60 mph, warm, pack at 61%.
function LIVE(over) {
  return {
    connected: true, port: "/dev/ttyUSB0", kind: "wired", t: now(), uptime: 900,
    protocol: "ISO 15765-4 (CAN 11/500)", supported: SUPPORTED,
    values: Object.assign({ SPEED: 96.56, RPM: 2100, COOLANT_TEMP: 90,
                            CONTROL_MODULE_VOLTAGE: 14.1, FUEL_LEVEL: 55,
                            HYBRID_BATTERY_REMAINING: 61, ENGINE_LOAD: 40 }, over || {}),
    economy_lphk: 5.2, efficiency: 0.6, efficiency_basis: "economy",
    handover: false,
  };
}

// yield_snapshot(): the last complete sample, re-stamped, connected false.
function HANDOVER(from) {
  return Object.assign({}, from || LIVE(), {
    connected: false, status: "yielded", handover: true, t: now(),
    note: "a command is using the adapter",
  });
}

// wait_for_car()'s hand-off: a lease arriving while the daemon has no
// connection of its own to lend. Nothing but the status comes with it.
const BARE = { connected: false, status: "yielded", port: "/dev/ttyUSB0",
               note: "a command is using the adapter", handover: true };

// records.live() over a daemon that stopped writing: every value dropped, and
// no handover key at all (that return happens before the key is set).
const STALE = { connected: false, status: "no daemon", stale_for: 40,
                port: "/dev/ttyUSB0", kind: "wired",
                protocol: "ISO 15765-4 (CAN 11/500)", values: {} };

function feed(sample) {
  store.live = sample;
  store.emit("live");
}

// The store is one module-wide object; what one test leaves in it must not be
// the next one's starting point.
function forget() {
  store.live = null;
  store.car = null;
  store.emit("live");
}

function ensureHosts() {
  for (const id of ["toasts", "modal-host", "app"]) {
    if (!document.getElementById(id)) {
      const d = document.createElement("div");
      d.id = id;
      document.body.appendChild(d);
    }
  }
}

// THE LOOK IS PART OF THE FIX, so it is read with the real stylesheets on.
// Loaded for the length of one test and taken off again, so no other suite in
// this runner changes under it.
async function withCss(fn) {
  const links = [];
  for (const href of ["css/app.css", "css/home.css", "css/ima.css"]) {
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = href;
    const done = new Promise((r) => { l.onload = r; l.onerror = r; });
    document.head.appendChild(l);
    links.push(l);
    await done;
  }
  try { await fn(); } finally { for (const l of links) l.remove(); }
}

// What var(--name) resolves to, as a colour, where `el` is. A probe beside the
// element, so a screen-scoped override of the token (the drive screen lifts
// --faint for sunlight) is the one compared against.
function token(el, name) {
  const host = el instanceof SVGElement ? el.ownerSVGElement.parentElement : el.parentElement;
  const probe = document.createElement("span");
  probe.style.color = `var(${name})`;
  host.appendChild(probe);
  const got = getComputedStyle(probe).color;
  probe.remove();
  return got;
}
const ink = (el, prop = "color") => getComputedStyle(el)[prop];

// A computed colour as [r, g, b] in 0..255, whether Chromium serialises it as
// rgb() or, for a color-mix(), as color(srgb ...).
function rgb(s) {
  let m = /^rgba?\(([^)]+)\)/.exec(s);
  if (m) return m[1].split(/[\s,/]+/).filter(Boolean).slice(0, 3).map(Number);
  m = /^color\(srgb ([^)]+)\)/.exec(s);
  if (m) return m[1].trim().split(/\s+/).slice(0, 3).map((x) => Number(x) * 255);
  return null;
}

// How far `c` sits from `b` toward `a`, if it lies on the line between them:
// 1 is all `a`, 0 is all `b`, null is some other colour entirely. A dimmed
// warning is its warning colour part-way to the ground, so this says both
// that it is still that colour and that it is dimmed, without fixing how much.
function mixOf(c, a, b) {
  const [C, A, B] = [rgb(c), rgb(a), rgb(b)];
  if (!C || !A || !B) return null;
  const ts = [];
  for (let i = 0; i < 3; i++) {
    if (Math.abs(A[i] - B[i]) < 8) {
      if (Math.abs(C[i] - B[i]) > 3) return null;
    } else ts.push((C[i] - B[i]) / (A[i] - B[i]));
  }
  if (!ts.length || Math.max(...ts) - Math.min(...ts) > 0.04) return null;
  return ts.reduce((x, y) => x + y, 0) / ts.length;
}
const dimmedAs = (el, prop, name) => {
  const t = mixOf(ink(el, prop), token(el, name), token(el, "--ground"));
  ok(t !== null && t > 0.2 && t < 0.95,
     `drawn ${ink(el, prop)}: wanted ${name} (${token(el, name)}) dimmed toward the ground, got mix ${t}`);
};

async function mounted(view, arg) {
  ensureHosts();
  const stage = document.createElement("div");
  const root = document.createElement("div");
  stage.appendChild(root);
  document.body.appendChild(stage);
  const unmount = view(root, { arg });
  return {
    root,
    done: () => {
      try { unmount(); } finally { stage.remove(); forget(); }
    },
  };
}

async function withHome(before, fn) {
  if (before) before();
  const m = await mounted(home);
  try {
    for (let i = 0; i < 40 && !m.root.querySelector(".home-grid .sig"); i++) await wait(50);
    await fn(m.root);
  } finally { m.done(); }
}

// Every reading Home marks, as [what, state, has a source].
const marked = (root) => [...root.querySelectorAll("[data-state]")].map((el) =>
  [el.dataset.reading || (el.classList.contains("dial-rpm") ? "rpm" : "speed"),
   el.dataset.state, "src" in el.dataset]);

// ---------------------------------------------------------------- the rules
const rules = [
  ["a hand-off sample's live readings are paused, not live and not waiting", () => {
    const s = HANDOVER();
    eq(["speed", "rpm", "coolant", "volts", "fuel", "charge", "ima", "load"]
         .map((id) => readingState(READINGS[id], s)),
       ["paused", "paused", "paused", "paused", "paused", "paused", "paused", "paused"]);
  }],
  ["economy and range are worked out from the sample, so they pause too", () =>
    eq([readingState(READINGS.econ_now, HANDOVER()), readingState(READINGS.range, HANDOVER())],
       ["paused", "paused"])],
  ["what comes from the snapshot, not the sample, is not paused by a hand-off", () =>
    eq(["odometer", "codes", "service", "today", "econ_trip"]
         .map((id) => readingState(READINGS[id], HANDOVER())),
       ["live", "live", "live", "live", "live"])],
  ["a reading this car does not report stays 'Not on this car' through a hand-off", () =>
    eq(readingState(READINGS.intake, HANDOVER()), "absent")],
  ["a stale no-daemon sample is waiting, never paused", () =>
    eq(["speed", "coolant", "charge", "econ_now"].map((id) => readingState(READINGS[id], STALE)),
       ["waiting", "waiting", "waiting", "live"])],
  ["a normal sample is live", () =>
    eq(["speed", "coolant", "charge"].map((id) => readingState(READINGS[id], LIVE())),
       ["live", "live", "live"])],
  ["the words: short on a tile, with the reason on a line, and how long only once it is long", () => {
    const t = Date.now();
    eq([pausedNote(null), pausedNote(t - 20000, false, t), pausedNote(t - 20000, true, t),
        pausedNote(t - 59000, true, t), pausedNote(t - 142000, false, t),
        pausedNote(t - 142000, true, t)],
       ["Paused", "Paused", "Paused · adapter in use",
        "Paused · adapter in use", "Paused · 2 min",
        "Paused · adapter in use · 2 min"]);
  }],
];

// ---------------------------------------------------------------- a tile
const tile = [
  ["a signal tile in a hand-off draws the last value, paused, with no source", () => {
    const t = makeSignalTile("coolant");
    t.paint({}, HANDOVER(LIVE({ COOLANT_TEMP: 108 })));
    eq([t.node.dataset.state, t.node.querySelector(".sig-v").textContent,
        t.node.querySelector(".sig-note").textContent, "src" in t.node.dataset, t.node.dataset.tone],
       ["paused", "226", "Paused", false, "bad"]);
  }],
  ["and on the next fresh sample it is live again, with its source and its colour", () => {
    const t = makeSignalTile("coolant");
    t.paint({}, HANDOVER(LIVE({ COOLANT_TEMP: 108 })));
    t.paint({}, LIVE({ COOLANT_TEMP: 108 }));
    eq([t.node.dataset.state, t.node.querySelector(".sig-v").textContent,
        t.node.querySelector(".sig-note").textContent, t.node.dataset.src, t.node.dataset.tone],
       ["live", "226", "", "obd", "bad"]);
  }],
];

// ---------------------------------------------------------------- Home
const onHome = [
  ["Home in a hand-off: every live reading is drawn paused, dimmed, never as current", () =>
    withCss(() => withHome(() => feed(LIVE()), async (root) => {
      feed(HANDOVER());
      const m = marked(root);
      ok(m.length >= 6, `Home marked ${m.length} readings`);
      eq(m.filter(([, st]) => st !== "paused"), [], "readings not paused");
      eq(m.filter(([, , src]) => src), [], "a paused reading claiming a source");
      const dial = root.querySelector(".hc-dial .g-svg");
      const value = dial.querySelector(".g-value");
      eq(value.textContent, "60", "the dial keeps the last speed rather than blanking");
      eq(ink(value, "fill"), token(value, "--faint"), "and draws it dimmed");
      eq(root.querySelector(".dial-rpm").textContent, "Paused · adapter in use", "the dial's words");
      for (const s of root.querySelectorAll(".home-grid .sig")) {
        const v = s.querySelector(".sig-v");
        ok(v.textContent !== "", `${s.dataset.reading}: the last value is still shown`);
        eq(ink(v), token(v, "--faint"), `${s.dataset.reading}: dimmed`);
        eq(s.querySelector(".sig-note").textContent, "Paused", `${s.dataset.reading}: says so`);
      }
    }))],

  ["Home on a normal sample draws every reading live, with its source, at full ink", () =>
    withCss(() => withHome(() => feed(LIVE()), async (root) => {
      const m = marked(root);
      eq(m.filter(([, st, src]) => st !== "live" || !src), [], "readings not live, or with no source");
      const value = root.querySelector(".hc-dial .g-value");
      eq(value.textContent, "60", "the dial");
      ok(ink(value, "fill") !== token(value, "--faint"), "not dimmed");
      eq(root.querySelector(".dial-rpm").textContent, "2,100 rpm", "the dial's words");
      ok(!/Paused/.test(root.textContent), "nothing on Home says paused");
    }))],

  ["Home on a stale no-daemon sample says No data, not paused", () =>
    withCss(() => withHome(() => feed(LIVE()), async (root) => {
      feed(STALE);
      eq(marked(root).filter(([, st]) => st !== "waiting"), [], "readings not waiting");
      eq(root.querySelector(".dial-rpm").textContent, "No data", "the dial's words");
      eq(root.querySelector(".hc-dial .g-value").textContent, "—", "the dial draws no number");
      for (const s of root.querySelectorAll(".home-grid .sig")) {
        eq(s.querySelector(".sig-note").textContent, "Waiting for the car", s.dataset.reading);
      }
      ok(!/Paused/.test(root.textContent), "nothing on Home says paused");
    }))],

  ["a hand-off, then fresh data: Home is back to normal on that very sample", () =>
    withCss(() => withHome(() => feed(LIVE()), async (root) => {
      feed(HANDOVER());
      eq(root.querySelector(".hc-dial .g-svg").dataset.state, "paused", "paused first");
      feed(LIVE({ SPEED: 0, RPM: 800, COOLANT_TEMP: 91 }));
      eq(marked(root).filter(([, st, src]) => st !== "live" || !src), [], "readings not back to live");
      const value = root.querySelector(".hc-dial .g-value");
      eq(value.textContent, "0", "the dial shows the new speed");
      ok(ink(value, "fill") !== token(value, "--faint"), "at full ink again");
      eq(root.querySelector(".dial-rpm").textContent, "800 rpm", "the dial's words");
      ok(!/Paused/.test(root.textContent), "nothing still says paused");
    }))],

  ["a hand-off that carries no values still shows the last ones, dimmed, not a blank", () =>
    withHome(() => feed(LIVE()), async (root) => {
      feed(BARE);
      eq(root.querySelector(".hc-dial .g-value").textContent, "60", "the dial");
      const cool = root.querySelector('.sig[data-reading="coolant"]');
      eq([cool.dataset.state, cool.querySelector(".sig-v").textContent], ["paused", "194"], "coolant");
    })],

  ["but with nothing read before it, a bare hand-off says paused over no number at all", () =>
    withHome(() => feed(STALE), async (root) => {
      feed(BARE);
      const cool = root.querySelector('.sig[data-reading="coolant"]');
      eq([cool.dataset.state, cool.querySelector(".sig-v").textContent,
          cool.querySelector(".sig-note").textContent], ["paused", "", "Paused"], "coolant");
      eq(root.querySelector(".hc-dial .g-value").textContent, "—", "the dial");
    })],

  ["Begin is still never offered during a hand-off, whatever it now looks like", () =>
    withHome(() => feed(LIVE({ SPEED: 0, RPM: 0 })), async (root) => {
      feed(HANDOVER(LIVE({ SPEED: 0, RPM: 0 })));
      eq(root.querySelector(".dial-begin").style.visibility, "hidden", "no Begin mid-hand-off");
    })],
];

// ---------------------------------------------------------------- Gauges
const LAYOUT = { hero: "speed", heroKind: "digital", columns: 2, footer: "none",
                 tiles: ["coolant", "ima", "econ_now", "odometer"], kinds: { coolant: "dial" } };

async function withGauges(before, fn, layout = LAYOUT) {
  const was = api.driveLayout;
  api.driveLayout = async () => JSON.parse(JSON.stringify(layout));
  if (before) before();
  const m = await mounted(drive);
  try {
    for (let i = 0; i < 40 && m.root.querySelectorAll(".drive-tile").length < layout.tiles.length; i++) {
      await wait(50);
    }
    await fn(m.root);
  } finally { m.done(); api.driveLayout = was; }
}

const tiles = (root) => Object.fromEntries([...root.querySelectorAll(".drive-tile")].map((t) =>
  [t.querySelector(".drive-tile-k").textContent, t.dataset.state]));

const onGauges = [
  ["Gauges in a hand-off: the hero and every live readout are paused and dimmed, and the line says why", () =>
    withCss(() => withGauges(() => feed(LIVE()), async (root) => {
      feed(HANDOVER());
      const hero = root.querySelector(".drive-hero-slot");
      const speed = root.querySelector(".drive-speed");
      eq([hero.dataset.state, speed.textContent], ["paused", "60"], "the hero keeps the last speed, paused");
      eq(ink(speed), token(speed, "--faint"), "dimmed");
      eq(root.querySelector(".drive-state").textContent, "Paused · adapter in use", "the line under it");
      eq(tiles(root), { Coolant: "paused", IMA: "paused", Economy: "paused", Odometer: "live" },
         "each readout's state");
      const cool = root.querySelector(".drive-tile .g-value");
      eq(ink(cool, "fill"), token(cool, "--faint"), "the coolant dial is dimmed");
    }))],

  ["and back to live on the next fresh sample", () =>
    withCss(() => withGauges(() => feed(LIVE()), async (root) => {
      feed(HANDOVER());
      feed(LIVE({ SPEED: 48.28 }));
      const speed = root.querySelector(".drive-speed");
      eq([root.querySelector(".drive-hero-slot").dataset.state, speed.textContent], ["live", "30"]);
      ok(ink(speed) !== token(speed, "--faint"), "full ink");
      eq(root.querySelector(".drive-state").textContent, "", "moving: nothing under the hero");
      eq(tiles(root), { Coolant: "live", IMA: "live", Economy: "live", Odometer: "live" });
    }))],

  ["a stale no-daemon sample on Gauges is 'no link', never paused", () =>
    withGauges(() => feed(LIVE()), async (root) => {
      feed(STALE);
      eq(root.querySelector(".drive-state").textContent, "no link");
      eq(root.querySelector(".drive-hero-slot").dataset.state, "waiting");
      eq(root.querySelector(".drive-speed").textContent, "—");
    })],
];

// ---------------------------------------------------------------- a warning outlives the hand-off
//
// Dimming a paused value must not take its warning with it. A coolant
// temperature in the red just before the adapter was lent out is still the
// last thing known about the engine, and the driver still needs to see that it
// was red: tinted, dimmed with the rest, and labelled paused.
const WARNINGS = [
  // [reading, what puts it in its band, the tone]
  ["coolant", { COOLANT_TEMP: 103 }, "warn"],
  ["coolant", { COOLANT_TEMP: 108 }, "bad"],
  ["volts", { CONTROL_MODULE_VOLTAGE: 12.9 }, "warn"],   // RPM 2100: running
  ["volts", { CONTROL_MODULE_VOLTAGE: 12.1 }, "bad"],
  ["charge", { HYBRID_BATTERY_REMAINING: 30 }, "warn"],
  ["charge", { HYBRID_BATTERY_REMAINING: 15 }, "bad"],
  ["fuel", { FUEL_LEVEL: 15 }, "warn"],
  ["fuel", { FUEL_LEVEL: 8 }, "bad"],
  ["rpm", { RPM: 5800 }, "warn"],
];
const HOT = () => LIVE({ COOLANT_TEMP: 108, CONTROL_MODULE_VOLTAGE: 12.1, FUEL_LEVEL: 8 });

const warnings = [
  ["every reading in a warning band keeps its warning through a hand-off, paused and labelled", () => {
    const got = WARNINGS.map(([id, over]) => {
      const t = makeSignalTile(id);
      t.paint({}, LIVE(over));
      const before = t.node.dataset.tone;
      t.paint({}, HANDOVER(LIVE(over)));
      return [id, before, t.node.dataset.state, t.node.dataset.tone,
              t.node.querySelector(".sig-note").textContent];
    });
    eq(got, WARNINGS.map(([id, , tone]) => [id, tone, "paused", tone, "Paused"]));
  }],

  ["Home: hot coolant, then a long hand-off -- still tinted red, dimmed, and 'Paused · 2 min'", () =>
    withCss(() => withHome(() => feed(HOT()), async (root) => {
      feed(HANDOVER(HOT()));
      store.pausedSince = Date.now() - 142000;
      feed(HANDOVER(HOT()));
      const cool = root.querySelector('.sig[data-reading="coolant"]');
      const v = cool.querySelector(".sig-v");
      eq([cool.dataset.state, cool.dataset.tone, v.textContent,
          cool.querySelector(".sig-note").textContent],
         ["paused", "bad", "226", "Paused · 2 min"]);
      dimmedAs(v, "color", "--bad");
    }))],

  ["Gauges: a warning keeps its tone, dimmed, on a number, a bar and an arc alike", () =>
    withCss(() => withGauges(() => feed(HOT()), async (root) => {
      feed(HANDOVER(HOT()));
      const tile = (k) => [...root.querySelectorAll(".drive-tile")]
        .find((t) => t.querySelector(".drive-tile-k").textContent === k);
      const num = tile("Coolant").querySelector(".drive-tile-v");
      const bar = tile("12V system").querySelector(".g-bar");
      const arc = tile("Fuel").querySelector(".g-svg");
      eq([tile("Coolant").dataset.state, tile("12V system").dataset.state, tile("Fuel").dataset.state],
         ["paused", "paused", "paused"], "all three paused");
      eq([num.classList.contains("bad"), bar.dataset.tone, arc.dataset.tone], [true, "bad", "bad"],
         "and all three keep their warning");
      dimmedAs(num, "color", "--bad");
      dimmedAs(bar.querySelector(".g-bar-v"), "color", "--bad");
      dimmedAs(arc.querySelector(".g-value"), "fill", "--bad");
      eq(root.querySelector(".drive-state").textContent, "Paused · adapter in use", "and says why");
    }, { hero: "speed", heroKind: "digital", columns: 3, footer: "none",
         tiles: ["coolant", "volts", "fuel"], kinds: { volts: "bar", fuel: "arc" } }))],

  ["and a warning drawn live is still full strength, not the dimmed tint", () =>
    withCss(() => withHome(() => feed(HOT()), async (root) => {
      const v = root.querySelector('.sig[data-reading="coolant"] .sig-v');
      eq(ink(v), token(v, "--bad"), "live and red");
    }))],
];

// ---------------------------------------------------------------- a hand-off that never ends
//
// While a hand-off is real the daemon re-stamps `t` about every 0.3 s. One
// whose `t` has gone past records.LIVE_STALE (15 s) is a daemon that stopped
// mid-hand-off, and live.json will say "yielded" for ever. That is no data,
// not "Paused · adapter in use · 40 min". A hand-off with no `t` at all (the
// bare one) has only the store's own clock, and gives out at the longest a
// lease holds without a heartbeat (connect.YIELD_GRACE, 90 s).
const QUIET = (ago) => Object.assign(HANDOVER(), { t: now() - ago });

const deadDaemon = [
  ["a hand-off whose t is past 15 s old is a stopped daemon: waiting, not paused", () =>
    eq(["speed", "coolant", "charge"].map((id) => readingState(READINGS[id], QUIET(40))),
       ["waiting", "waiting", "waiting"])],
  ["one re-stamped within the last 15 s is still paused", () =>
    eq(["speed", "coolant"].map((id) => readingState(READINGS[id], QUIET(10))), ["paused", "paused"])],
  ["Home: a hand-off that stopped being re-stamped says No data, draws no number, and never Paused", () =>
    withHome(() => feed(LIVE()), async (root) => {
      feed(HANDOVER());
      eq(root.querySelector(".hc-dial .g-svg").dataset.state, "paused", "paused while it is fresh");
      feed(QUIET(40));
      eq(marked(root).filter(([, st]) => st !== "waiting"), [], "readings not waiting");
      eq(root.querySelector(".dial-rpm").textContent, "No data", "the dial's words");
      eq(root.querySelector(".hc-dial .g-value").textContent, "—", "no last speed on the arc");
      for (const s of root.querySelectorAll(".home-grid .sig")) {
        eq([s.querySelector(".sig-v").textContent, s.querySelector(".sig-note").textContent],
           ["", "Waiting for the car"], s.dataset.reading);
      }
      ok(!/Paused/.test(root.textContent), "nothing says paused");
    })],
  ["Gauges: a stopped hand-off is 'no link', with no last number", () =>
    withGauges(() => feed(LIVE()), async (root) => {
      feed(QUIET(40));
      eq([root.querySelector(".drive-hero-slot").dataset.state, root.querySelector(".drive-speed").textContent,
          root.querySelector(".drive-state").textContent], ["waiting", "—", "no link"]);
    })],
  ["the Cluster says 'no daemon' for it, as for any stale sample, not 'yielded' or paused", () =>
    withCss(async () => {
      feed(LIVE());
      const m = await mounted(cluster);
      try {
        feed(QUIET(40));
        eq([m.root.querySelector(".cluster").dataset.state, m.root.querySelector(".cl-speed").textContent,
            m.root.querySelector("#live-status").textContent], ["waiting", "—", "no daemon"]);
      } finally { m.done(); }
    })],
  ["a bare hand-off pauses, but not past the longest lease without a heartbeat", () =>
    withHome(() => feed(LIVE()), async (root) => {
      feed(BARE);
      const cool = root.querySelector('.sig[data-reading="coolant"]');
      eq([cool.dataset.state, cool.querySelector(".sig-v").textContent], ["paused", "194"], "at first");
      store.pausedSince = Date.now() - 91000;
      feed(BARE);
      eq([cool.dataset.state, cool.querySelector(".sig-v").textContent,
          cool.querySelector(".sig-note").textContent], ["waiting", "", "Waiting for the car"], "after 91 s");
      eq(root.querySelector(".dial-rpm").textContent, "No data", "the dial's words");
    })],
];

// ---------------------------------------------------------------- the IMA state
//
// The direction is the pack's own movement over a twelve-second window. A
// window that reaches back across a hand-off compares a fresh reading with one
// from before it -- which is how it said "charging" while the motor assisted.
const soc = (x, extra) => Object.assign(LIVE({ HYBRID_BATTERY_REMAINING: x }), extra || {});
const imaSays = (s) => READINGS.ima.get(s.values || {}, s).v;

const direction = [
  ["the charge/assist state never reaches back across a hand-off", () => {
    // Charging before it...
    for (const x of [58, 59, 60, 61]) imaSays(soc(x));
    eq(imaSays(soc(61.5)), "Charging", "regen before the hand-off");
    // ...the adapter lent out for a while, and the motor assisting after it.
    for (let i = 0; i < 8; i++) imaSays(HANDOVER(soc(61.5)));
    const after = [62, 61.7, 61.4].map((x) => imaSays(soc(x)));
    ok(!after.includes("Charging"), `said ${JSON.stringify(after)} while the pack was falling`);
    eq(after[2], "Assist", "and it says what is happening now");
  }],
  ["during the hand-off it shows the last state it had, for the renderer to dim", () => {
    for (const x of [50, 49, 48, 47]) imaSays(soc(x));
    eq(imaSays(HANDOVER(soc(47))), "Assist");
    eq(readingState(READINGS.ima, HANDOVER(soc(47))), "paused");
  }],
];

// ---------------------------------------------------------------- Cluster
const onCluster = [
  ["the Cluster in a hand-off: readouts paused and dimmed, and the status line says why", () =>
    withCss(async () => {
      feed(LIVE());
      const m = await mounted(cluster);
      try {
        feed(HANDOVER());
        const read = m.root.querySelector(".cluster");
        ok(read, "the readouts");
        eq(read.dataset.state, "paused", "their state");
        const speed = read.querySelector(".cl-speed");
        eq(speed.textContent, "60", "the last speed is still shown");
        eq(ink(speed), token(speed, "--faint"), "dimmed");
        eq(m.root.querySelector("#live-status").textContent, "Paused · adapter in use", "the status line");
        feed(LIVE({ SPEED: 0 }));
        eq([read.dataset.state, speed.textContent], ["live", "0"], "and live again on the next sample");
        ok(ink(speed) !== token(speed, "--faint"), "full ink");
      } finally { m.done(); }
    })],
];

// ---------------------------------------------------------------- Music
const onMusic = [
  ["the Music dock in a hand-off: its numbers dimmed, and it says paused", () =>
    withCss(async () => {
      feed(LIVE());
      const m = await mounted(music);
      try {
        const dock = m.root.querySelector(".music-dock");
        const speed = dock.querySelector(".music-v");
        const says = () => [...dock.querySelectorAll(".music-k")]
          .filter((k) => !k.hidden && /Paused/.test(k.textContent)).map((k) => k.textContent);
        feed(HANDOVER());
        eq([dock.dataset.state, speed.textContent], ["paused", "60"], "the last speed, paused");
        eq(ink(speed), token(speed, "--faint"), "dimmed");
        eq(says(), ["Paused · adapter in use"], "the words");
        feed(LIVE({ SPEED: 0 }));
        eq(["state" in dock.dataset, speed.textContent, says()], [false, "0", []], "live again");
      } finally { m.done(); }
    })],
];

// ---------------------------------------------------------------- the phone screen's rail
const onRail = [
  ["the phone screen's gauge rail goes dim for a hand-off, though its sample's t is fresh", () => {
    const r = gaugeRail();
    try {
      feed(LIVE());
      eq(r.el.dataset.live, "1", "live");
      feed(HANDOVER());
      eq(r.el.dataset.live, "0", "hand-off");
      feed(LIVE());
      eq(r.el.dataset.live, "1", "live again");
    } finally { r.destroy(); forget(); }
  }],
];

// ---------------------------------------------------------------- Battery (IMA view)
const onBattery = [
  ["the Battery screen in a hand-off shows the pack dimmed and paused, not a direction", () =>
    withCss(async () => {
      const was = { ima: api.ima, history: api.history };
      api.ima = async () => ({});
      api.history = async () => ({ rows: [] });
      store.car = { live: LIVE() };
      store.emit("car");
      const m = await mounted(ima);
      try {
        for (let i = 0; i < 40 && !m.root.querySelector(".charge-stage"); i++) await wait(50);
        ok(m.root.querySelector(".charge-stage"), "the charge dial drew");
        store.car = { live: HANDOVER() };
        store.emit("car");
        const stage = m.root.querySelector(".charge-stage");
        eq(stage.dataset.state, "paused", "the dial's state");
        eq(m.root.querySelector(".energy-state").textContent, "Paused · adapter in use", "the words");
        const pct = m.root.querySelector(".orbit-reading strong");
        eq(pct.textContent, "61%", "the last reading is still shown");
        eq(ink(pct), token(pct, "--faint"), "dimmed");
        store.car = { live: LIVE() };
        store.emit("car");
        eq(m.root.querySelector(".charge-stage").dataset.state, "live", "live again");
      } finally {
        m.done();
        Object.assign(api, was);
        if (!was.ima) delete api.ima;
      }
    })],
];

export default [...rules, ...tile, ...onHome, ...onGauges, ...warnings, ...deadDaemon, ...direction,
                ...onCluster, ...onMusic, ...onRail, ...onBattery];
