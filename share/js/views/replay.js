// Replay — walk back through a drive you already took.
//
// The tool could already record: `samples` holds every reading at about 1 Hz,
// and trips segment them into drives. What it could not do was give any of that
// back. You could see a chart of the last twenty minutes and nothing else --
// no way to step to the moment a fault set, no way to read the exact values at
// that instant, no way to get the numbers out.
//
// That gap matters most for the thing this tool is for. An intermittent fault
// is by definition not happening while you look at it; the whole value is in
// the recording. So: pick a drive, scrub to the moment, read every channel at
// that instant, and export the span if you want to work on it elsewhere.
//
// WHAT THIS SCREEN USED TO BE, AND WHY IT IS NOT THAT ANY MORE.
//
// It was a slider, a row of numbers, and a 260px black rectangle. The
// rectangle was meant to be the graph: it handed `scope()` channels with no
// `get` on them, so charts.js threw on the first trace and every drive replayed
// as an empty axis. The numbers updated, so the bug looked like a design --
// "the graph is coming" -- for as long as nobody opened the console.
//
// The replacement is not a bug fix with a chart bolted on. A replay screen has
// one job: put ONE time axis under everything the car said, and let you move a
// single playhead along it. So the traces, the event markers, the instruments
// and the numbers are all fed by the same index into the same rows, and the
// only thing that moves when you scrub is a line and a dozen text nodes.
//
// THREE DECISIONS THAT ARE LOAD-BEARING ON A 2017 DUAL-CORE LAPTOP.
//
//   Two canvases, not one. The traces are expensive and never change while you
//   scrub; the playhead is cheap and changes sixteen times a second. Drawing
//   them on one canvas means redrawing every trace to move a line -- which is
//   what the old cursor did, on top of a scope() call, per tick. The base layer
//   is repainted only when the span, the channel selection or the width
//   changes. The overlay is a clearRect and one stroke.
//
//   Min/max buckets, not points. /api/history clamps at 20000 rows; a canvas is
//   about 900 CSS pixels wide. Stroking 20000 line segments to fill 900 columns
//   costs twenty times what it buys and LOSES the spikes -- a 200 ms misfire dip
//   lands between two drawn points and disappears. One bucket per pixel column
//   carrying the min and max of everything that fell in it is both cheaper and
//   more honest: a spike one sample wide still draws.
//
//   Structure once, values in place. The hub view used to rebuild itself four
//   times a second and visibly blinked. Everything here that changes with the
//   playhead -- gauges, chips, clock, slider -- is a node captured at build time
//   and written to by paintNow(). draw() runs when the SHAPE changes, never on
//   a tick.
//
// WHAT IS ON THE AXIS IS WHAT IS IN THE RECORD, AND NOTHING ELSE.
//
// The sample stream is records.SAMPLE_COLS: rpm, speed, load, throttle,
// coolant, intake, maf, stft, ltft, timing, lphk, eff. There is no battery
// voltage in it. The daemon does log volts -- in the dtclog jsonl files, at one
// reading every five minutes, alongside the module fault counts -- but that is
// a different stream at a different cadence with no API in front of it, and a
// voltage lane drawn from it would be four points across an hour pretending to
// be a trace. So there is no volts lane here, and if you want one the honest
// place to start is teaching the daemon to put CONTROL_MODULE_VOLTAGE in
// `samples`, not teaching this file to interpolate.
//
// For the same reason the channel list is filtered per span: a lane is drawn
// only when that span actually carries readings for it. An axis with nothing on
// it is a claim that the car was silent, which is not the same as the tool not
// having asked.

import { h, clear, store, api, toast, clockOf, shortDate, mins, grouped,
         econVal, U, sevTone, dist } from "../core.js";
import { KINDS, makeGauge, kindsFor, normaliseKind } from "../gauges.js";
import { explain } from "../learn.js";

// Recordings saved before 2026-10-11 were named "447 min from 2 Sep 11:04";
// shown as Replay writes a session ("7h 27m from …"). The stored name stays.
function recordingLabel(label) {
  const m = /^(\d+) min from (.+)$/.exec(label || "");
  if (!m) return label || "Recording";
  return `${mins(Number(m[1]) * 60)} from ${m[2]}`;
}

// Which gauge face each channel wears, remembered per browser. Server-side
// would be wrong: this is a preference about a screen, not about the car, and
// the drive layout is already the one arrangement that has to travel.
const KIND_KEY = "omacar.replayKinds";
const PICK_KEY = "omacar.replayChannels";

const MAX_LANES = 6;      // six stacked lanes is where a lane stops being readable
const GAP = 20;           // seconds without a sample: a hole, not a flat line
const JUMP = 30;          // playback steps over a hole rather than sitting in it
const TICK = 100;         // ms between playback steps, whatever the rate
const PAD = { l: 8, r: 8, t: 8, b: 20 };

