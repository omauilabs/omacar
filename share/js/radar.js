// The radar detector, as the screens see it: one feed, and the pure functions
// that turn what the server says (/api/v1, lib/v1.py) into what is drawn.
//
// THREE SCREENS SHARE ONE FEED: Home's card, the banner across the stage, and
// the Radar view. Each subscribes; the feed polls while anybody listens and
// stops when nobody does, so a Home with no radar card and a banner that has
// nothing to say costs one request every half minute.
//
// WHERE THE READING CAME FROM IS ALWAYS ON THE SCREEN. "V1 · Bluetooth" from
// the detector, "Simulated" from `omacar v1 sim`, "Not connected" when the
// link is down. A stale file never shows an alert: the server drops them.
//
// NO SOUND, anywhere in this file or the three screens: the V1 beeps for
// itself, and a child sleeps near the tablet.
//
// radarView(), bannerView(), bannerPhase(), alertRows() and logRows() are pure
// (test/js/radar.test.js); the screens only draw what they return.

import { withToken } from "./core.js";

// Laser gets the bad tone, Ka the warn tone; the quieter bands the info tone.
// The tone also decides the colour, never alone: the band's name is always
// written next to it, because the red-only night look has no hues.
export const TONE = { Laser: "bad", Ka: "warn", K: "info", X: "info", Ku: "info", Photo: "info" };
export const BAND_ORDER = ["Laser", "Ka", "K", "X"];     // the V1's own four lamps
export const DIR_WORD = { front: "Ahead", side: "Beside", rear: "Behind" };
export const HOLD_S = 3;                                   // the banner outlives the last alert by this

// 34700 -> "34.7" (GHz), 24150 -> "24.15", 10525 -> "10.525", 35000 -> "35.0"
export function fmtFreq(mhz) {
  if (!mhz) return "";
  let s = (mhz / 1000).toFixed(3).replace(/0+$/, "");
  if (s.endsWith(".")) s += "0";
  return s;
}

export function headOf(a) {
  if (!a) return "";
  if (a.band === "Photo") return a.photo ? `Photo ${a.photo}` : "Photo";
  const f = fmtFreq(a.freq_mhz);
  return f ? `${a.band} ${f}` : a.band;
}