const asTemp = (c) => (c === null || c === undefined ? null : U.imperial ? c * 9 / 5 + 32 : c);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// ---------------------------------------------------------------- channels
//
// Deliberately NOT drive.js's TILES. Those read a live sample keyed by PID name
// (v.SPEED, v.COOLANT_TEMP) off the store; a replay row is a database row keyed
// by column (r.speed, r.coolant) and there is no live anything. Same SHAPE
// though -- label, a reader that returns display units, a scale function that
// follows the units toggle -- so makeGauge() takes these as they are and this
// file never learns what a dial is.
//
// `src` is the column presence is judged on. It differs from `key` twice, and
// both times because the column is DERIVED rather than measured: `lphk` is
// litres per hundred worked out from MAF and speed, and `eff` is the 0..1 band
// the ambient ring reads. Both are honest numbers; neither came off the bus.
const CHANNELS = [
  {
    key: "speed", src: "speed", label: "Speed",
    unit: () => U.units.speed,
    read: (r) => (r.speed == null ? null : r.speed * U.units.km),
    fmt: (v) => String(Math.round(v)),
    scale: () => ({ min: 0, max: U.imperial ? 140 : 220, step: U.imperial ? 20 : 40 }),
  },
  {
    key: "rpm", src: "rpm", label: "Engine speed",
    unit: () => "rpm",
    read: (r) => (r.rpm == null ? null : r.rpm),
    fmt: (v) => grouped(v),
    tone: (v) => (v > 6200 ? "bad" : v > 5500 ? "warn" : ""),
    // Labelled in thousands, as every tachometer is.
    scale: () => ({ min: 0, max: 7000, step: 1000,
                    tick: (x) => String(Math.round(x / 1000)),
                    bands: [{ from: 5500, to: 6200, tone: "warn" },
                            { from: 6200, tone: "bad" }] }),
  },
  {
    key: "load", src: "load", label: "Engine load",
    unit: () => "%",
    read: (r) => (r.load == null ? null : r.load),
    fmt: (v) => v.toFixed(0),
    scale: () => ({ min: 0, max: 100, step: 25 }),
  },
  {
    key: "throttle", src: "throttle", label: "Throttle",
    unit: () => "%",
    read: (r) => (r.throttle == null ? null : r.throttle),
    fmt: (v) => v.toFixed(0),
    scale: () => ({ min: 0, max: 100, step: 25 }),
  },
  {
    key: "coolant", src: "coolant", label: "Coolant",
    unit: () => U.units.temp,
    read: (r) => (r.coolant == null ? null : asTemp(r.coolant)),
    fmt: (v) => String(Math.round(v)),
    tone: (v) => (v > asTemp(105) ? "bad" : v > asTemp(100) ? "warn" : ""),
    scale: () => ({ min: asTemp(40), max: asTemp(120), step: U.imperial ? 40 : 20,
                    bands: [{ from: asTemp(100), to: asTemp(105), tone: "warn" },
                            { from: asTemp(105), tone: "bad" }] }),
  },
  {
    key: "intake", src: "intake", label: "Intake air",
    unit: () => U.units.temp,
    read: (r) => (r.intake == null ? null : asTemp(r.intake)),
    fmt: (v) => String(Math.round(v)),
    scale: () => ({ min: asTemp(-10), max: asTemp(70), step: U.imperial ? 40 : 20 }),
  },
  {
    key: "maf", src: "maf", label: "Air flow",
    unit: () => "g/s",
    read: (r) => (r.maf == null ? null : r.maf),
    fmt: (v) => v.toFixed(1),
    scale: () => ({ min: 0, max: 60, step: 15 }),
  },
  {
    key: "stft", src: "stft", label: "Short trim",
    unit: () => "%",
    read: (r) => (r.stft == null ? null : r.stft),
    fmt: (v) => (v > 0 ? "+" : "") + v.toFixed(1),
    // Ten per cent is the number a technician reaches for and twenty is the one
    // that ends the argument. Trouble is at BOTH ends: a trim pinned negative
    // is as wrong as one pinned positive, which is why this is two bands and
    // not a ceiling.
    tone: (v) => (Math.abs(v) >= 20 ? "bad" : Math.abs(v) >= 10 ? "warn" : ""),
    scale: () => ({ min: -25, max: 25, step: 25,
                    bands: [{ to: -20, tone: "bad" }, { from: -20, to: -10, tone: "warn" },
                            { from: 10, to: 20, tone: "warn" }, { from: 20, tone: "bad" }] }),
  },
  {
    key: "ltft", src: "ltft", label: "Long trim",
    unit: () => "%",
    read: (r) => (r.ltft == null ? null : r.ltft),
    fmt: (v) => (v > 0 ? "+" : "") + v.toFixed(1),
    tone: (v) => (Math.abs(v) >= 20 ? "bad" : Math.abs(v) >= 10 ? "warn" : ""),
    scale: () => ({ min: -25, max: 25, step: 25,
                    bands: [{ to: -20, tone: "bad" }, { from: -20, to: -10, tone: "warn" },
                            { from: 10, to: 20, tone: "warn" }, { from: 20, tone: "bad" }] }),
  },
  {
    key: "timing", src: "timing", label: "Timing",
    unit: () => "°",
    read: (r) => (r.timing == null ? null : r.timing),
    fmt: (v) => v.toFixed(1),
    scale: () => ({ min: -30, max: 60, step: 30 }),
  },
  {
    key: "econ", src: "lphk", label: "Economy",
    unit: () => U.units.econ,
    // Reciprocal in imperial, so the conversion is core's and not a multiply.
    read: (r) => (r.lphk == null ? null : econVal(r.lphk)),
    fmt: (v) => v.toFixed(1),
    scale: () => (U.imperial ? { min: 0, max: 60, step: 15 } : { min: 0, max: 20, step: 5 }),
  },
  {
    key: "eff", src: "eff", label: "Efficiency",
    unit: () => "%",
    // Not a PID. The 0..1 index the ambient ring reads, kept because it is the
    // one channel that carries through the parked rows either side of a drive.
    read: (r) => (r.eff == null ? null : r.eff * 100),
    fmt: (v) => v.toFixed(0),
    scale: () => ({ min: 0, max: 100, step: 25 }),
  },
];

const byKey = (k) => CHANNELS.find((c) => c.key === k);
const DEFAULT_PICK = ["speed", "rpm", "coolant", "stft"];

// The face each channel opens on, until you click it onto another.
//
// Not all digital. A number above a number is what the chips underneath already
// are, and the first build of this row proved it: four large digits over four
// small ones, saying the same thing twice. The face is doing work only when it
// says something the digit cannot -- where this reading sits in the range it is
// allowed, which for a trim is "which side of zero and how far" (a bar anchored
// at nought) and for a tachometer is "how near the limit" (a dial with the
// redline painted on the scale).
const DEFAULT_KINDS = {
  speed: "dial", rpm: "dial", coolant: "arc", intake: "arc", econ: "arc",
  load: "bar", throttle: "bar", maf: "bar", stft: "bar", ltft: "bar",
  timing: "bar", eff: "bar",
};

function savedMap(key) {
  try { return JSON.parse(localStorage.getItem(key) || "{}") || {}; }
  catch { return {}; }
}
function saveMap(key, v) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* private mode */ }
}

// The clock on the axis. Seconds appear only when the span is short enough for
// them to mean something -- across three hours they are six identical digits.
function stampAt(t, span) {
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return span < 1200
    ? `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    : `${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function replay(root, { arg } = {}) {
  let spans = [];          // trips, saved recordings and sessions, newest first
  let chosen = null;
  let rows = [];
  let events = [];         // scans, alerts, clears and fault sightings in the span
  let loading = false;
  let idx = 0;             // index into rows: the one source of "when"
  let playAt = 0;          // playback clock, seconds, so a rate means real time
  let playing = false;
  let rate = 1;
  let timer = null;
  let raf = 0;
  let present = [];        // channel keys this span actually carries
  let picked = [];
  let kinds = savedMap(KIND_KEY);

  const wrap = h("div.replay");
  root.appendChild(wrap);

  // Live handles into the built DOM. Everything a tick touches is here, so a
  // tick is a handful of property writes and never a querySelector.
  let stageEl = null, base = null, over = null, ribbon = null, slider = null;
  let spansHost = null;
  let atEl = null, elapsedEl = null, playBtn = null;
  let chips = new Map();     // key -> the value node inside its chip
  let chipBtns = new Map();  // key -> the chip itself, for the lit/unlit class
  let gaugeRow = null;
  let gauges = [];           // { ch, g }
  let geo = null;          // lane geometry from the last base paint
  let ro = null, resizeAt = 0, lastW = 0;

  // ---- colour ------------------------------------------------------------
  //
  // A canvas cannot read a custom property, and copying hexes into JavaScript
  // is exactly how a colour quietly stops matching the theme -- theme.py
  // derives every token through contrast floors, and a literal in here would
  // survive a theme change looking wrong. So the palette lives in replay.css
  // as --rp-lane-N and this asks the browser what it resolved them to. One
  // getComputedStyle per colour per BASE repaint; none per tick.
  const probe = h("i.rp-probe", { "aria-hidden": "true" });
  wrap.appendChild(probe);
  let inkCache = {};
  function ink(expr) {
    if (inkCache[expr]) return inkCache[expr];
    probe.style.color = expr;
    const got = getComputedStyle(probe).color || "#888";
    inkCache[expr] = got;
    return got;
  }
  const tokenInk = (name) => ink(`var(${name})`);
  const mono = () =>
    getComputedStyle(document.documentElement).getPropertyValue("--mono").trim()
    || "ui-monospace, monospace";

  // ---- what there is to replay -------------------------------------------
  async function loadSpans() {
    let recs = [];
    try {
      const [hist, book] = await Promise.all([
        api.trips(50),
        api.records({ n: 500 }),
      ]);
      recs = book.records || [];
      const trips = (hist.trips || [])
        // A trip needs a beginning and an end that are not each other. The
        // fixture row the test suite leaves behind has t0 = 1000, which dates
        // it to 1970 and puts it at the bottom of a list sorted by time.
        .filter((t) => t.t0 > 1e9 && t.t1 > t.t0)
        .map((t) => ({
          kind: "drive", t0: t.t0, t1: t.t1,
          // In the owner's units, as every other distance: this said "116.8 km"
          // on a car whose screens all read miles.
          label: `Drive · ${t.km != null ? dist(t.km) : "?"}`,
          detail: `${shortDate(t.t0)}  ${clockOf(t.t0)} — ${clockOf(t.t1)}`,
        }));
      const movies = recs
        .filter((r) => r.kind === "movie" && r.t0 && r.t1)
        .map((r) => ({
          kind: "recording", t0: r.t0, t1: r.t1,
          label: recordingLabel(r.label),
          detail: `${shortDate(r.t0)}  ${clockOf(r.t0)} — ${clockOf(r.t1)}`,
        }));
      spans = [...trips, ...movies].sort((a, b) => b.t0 - a.t0);
      bookRecords = recs;
      draw();
      openArg();

      // The sessions arrive SECOND, and on their own, because finding them costs
      // a thinned pass over a month of samples: /api/history takes four seconds
      // over this car's record on the machine it runs on. Awaited inline that
      // was four seconds of empty screen before the trips it already had could
      // be shown — the picker blocked on the slowest thing in it. Now the exact
      // lists paint immediately and the derived ones drop in when they are
      // ready, into the picker alone, so a span you are already replaying is not
      // torn down underneath you.
      const found = await sessions(spans);
      if (!found.length) return;
      spans = [...spans, ...found].sort((a, b) => b.t0 - a.t0);
      renderSpans();
      openArg();
    } catch (e) {
      spans = [];
      draw();
      toast("Could not list drives: " + (e.message || e), "bad");
    }
  }

  // #replay/<epoch> opens a span directly, which is how you hand somebody else
  // the moment rather than a description of it.
  function openArg() {
    if (!arg || chosen) return;
    const want = Number(arg);
    const hit = spans.find((s) => Math.abs(s.t0 - want) < 2)
             || spans.find((s) => want >= s.t0 && want <= s.t1);
    if (hit) open(hit);
  }

  let bookRecords = [];

  // Sessions: every continuous run of recording, found from the record itself.
  //
  // Trips are segmented by movement, so everything the daemon watched while the
  // car was parked -- which is most of what an intermittent fault happens in --
  // belongs to no trip and was unreachable from this screen. This asks for a
  // thinned pass over the last month and cuts it wherever the record goes quiet
  // for five minutes.
  //
  // Thinned, so it is approximate BY CONSTRUCTION: /api/history decimates to fit
  // the row limit, and once the record is long enough that the stride exceeds
  // the gap threshold, short sessions merge into their neighbours. Trips and
  // saved recordings are exact and are listed from their own tables; this is the
  // net that catches what they miss.
  async function sessions(known) {
    try {
      const r = await api.history({ from: Math.floor(Date.now() / 1000) - 30 * 86400, n: 1500 });
      const all = r.rows || r.samples || [];
      if (all.length < 2) return [];
      const cut = 300;
      const out = [];
      let start = all[0].t, prev = all[0].t;
      for (const s of all) {
        if (s.t - prev > cut) { out.push([start, prev]); start = s.t; }
        prev = s.t;
      }
      out.push([start, prev]);
      return out
        .filter(([t0, t1]) => t1 - t0 > 120)
        .filter(([t0, t1]) => !known.some((k) => Math.abs(k.t0 - t0) < 90 && Math.abs(k.t1 - t1) < 90))
        .map(([t0, t1]) => ({
          kind: "session", t0, t1,
          label: `Session · ${mins(t1 - t0)}`,
          detail: `${shortDate(t0)}  ${clockOf(t0)} — ${clockOf(t1)}`,
        }));
    } catch {
      return [];   // the list is still useful without them
    }
  }

  async function open(span) {
    stop();
    chosen = span;
    rows = [];
    events = [];
    idx = 0;
    loading = true;
    draw();
    try {
      // n is the server's ceiling (lib/api.py clamps to 20000), not a number
      // picked here. Ask for more and records.samples() decimates to fit, so a
      // three-hour span IS thinned -- the min/max buckets below are what keep a
      // one-sample spike visible after that.
      const r = await api.history({ from: span.t0, to: span.t1, n: 20000 });
      rows = (r.rows || r.samples || []).filter((x) => x && x.t != null);
    } catch (e) {
      toast("Could not load that span: " + (e.message || e), "bad");
    }
    loading = false;
    presentChannels();
    buildEvents();
    draw();
  }

  // A channel is offered only if this span carries it. Checked on the raw
  // column rather than through read(), so a genuine zero counts and a unit
  // conversion cannot turn a missing reading into one.
  function presentChannels() {
    present = CHANNELS
      .filter((c) => rows.some((r) => r[c.src] !== null && r[c.src] !== undefined))
      .map((c) => c.key);
    // The selection is remembered, but the SPAN decides what survives it: a
    // drive that never reported air flow must not open with an empty air-flow
    // lane just because the last one did.
    const saved = savedMap(PICK_KEY).keys;
    const want = Array.isArray(saved) && saved.length ? saved : DEFAULT_PICK;
    picked = want.filter((k) => present.includes(k)).slice(0, MAX_LANES);
    if (!picked.length) picked = present.slice(0, Math.min(4, MAX_LANES));
  }

  // ---- the events on the axis --------------------------------------------
  //
  // The point of putting these on the same axis as the traces is the question a
  // scan tool answers badly: what was the car DOING when the code set. A tablet
  // shows you a fault list and a graph on two different screens and leaves the
  // correlation to your memory.
  //
  // Everything here is a timestamp something else wrote. Scans and clears come
  // from the record book with their own labels; faults come from the snapshot's
  // first_seen/last_seen. Nothing is inferred: if the record has no event in
  // this span, the ribbon says so rather than decorating the axis.
  function buildEvents() {
    if (!chosen) { events = []; return; }
    const inSpan = (t) => t != null && t >= chosen.t0 && t <= chosen.t1;
    const out = [];
    for (const r of bookRecords) {
      if (!inSpan(r.at)) continue;
      const p = r.payload || {};
      if (r.kind === "scan") {
        const codes = (p.totals && p.totals.codes) || 0;
        out.push({ t: r.at, tone: codes ? "bad" : "ok",
                   label: codes ? `Scan · ${codes} code(s)` : "Scan · clean" });
      } else if (r.kind === "clear") {
        out.push({ t: r.at, tone: "info", label: r.label || "Codes cleared" });
      } else if (r.kind === "alert") {
        const u = p.urgency;
        out.push({ t: r.at, tone: u === "high" ? "bad" : u === "medium" ? "warn" : "info",
                   label: r.label || "Alert" });
      }
    }
    const faults = (store.car && store.car.faults) || [];
    for (const f of faults) {
      if (inSpan(f.first_seen)) {
        out.push({ t: f.first_seen, tone: sevTone(f.severity) || "warn",
                   label: `${f.code} first seen` });
      }
      if (inSpan(f.last_seen) && Math.abs(f.last_seen - f.first_seen) > 60) {
        out.push({ t: f.last_seen, tone: sevTone(f.severity) || "warn",
                   label: `${f.code} last seen` });
      }
    }
    events = cluster(out.sort((a, b) => a.t - b.t));
  }

  // Twenty markers inside one percent of the axis is a blob, not a timeline.
  //
  // The night the adapter kept dropping put sixty "stopped watching / is
  // watching" pairs into a twenty-three hour recording, and drawn one per event
  // they merged into a solid bar over the graph -- less legible than no markers
  // at all. Grouped by position, the same night is four markers that each say
  // how many they stand for, and the two scans that matter are still their own
  // dot because nothing else happened near them.
  //
  // The worst tone in a group wins the colour: a cluster containing one code
  // sighting and nine reconnections is a cluster you want to look at.
  const RANK = { bad: 3, warn: 2, ok: 1, info: 0 };
  function cluster(list) {
    if (!rows.length || !list.length) return list.slice(0, 60);
    const t0 = rows[0].t;
    const span = Math.max(1, rows[rows.length - 1].t - t0);
    const near = span * 0.012;          // about ten pixels on a normal window
    const out = [];
    for (const ev of list) {
      const last = out[out.length - 1];
      if (last && ev.t - last.t <= near) {
        last.n += 1;
        if (RANK[ev.tone] > RANK[last.tone]) { last.tone = ev.tone; last.label = ev.label; }
        continue;
      }
      out.push({ ...ev, n: 1 });
    }
    return out.slice(0, 60);
  }

  // ---- transport ---------------------------------------------------------
  function stop() {
    playing = false;
    if (timer) { clearInterval(timer); timer = null; }
    if (playBtn) playBtn.textContent = "Play";
  }

  function play() {
    if (rows.length < 2) return;
    stop();
    playing = true;
    if (playBtn) playBtn.textContent = "Pause";
    if (idx >= rows.length - 1) idx = 0;
    playAt = rows[idx].t;
    // Advance by wall clock, not by row. Samples land at roughly 1.3 s here and
    // never exactly, so stepping one row per tick made "1×" mean "whatever the
    // adapter managed that day" -- and made a rate a lie you could not check.
    timer = setInterval(() => {
      playAt += (TICK / 1000) * rate;
      const last = rows.length - 1;
      if (playAt >= rows[last].t) { idx = last; paintCursor(); stop(); return; }
      let j = idx;
      while (j < last && rows[j + 1].t <= playAt) j++;
      // A parked hour inside a session is not worth playing through.
      if (j < last && rows[j + 1].t - rows[j].t > JUMP) { j++; playAt = rows[j].t; }
      idx = j;
      paintCursor();
    }, TICK);
  }

  function seek(t) {
    if (!rows.length) return;
    idx = nearest(t);
    playAt = rows[idx].t;
    paintCursor();
  }

  function step(d) {
    if (!rows.length) return;
    idx = clamp(idx + d, 0, rows.length - 1);
    playAt = rows[idx].t;
    paintCursor();
  }

  // Binary search: at 20000 rows a linear scan per pointer-move is 20000
  // comparisons a frame for an answer fourteen comparisons can give.
  function nearest(t) {
    let lo = 0, hi = rows.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (rows[mid].t < t) lo = mid + 1; else hi = mid;
    }
    if (lo > 0 && Math.abs(rows[lo - 1].t - t) < Math.abs(rows[lo].t - t)) lo -= 1;
    return lo;
  }

  // ---- the base layer: traces, grid, event lines --------------------------
  function lanes() {
    return picked.map((k) => byKey(k)).filter(Boolean);
  }

  function layout() {
    const n = lanes().length;
    const w = Math.max(240, (stageEl && stageEl.clientWidth) || 640);
    // Taller lanes when there are few: one channel on its own gets room to show
    // its shape, six get enough to still be told apart.
    const laneH = n ? clamp(Math.round(380 / n), 54, 120) : 0;
    return { w, laneH, h: PAD.t + laneH * n + PAD.b, n };
  }

  function sizeTo(canvas, w, hh) {
    const dpr = clamp(window.devicePixelRatio || 1, 1, 2);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(hh * dpr);
    canvas.style.height = hh + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hh);
    return ctx;
  }

  function drawBase() {
    if (!base || !rows.length) return;
    inkCache = {};                       // a theme may have changed under us
    const { w, laneH, h: hh, n } = layout();
    lastW = w;
    stageEl.style.height = hh + "px";
    const ctx = sizeTo(base, w, hh);
    sizeTo(over, w, hh);
    if (!n) { geo = null; drawCursor(); return; }

    const t0 = rows[0].t, t1 = rows[rows.length - 1].t;
    const span = Math.max(1, t1 - t0);
    const plotW = w - PAD.l - PAD.r;
    const x = (t) => PAD.l + plotW * ((t - t0) / span);

    const hair = tokenInk("--hair");
    const ghost = tokenInk("--ghost");
    const faint = tokenInk("--faint");
    const panelInk = tokenInk("--panel-2");
    ctx.font = "10px " + mono();
    ctx.textBaseline = "top";

    // Time gridlines, six of them however long the span is.
    ctx.strokeStyle = hair;
    ctx.lineWidth = 1;
    ctx.textAlign = "center";
    for (let i = 0; i <= 6; i++) {
      const px = PAD.l + plotW * (i / 6);
      ctx.beginPath();
      ctx.moveTo(px, PAD.t);
      ctx.lineTo(px, hh - PAD.b);
      ctx.stroke();
      ctx.fillStyle = ghost;
      ctx.fillText(stampAt(t0 + span * (i / 6), span),
                   clamp(px, 26, w - 26), hh - PAD.b + 5);
    }

    // Event lines BEHIND the traces, so a code and a spike line up by eye
    // without a legend. Only the ones that carry a verdict get the full height:
    // a clean scan and a reconnection are worth a tick at the top and nothing
    // through the graph, or an evening of dropped connections buries the traces
    // it was supposed to annotate.
    for (const ev of events) {
      const px = x(ev.t);
      const loud = ev.tone === "bad" || ev.tone === "warn";
      ctx.save();
      ctx.globalAlpha = loud ? 0.45 : 0.6;
      ctx.strokeStyle = tokenInk(ev.tone === "bad" ? "--bad"
                               : ev.tone === "warn" ? "--warn"
                               : ev.tone === "ok" ? "--ok" : "--info");
      ctx.lineWidth = 1;
      if (loud) ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(px, PAD.t);
      ctx.lineTo(px, loud ? hh - PAD.b : PAD.t + 7);
      ctx.stroke();
      ctx.restore();
    }

    // One bucket per pixel column, carrying the min and max that fell in it.
    const cols = Math.max(2, Math.round(plotW));
    const secsPerCol = span / cols;
    const gapCols = Math.max(2, Math.ceil(GAP / Math.max(0.001, secsPerCol)));
    const lo32 = new Float32Array(cols), hi32 = new Float32Array(cols);
    const has = new Uint8Array(cols);
    const any = new Uint8Array(cols);   // did ANY drawn lane have a reading here

    // Was a row WRITTEN in this column, regardless of whether anything in it
    // was readable? That is a different question from `any` below, which asks
    // whether a drawn lane had a value — and telling the two apart is the only
    // way to distinguish a parked car from a logger that was not running.
    // Lane-independent, so it is filled once here rather than per lane.
    const rowIn = new Uint8Array(cols);
    for (const r of rows) {
      rowIn[clamp(Math.round((cols - 1) * ((r.t - t0) / span)), 0, cols - 1)] = 1;
    }

    geo = { t0, t1, span, plotW, w, h: hh, laneH, x, lanes: [] };

    lanes().forEach((ch, i) => {
      lo32.fill(0); hi32.fill(0); has.fill(0);
      let vmin = Infinity, vmax = -Infinity;
      for (const r of rows) {
        const v = ch.read(r);
        if (v === null || v === undefined || Number.isNaN(v)) continue;
        const c = clamp(Math.round((cols - 1) * ((r.t - t0) / span)), 0, cols - 1);
        any[c] = 1;
        if (!has[c]) { has[c] = 1; lo32[c] = v; hi32[c] = v; }
        else { if (v < lo32[c]) lo32[c] = v; if (v > hi32[c]) hi32[c] = v; }
        if (v < vmin) vmin = v;
        if (v > vmax) vmax = v;
      }

      const top = PAD.t + laneH * i;
      const lane = { ch, top, empty: vmin === Infinity };

      // The lane's own baseline, so stacked channels read as several
      // instruments rather than as one tangle.
      ctx.strokeStyle = hair;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD.l, top + laneH - 2.5);
      ctx.lineTo(w - PAD.r, top + laneH - 2.5);
      ctx.stroke();

      if (lane.empty) { geo.lanes.push(lane); return; }

      // The drawn range is the data's, padded, not the gauge's: a coolant lane
      // scaled 40..120 draws a warm engine as a flat line through the middle
      // and hides the four degrees that were the whole question. The gauge
      // beside it carries the absolute scale.
      let lo = vmin, hi = vmax;
      if (hi - lo < 1e-6) { lo -= 0.5; hi += 0.5; }
      const padV = (hi - lo) * 0.14;
      lo -= padV; hi += padV;
      const y = (v) => top + laneH - 5 - (laneH - 16) * ((v - lo) / (hi - lo));
      Object.assign(lane, { lo, hi, y });

      ctx.beginPath();
      let prev = -1;
      for (let c = 0; c < cols; c++) {
        if (!has[c]) continue;
        const px = PAD.l + plotW * (c / (cols - 1));
        if (prev < 0 || c - prev > gapCols) ctx.moveTo(px, y(hi32[c]));
        else ctx.lineTo(px, y(hi32[c]));
        ctx.lineTo(px, y(lo32[c]));
        prev = c;
      }
      const tint = tokenInk(`--rp-lane-${(i % 6) + 1}`);
      lane.tint = tint;
      ctx.strokeStyle = tint;
      ctx.lineWidth = 1.5;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();

      // The captions go on LAST, over a knocked-back backing. Drawn under the
      // trace they vanished into it; drawn over it without the backing, a busy
      // speed lane ran a blue line straight through the word SPEED.
      const caption = (text, align) => {
        const tw = ctx.measureText(text).width;
        const tx = align === "left" ? PAD.l + 3 : w - PAD.r - 3 - tw;
        ctx.globalAlpha = 0.82;
        ctx.fillStyle = panelInk;
        ctx.fillRect(tx - 3, top + 1, tw + 6, 12);
        ctx.globalAlpha = 1;
        ctx.fillStyle = align === "left" ? faint : ghost;
        ctx.fillText(text, tx, top + 2);
      };
      ctx.textAlign = "left";
      ctx.font = "10px " + mono();
      caption(ch.label.toUpperCase(), "left");
      caption(`${ch.fmt(vmin)} – ${ch.fmt(vmax)} ${ch.unit()}`, "right");

      geo.lanes.push(lane);
    });

    // EMPTY IS A FINDING, AND IT HAS TWO CAUSES WORTH TELLING APART.
    //
    // A long stretch of graph with nothing drawn on it is the thing that made
    // this screen look broken in the first place, and left unexplained it reads
    // as "the tool lost your data". It is almost always one of two things, and
    // the record can say which:
    //
    //   no samples   nothing was written at all — the daemon was not running,
    //                or the adapter was unplugged.
    //   no readings  rows were written and every PID in them was empty. On this
    //                car that is the shape of a key-off: the logger keeps its
    //                cadence, the engine module stops answering.
    //
    // Both are drawn from the columns rather than from the row timestamps, so a
    // stretch counts as empty only when nothing you asked to SEE was recorded —
    // which is the question the blank space actually raises.
    //
    // AND A RUN IS SPLIT WHERE THE CAUSE CHANGES.
    //
    // The first version walked an empty run to its end and then asked, once,
    // which of the two it was. On the owner's own recording that merged a real
    // outage into the key-off stretch beside it and labelled all 16.8 hours
    // "no readings" — reporting sixteen hours of genuinely lost data as a car
    // sitting parked. The run now stops wherever `rowIn` flips, so each cause
    // gets its own band, its own word and its own duration, and a gap in the
    // record can no longer hide behind a gap in the driving.
    ctx.save();
    ctx.font = "10px " + mono();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const minRun = 24;                      // px; shorter than this is just a dip
    for (let c = 0; c < cols; c++) {
      if (any[c]) continue;
      let e = c;
      // Stop at the boundary where the CAUSE changes, not only where data
      // resumes. Without the rowIn test these two runs are one run.
      while (e + 1 < cols && !any[e + 1] && rowIn[e + 1] === rowIn[c]) e++;
      const runW = e - c + 1;
      if (runW >= minRun) {
        const a = PAD.l + plotW * (c / (cols - 1));
        const b = PAD.l + plotW * (e / (cols - 1));
        ctx.globalAlpha = 0.5;
        ctx.fillStyle = tokenInk("--ground");
        ctx.fillRect(a, PAD.t, b - a, hh - PAD.b - PAD.t);
        ctx.globalAlpha = 1;
        if (b - a > 104) {
          const ta = t0 + span * (c / (cols - 1));
          const tb = t0 + span * (e / (cols - 1));
          // Read straight off rowIn, which is uniform across the run by
          // construction now. The old `nearest(tb) - nearest(ta) > 2` counted
          // rows across the WHOLE merged run, so two rows at one end were
          // enough to call sixteen hours of silence "no readings".
          ctx.fillStyle = ghost;
          ctx.fillText(`${rowIn[c] ? "no readings" : "no samples"} · ${mins(tb - ta)}`,
                       (a + b) / 2, hh / 2);
        }
      }
      c = e;
    }
    ctx.restore();

    drawCursor();
  }

  // ---- the overlay: the playhead, and nothing else ------------------------
  function drawCursor() {
    if (!over || !geo) return;
    const dpr = clamp(window.devicePixelRatio || 1, 1, 2);
    const ctx = over.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, geo.w, geo.h);
    const row = rows[idx];
    if (!row) return;
    const px = geo.x(row.t);

    ctx.strokeStyle = tokenInk("--bright");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(px + 0.5, PAD.t);
    ctx.lineTo(px + 0.5, geo.h - PAD.b);
    ctx.stroke();

    ctx.font = "11px " + mono();
    ctx.textBaseline = "middle";
    const panel = tokenInk("--panel-2");
    for (const lane of geo.lanes) {
      if (lane.empty) continue;
      const v = lane.ch.read(row);
      if (v === null || v === undefined || Number.isNaN(v)) continue;
      const py = clamp(lane.y(v), lane.top + 2, lane.top + geo.laneH - 3);
      ctx.fillStyle = lane.tint;
      ctx.beginPath();
      ctx.arc(px, py, 2.8, 0, Math.PI * 2);
      ctx.fill();
      // The number rides the playhead, flipping side near the right edge so it
      // never runs off the canvas at the end of a drive.
      const text = lane.ch.fmt(v);
      const wText = ctx.measureText(text).width;
      const right = px + 7 + wText + 5 < geo.w;
      const tx = right ? px + 7 : px - 7 - wText - 5;
      ctx.globalAlpha = 0.86;
      ctx.fillStyle = panel;
      ctx.fillRect(tx - 2, py - 8, wText + 6, 16);
      ctx.globalAlpha = 1;
      ctx.fillStyle = lane.tint;
      ctx.fillText(text, tx + 1, py);
    }
  }

  // ---- everything that moves with the playhead ----------------------------
  function paintCursor() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; paintNow(); });
  }

  function paintNow() {
    const row = rows[idx];
    if (!row) return;
    if (slider && Number(slider.value) !== Math.round(row.t - rows[0].t)) {
      slider.value = String(Math.round(row.t - rows[0].t));
    }
    if (atEl) atEl.textContent = stampAt(row.t, 0);
    if (elapsedEl) elapsedEl.textContent = mins(row.t - rows[0].t) + " in";
    for (const [key, node] of chips) {
      const ch = byKey(key);
      const v = ch ? ch.read(row) : null;
      node.textContent = v === null || v === undefined || Number.isNaN(v) ? "—" : ch.fmt(v);
    }
    for (const { ch, g } of gauges) {
      const v = ch.read(row);
      const has = v !== null && v !== undefined && !Number.isNaN(v);
      g.update({ v: has ? ch.fmt(v) : "—", n: ch.unit(),
                 tone: has && ch.tone ? ch.tone(v) : "" }, has ? v : null);
    }
    drawCursor();
  }

  // ---- instruments ---------------------------------------------------------
  //
  // The same faces drive mode uses, from gauges.js, rather than a second
  // vocabulary invented here: a dial that means one thing at seventy miles an
  // hour and another at the kitchen table is worse than no dial.
  //
  // Its own function because changing the channel selection must NOT go through
  // draw(). On the longest recording in this car's book -- twenty-three hours,
  // fourteen thousand rows -- a full rebuild of the view measured 357 ms, and
  // almost none of that was the graph: it was throwing away and rebuilding
  // twelve span cards, twelve chips and six dials' worth of SVG to change which
  // four of them were highlighted. Repainting only what the click actually
  // changed puts the same toggle under a tenth of that.
  function buildGauges() {
    if (!gaugeRow) return;
    clear(gaugeRow);
    gauges = [];
    for (const ch of lanes()) {
      // makeGauge wants a resolved scale OBJECT; the catalogue stores a scale
      // FUNCTION so it can follow the units toggle. Same resolve drive.js does.
      const def = { ...ch, scale: ch.scale ? ch.scale() : null };
      const kind = normaliseKind(kinds[ch.key] || DEFAULT_KINDS[ch.key], def);
      const g = makeGauge(kind, def);
      const slot = h("div.rp-gauge-slot");
      slot.appendChild(g.el);
      const allowed = kindsFor(def);
      gaugeRow.appendChild(h("button.rp-gauge", {
        type: "button", data: { kind },
        title: `${KINDS[kind].label} — ${KINDS[kind].note}  ·  click for the next face`,
        onclick: () => {
          kinds[ch.key] = allowed[(allowed.indexOf(kind) + 1) % allowed.length];
          saveMap(KIND_KEY, kinds);
          buildGauges();
          paintNow();
        },
      }, h("div.rp-gk", ch.label), slot));
      gauges.push({ ch, g });
    }
  }

  // Add or drop a lane. Everything that changes is touched by hand: the chip's
  // own class, the instrument row, the traces. Nothing else is rebuilt.
  function repick(key) {
    const on = picked.includes(key);
    picked = on ? picked.filter((x) => x !== key)
                : [...picked, key].slice(-MAX_LANES);
    saveMap(PICK_KEY, { keys: picked });
    for (const [k, btn] of chipBtns) {
      const lit = picked.includes(k);
      btn.classList.toggle("on", lit);
      btn.title = lit ? "Hide from the chart" : "Show on the chart";
    }
    if (stageEl) {
      stageEl.setAttribute("aria-label",
        `${picked.length} channel(s) over the recording`);
    }
    buildGauges();
    drawBase();
    paintNow();
  }

  // ---- structure, built once per shape change -----------------------------
  function draw() {
    stop();
    spansHost = null;
    chips = new Map();
    chipBtns = new Map();
    gaugeRow = null;
    gauges = [];
    geo = null;
    if (ro) { ro.disconnect(); ro = null; }
    clear(wrap);
    wrap.appendChild(probe);

    const ex = explain(h, "replay");
    if (ex) wrap.appendChild(ex);

    spansHost = h("div.rp-spans-host");
    renderSpans();
    wrap.appendChild(h("section.card",
      h("div.eyebrow", "Recorded"),
      h("div.title", "Replay a drive"),
      spansHost));

    if (!chosen) return;
    if (loading) {
      wrap.appendChild(h("section.card", h("p.lede", "Loading the span…")));
      return;
    }
    if (!rows.length) {
      wrap.appendChild(h("section.card",
        h("div.eyebrow", "Nothing to play"),
        h("p.lede", "The recorder wrote no samples between "
          + `${clockOf(chosen.t0)} and ${clockOf(chosen.t1)}. That usually means `
          + "the adapter was not answering — the daemon keeps the span either "
          + "way, so an empty one is itself a fact about the drive.")));
      return;
    }

    // ---- the console -----------------------------------------------------
    playBtn = h("button.btn.primary.rp-play", {
      type: "button", onclick: () => (playing ? stop() : play()),
    }, "Play");

    atEl = h("span.rp-at", "—");
    elapsedEl = h("span.rp-elapsed", "");

    const total = Math.max(1, Math.round(rows[rows.length - 1].t - rows[0].t));
    slider = h("input.rp-slider", {
      type: "range", min: "0", max: String(total), value: "0", step: "1",
      "aria-label": "Position in the recording",
      // Time, not row index. With gaps in the record the two disagree, and the
      // slider has to agree with the axis it sits under or scrubbing feels
      // broken in a way nobody can describe.
      oninput: (e) => { stop(); seek(rows[0].t + Number(e.target.value)); },
    });

    ribbon = h("div.rp-events");
    base = h("canvas.rp-base", { "aria-hidden": "true" });
    over = h("canvas.rp-over", { "aria-hidden": "true" });
    stageEl = h("div.rp-stage", {
      role: "img",
      "aria-label": `${picked.length} channel(s) over ${mins(total)} of driving`,
      onpointerdown: (e) => {
        stop();
        // Capture so a drag that leaves the canvas keeps scrubbing rather than
        // stopping at the edge. try/catch because a synthetic event -- a test,
        // an assistive device -- carries a pointerId the browser never issued.
        try { stageEl.setPointerCapture(e.pointerId); } catch { /* not a real pointer */ }
        scrubTo(e);
      },
      onpointermove: (e) => { if (e.buttons & 1) scrubTo(e); },
    }, base, over);

    function scrubTo(e) {
      if (!geo) return;
      const r = stageEl.getBoundingClientRect();
      const frac = clamp((e.clientX - r.left - PAD.l) / geo.plotW, 0, 1);
      seek(geo.t0 + geo.span * frac);
    }

    wrap.appendChild(h("section.card",
      h("div.rp-head",
        h("div",
          h("div.eyebrow", chosen.label),
          h("div.title", "The whole drive on one axis")),
        h("div.rp-time", atEl, elapsedEl)),
      h("div.rp-transport",
        playBtn,
        h("button.btn.sm", { type: "button", title: "Back one sample",
                             onclick: () => { stop(); step(-1); } }, "‹"),
        h("button.btn.sm", { type: "button", title: "Forward one sample",
                             onclick: () => { stop(); step(1); } }, "›"),
        h("div.seg", ...[1, 4, 16, 64].map((r) =>
          h("button.btn.sm" + (rate === r ? ".on" : ""), {
            type: "button",
            onclick: (e) => {
              rate = r;
              for (const b of e.target.parentElement.children) b.classList.remove("on");
              e.target.classList.add("on");
              if (playing) play();
            },
          }, r + "×"))),
        h("div.rp-spacer"),
        h("span.rp-hint", "drag the graph to scrub · space plays · ← → step"),
        h("button.btn", {
          type: "button",
          onclick: () => {
            // A real download, not a blob built in the page: the server
            // already streams this and knows how to name the file.
            window.location.href = `/export.csv?from=${chosen.t0}&to=${chosen.t1}`;
          },
        }, "Export CSV")),
      ribbon,
      stageEl,
      slider));

    renderEvents();

    // ---- instruments -----------------------------------------------------
    gaugeRow = h("div.rp-gauges");
    buildGauges();

    const missing = CHANNELS.filter((c) => !present.includes(c.key));
    wrap.appendChild(h("section.card",
      h("div.eyebrow", "At this moment"),
      h("div.title", "What the car was saying"),
      gaugeRow,
      h("div.rp-grid", ...present.map((k) => {
        const c = byKey(k);
        const value = h("div.rp-v", "—");
        chips.set(k, value);
        const btn = h("button.rp-cell" + (picked.includes(k) ? ".on" : ""), {
          type: "button", data: { ch: k },
          onclick: () => repick(k),
        }, h("div.rp-k", c.label), value, h("div.rp-u", c.unit()));
        chipBtns.set(k, btn);
        return btn;
      })),
      missing.length
        ? h("p.rp-missing", "Not in this recording: "
            + missing.map((c) => c.label).join(", ")
            + ". The recorder writes what the car answered, so a channel absent "
            + "here was never asked for or never came back.")
        : null));

    // The width is the only thing a repaint depends on, and a canvas resize
    // changes the height — watching height too would be a loop that repaints
    // itself forever.
    ro = new ResizeObserver(() => {
      if (!stageEl || Math.abs(stageEl.clientWidth - lastW) < 2) return;
      clearTimeout(resizeAt);
      resizeAt = setTimeout(drawBase, 120);
    });
    ro.observe(stageEl);

    drawBase();
    paintNow();
  }

  function renderSpans() {
    if (!spansHost) return;
    clear(spansHost);
    if (!spans.length) {
      spansHost.appendChild(h("p.lede",
        "No drives recorded yet. The daemon logs samples whenever it is "
        + "connected, and a drive is segmented automatically once you move and "
        + "then stop."));
      return;
    }
    spansHost.appendChild(h("div.rp-spans", ...spans.slice(0, 12).map((s) =>
      // Identity is BOTH ends. A trip and the recording somebody saved of it
      // start at the same second, and comparing t0 alone lit two cards for one
      // selection.
      h("button.rp-span" + (chosen && chosen.t0 === s.t0 && chosen.t1 === s.t1 ? ".on" : ""), {
        type: "button", onclick: () => open(s),
      },
        h("div.rp-span-l", s.label),
        h("div.rp-span-d",
          h("span.rp-span-kind", { data: { kind: s.kind } }, s.kind),
          s.detail)))));
  }

  function renderEvents() {
    if (!ribbon || !rows.length) return;
    clear(ribbon);
    // The ribbon's domain is the ROWS, not the span the picker offered: the
    // canvas draws first sample to last sample, and a marker placed against the
    // requested window would sit a few pixels off the spike it belongs to.
    const t0 = rows[0].t;
    const span = Math.max(1, rows[rows.length - 1].t - t0);
    if (!events.length) {
      ribbon.appendChild(h("span.rp-ev-none",
        "No scans, alerts or code sightings recorded in this span"));
      return;
    }
    for (const ev of events) {
      const frac = clamp((ev.t - t0) / span, 0, 1);
      const more = ev.n > 1 ? `  (+${ev.n - 1} more here)` : "";
      ribbon.appendChild(h("button.rp-ev", {
        type: "button", data: { tone: ev.tone, many: ev.n > 1 ? "1" : "0" },
        // The same mapping the canvas uses, expressed in CSS so the marker
        // tracks the trace through a resize without JavaScript re-measuring.
        style: { left: `calc(${PAD.l}px + (100% - ${PAD.l + PAD.r}px) * ${frac.toFixed(4)})` },
        title: `${stampAt(ev.t, 0)} — ${ev.label}${more}`,
        onclick: () => { stop(); seek(ev.t); },
      }, h("span.rp-ev-dot"), h("span.rp-ev-l", ev.label + (ev.n > 1 ? ` ×${ev.n}` : ""))));
    }
  }

  // ---- following a theme change -------------------------------------------
  //
  // The canvas holds copies of the tokens, and a copy is a thing that can go
  // stale: switch to Matrix or Night red and the DOM turns green or red while
  // the traces stay whatever they were painted. Nothing pushes a theme change
  // at a view — looks set data-look on <html>, and lib/theme.py's sheet is
  // swapped into <head> by main.js when the server's stamp moves — so the
  // cheapest correct answer is to watch both places and repaint. Attribute and
  // childList only: this fires a handful of times in a session, never in a loop
  // with the drawing it triggers.
  let themeAt = 0;
  const repaintTheme = () => {
    clearTimeout(themeAt);
    themeAt = setTimeout(() => { if (rows.length) drawBase(); }, 60);
  };
  const themeWatch = new MutationObserver(repaintTheme);
  themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-look"] });
  themeWatch.observe(document.head, { childList: true, subtree: true, characterData: true });

  // ---- keyboard ----------------------------------------------------------
  function onKey(e) {
    if (!rows.length) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    // Space is how a keyboard presses the button it is standing on. Stealing it
    // there would mean tabbing to Export CSV, pressing space, and getting
    // playback instead of a download.
    const onControl = t && (t.tagName === "BUTTON" || t.tagName === "A");
    if (e.key === " ") {
      if (onControl) return;
      e.preventDefault();
      playing ? stop() : play();
    }
    else if (e.key === "ArrowLeft") { e.preventDefault(); stop(); step(e.shiftKey ? -10 : -1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); stop(); step(e.shiftKey ? 10 : 1); }
    else if (e.key === "Home") { e.preventDefault(); stop(); step(-rows.length); }
    else if (e.key === "End") { e.preventDefault(); stop(); step(rows.length); }
  }
  document.addEventListener("keydown", onKey);

  // The snapshot usually lands after the first paint, and it is where the fault
  // sightings come from. Re-derive once when it does, and only redraw if it
  // actually changed the ribbon.
  const offCar = store.on("car", () => {
    if (!chosen || !rows.length) return;
    const before = events.length;
    buildEvents();
    if (events.length !== before) { renderEvents(); drawBase(); }
  });

  draw();
  loadSpans();

  return () => {
    stop();
    if (raf) cancelAnimationFrame(raf);
    clearTimeout(resizeAt);
    clearTimeout(themeAt);
    if (ro) ro.disconnect();
    themeWatch.disconnect();
    document.removeEventListener("keydown", onKey);
    offCar();
  };
}