export function agoText(s) {
  if (s === null || s === undefined || !Number.isFinite(s)) return "";
  if (s < 90) return `${Math.max(1, Math.round(s))} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

const clampBars = (n) => Math.max(0, Math.min(8, Math.round(Number(n) || 0)));

export function alertRows(st) {
  return ((st && st.alerts) || []).map((a, i) => ({
    head: headOf(a), band: a.band, freq: fmtFreq(a.freq_mhz), dir: a.dir || null,
    dirWord: DIR_WORD[a.dir] || "", strength: clampBars(a.strength), exact: !!a.exact,
    tone: TONE[a.band] || "info", priority: i === 0 || !!a.priority,
  }));
}

export function logRows(st, clock = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })) {
  return ((st && st.log) || []).map((e) => ({
    time: clock(e.at), head: headOf(e), band: e.band, dir: e.dir || null,
    dirWord: DIR_WORD[e.dir] || "", peak: clampBars(e.peak), secs: e.secs || 0,
    tone: TONE[e.band] || "info",
  }));
}

// What Home's card and the Radar view say. `st` is /api/v1; `now` seconds;
// `pending` a mute just asked for and not yet confirmed by the detector.
export function radarView(st, { now = Date.now() / 1000, pending = null } = {}) {
  if (!st || !st.present) {
    return { state: "absent", lamp: "Not set up", lampTone: "", live: false, sim: false,
             rows: [], pri: null, others: 0, today: 0, todayText: "No detector yet",
             lastText: "", muted: false, canMute: false,
             line: "Pair a detector: omacar v1 scan", facts: [] };
  }
  const sim = st.source === "sim";
  const live = !!st.connected;
  const lamp = sim ? "Simulated" : live ? "V1 · Bluetooth" : "Not connected";
  const lampTone = sim ? "warn" : live ? "ok" : "";
  const rows = live ? alertRows(st) : [];
  const pri = rows[0] || null;
  const muted = pending !== null ? pending : !!st.muted;
  const today = (st.today && st.today.count) || 0;
  // The server and this page share a clock (the same tablet), so the age of
  // the last alert is just now minus when it was written.
  const sinceLast = st.last_alert_at ? Math.max(0, now - st.last_alert_at) : null;
  const facts = [];
  facts.push(["Link", lamp]);
  if (st.name) facts.push(["Device", st.name]);
  if (st.version) facts.push(["Firmware", st.version + (st.gen2 === true ? " · Gen2" : st.gen2 === false ? " · original" : "")]);
  if (st.mode) facts.push(["Mode", st.mode]);
  facts.push(["Audio", muted ? "Muted" : "On"]);
  if (st.framing_errors) facts.push(["Bad frames", String(st.framing_errors)]);
  if (!live && st.age !== undefined) facts.push(["Last heard", agoText(st.age)]);
  return {
    state: !live ? "offline" : rows.length ? "alert" : "idle",
    lamp, lampTone, live, sim, rows, pri, others: Math.max(0, rows.length - 1),
    today, todayText: today === 1 ? "1 alert today" : `${today} alerts today`,
    lastText: st.last_alert_at ? `Last alert ${agoText(sinceLast)}` : "No alerts yet",
    muted, canMute: live && st.muted !== null && st.muted !== undefined,
    line: !live ? (st.note || "Waiting for the detector") : rows.length ? "" : "All quiet",
    mode: st.mode || "", facts,
  };
}

// The banner: shown while there is an alert, held for HOLD_S after the last.
// `memo` is what it last showed, kept by the caller so the fade has something
// to draw once the alert is gone.
export function bannerPhase(active, lastActiveAt, now, hold = HOLD_S) {
  if (active) return "on";
  if (lastActiveAt !== null && lastActiveAt !== undefined && now - lastActiveAt < hold) return "fading";
  return "off";
}

export function bannerView(st, opts) {
  const v = radarView(st, opts);
  return { active: v.state === "alert", pri: v.pri, others: v.others, muted: v.muted, canMute: v.canMute,
           sim: v.sim, lamp: v.lamp };
}

// Is the banner redundant on this screen? On the Radar view, and on Home while
// Home has a radar card, the alert is already the whole of what is drawn.
export function bannerHides(hash, homeHasCard) {
  const id = String(hash || "").replace(/^#/, "").split("/")[0] || "home";
  return id === "radar" || ((id === "home" || id === "hub") && !!homeHasCard);
}

// ---- the feed -----------------------------------------------------------------------------

const subs = new Set();
let last = null, timer = null, inflight = false, gone = false;

function nextDelay(st) {
  // Quick while there is a detector to listen to; slow while there is none.
  if (!st || !st.present) return 30000;
  if (st.connected && st.alerts && st.alerts.length) return 700;
  return st.connected ? 1500 : 5000;
}

async function poll() {
  timer = null;
  if (inflight || !subs.size) return;
  inflight = true;
  try {
    const r = await fetch(withToken("/api/v1"), { cache: "no-store" });
    if (r.ok) last = await r.json();
  } catch { /* keep the last answer; the server drops stale alerts itself */ }
  inflight = false;
  for (const f of [...subs]) { try { f(last); } catch (e) { console.warn("radar feed:", e); } }
  if (subs.size && !gone) timer = setTimeout(poll, nextDelay(last));
}

export const radarFeed = {
  get last() { return last; },
  subscribe(fn) {
    subs.add(fn);
    if (last) { try { fn(last); } catch { /* the caller's problem */ } }
    if (!timer && !inflight) timer = setTimeout(poll, 0);
    return () => {
      subs.delete(fn);
      if (!subs.size && timer) { clearTimeout(timer); timer = null; }
    };
  },
  refresh() { if (timer) clearTimeout(timer); timer = setTimeout(poll, 0); },
  // Mute, and only mute: the one thing the detector is ever asked.
  mute(on) {
    return fetch(withToken("/api/v1"), {
      method: "POST", cache: "no-store", body: JSON.stringify({ name: "mute", value: !!on }),
    }).then((r) => r.json());
  },
};
