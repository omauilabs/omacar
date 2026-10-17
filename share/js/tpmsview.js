// What the tyre screens say (lib/tpms.py is where the numbers come from).
// Everything here is a pure function of /api/tpms, so the words can be tested
// without a radio, a clock or a DOM.
//
// WHERE A NUMBER CAME FROM IS ALWAYS SAID. "Live" from the RTL-SDR, "Simulated"
// from `omacar tpms sim`, "Last seen N min ago" once a wheel has gone quiet.
// A wheel nobody has heard is a dash and a sentence, never a zero. Only the
// sensors the owner assigned are in /api/tpms at all; the learn list
// (/api/tpms/heard) is the one place a stranger's sensor is ever named.

import { fmtTemp, tempUnit } from "./envview.js";

export const PSI = 6.894757;
export const WHEELS = ["FL", "FR", "RL", "RR"];
export const NAMES = { FL: "Front left", FR: "Front right", RL: "Rear left", RR: "Rear right" };
const RANK = { ok: 0, warn: 1, bad: 2 };

// 241 kPa -> {v: "35.0", u: "psi"} or {v: "2.41", u: "bar"}.
export function fmtP(kpa, imperial = true) {
  if (kpa === null || kpa === undefined || !Number.isFinite(kpa)) return { v: "—", u: imperial ? "psi" : "bar" };
  return imperial ? { v: (kpa / PSI).toFixed(1), u: "psi" } : { v: (kpa / 100).toFixed(2), u: "bar" };
}

export function agoText(s) {
  if (s === null || s === undefined || !Number.isFinite(s)) return "";
  if (s < 90) return `${Math.max(1, Math.round(s))} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

// "5 % low" / "At placard" / "28 % high", against the cold placard pressure.
function against(w) {
  if (w.p20_kpa === null || w.p20_kpa === undefined || !w.placard_kpa) return "";
  const d = (w.p20_kpa / w.placard_kpa - 1) * 100;
  if (Math.abs(d) < 3) return "At placard";
  return d < 0 ? `${Math.round(-d)} % low` : `${Math.round(d)} % high`;
}

export function trendLine(w, imperial = true) {
  if (w.leak) {
    const per = fmtP(Math.abs(w.slope_kpa_day || 0), imperial);
    return { text: `Losing ${per.v} ${per.u} a day`, tone: "warn" };
  }
  if (w.slope_kpa_day === null || w.slope_kpa_day === undefined) {
    return { text: w.trend_n ? "Trend needs a few more mornings" : "No trend yet", tone: "" };
  }
  return { text: "Holding its air", tone: "ok" };
}

// One wheel, drawn. `st` is the whole /api/tpms (for its source and service).
export function wheelView(w, st, { imperial = true } = {}) {
  const sim = st && st.source === "sim";
  const heard = w.kpa !== null && w.kpa !== undefined;
  const p = fmtP(w.kpa, imperial);
  const t = trendLine(w, imperial);
  const live = !!(st && st.running && w.fresh);
  let src, srcTone;
  if (!w.assigned) { src = "No sensor assigned"; srcTone = ""; }
  else if (!heard) { src = "Not heard yet"; srcTone = ""; }
  else if (sim) { src = "Simulated"; srcTone = "warn"; }
  else if (live) { src = "Live · radio"; srcTone = "ok"; }
  else { src = `Last seen ${agoText(w.age)}`; srcTone = ""; }
  const rapid = !!(w.rapid && st && st.running);
  const level = w.level || null;
  const tone = rapid ? "bad" : level || "";
  const points = (w.trend || []).map((q) => [q[0], q[1]]);
  return {
    wheel: w.wheel, name: w.name || NAMES[w.wheel], assigned: !!w.assigned, heard,
    state: !w.assigned ? "unassigned" : !heard ? "quiet" : sim ? "sim" : live ? "live" : "stale",
    pressure: p.v, unit: p.u,
    temp: heard && w.temp_c !== null && w.temp_c !== undefined ? fmtTemp(w.temp_c, imperial) : "—",
    tempUnit: tempUnit(imperial),
    level, tone,
    word: rapid ? "Losing air fast" : !heard ? "" : (level === "bad" ? "Very low" : level === "warn"
      ? (w.p20_kpa > w.placard_kpa ? "High" : "Low") : "Good"),
    vs: against(w) === "At placard" ? "" : against(w),
    placard: fmtP(w.placard_kpa, imperial),
    source: src, sourceTone: srcTone,
    battery: w.battery === "low" ? "Sensor battery low" : w.battery === "ok" ? "Sensor battery ok" : "",
    batteryLow: w.battery === "low",
    normalised: w.normalised === false ? "No temperature: not corrected" : "",
    rssi: w.rssi,
    trend: t, trendPoints: points, trendLeak: !!w.leak,
    dim: heard && !live && !sim,
  };
}

// The whole screen.
export function tpmsView(st, { imperial = true } = {}) {
  if (!st || !st.present) {
    return { state: "absent", wheels: WHEELS.map((k) => wheelView({ wheel: k, name: NAMES[k] }, st, { imperial })),
             source: "", sourceTone: "", banner: null, note: "No tyre sensors yet: plug in the RTL-SDR and run omacar tpms learn",
             anyAssigned: false, anyHeard: false, sim: false };
  }
  const wheels = (st.wheels || []).map((w) => wheelView(w, st, { imperial }));
  const sim = st.source === "sim";
  const anyHeard = wheels.some((w) => w.heard);
  const anyAssigned = wheels.some((w) => w.assigned);
  let source, sourceTone;
  if (sim) { source = "Simulated"; sourceTone = "warn"; }
  else if (st.running) { source = "RTL-SDR · rtl_433"; sourceTone = "ok"; }
  else { source = st.age !== undefined ? `Last seen ${agoText(st.age)}` : "Not running"; sourceTone = ""; }
  let note = "";
  if (!anyAssigned) note = "Assign your sensors to the wheels (Learn sensors, while parked)";
  else if (st.radio && st.radio !== "ok" && st.radio !== "sim" && st.note) note = st.note;
  else if (anyAssigned && !anyHeard) note = "Waiting to hear them. Parked sensors may only speak every few minutes.";
  const alert = (st.alerts || []).slice().sort((a, b) => (a.kind === "rapid" ? 0 : 1) - (b.kind === "rapid" ? 0 : 1))[0] || null;
  return { state: "ok", wheels, source, sourceTone, banner: alert, note, anyAssigned, anyHeard, sim, running: !!st.running };
}

// Home's callout: one line for the four tyres, or null to leave the car's own
// deflation-warning state in charge (nothing assigned, nothing heard).
export function homeTyre(st, { imperial = true } = {}) {
  if (!st || !st.present) return null;
  const heard = (st.wheels || []).filter((w) => w.kpa !== null && w.kpa !== undefined);
  if (!heard.length) return null;
  const sim = st.source === "sim";
  const live = !!st.running && heard.some((w) => w.fresh);
  const rapid = st.running && heard.find((w) => w.rapid);
  const rank = (w) => RANK[w.level] || 0;
  const worst = heard.slice().sort((a, b) => rank(b) - rank(a) || (a.p20_kpa - b.p20_kpa))[0];
  const num = (w) => fmtP(w.kpa, imperial).v;
  const unit = fmtP(1, imperial).u;
  let text, tone;
  if (rapid) { text = `${rapid.wheel} losing air fast`; tone = "bad"; }
  else if (rank(worst) > 0) {
    const low = worst.p20_kpa < worst.placard_kpa;
    text = `${worst.wheel} ${num(worst)} ${unit} · ${low ? (worst.level === "bad" ? "very low" : "low") : "high"}`;
    tone = worst.level;
  } else {
    const vs = heard.map((w) => Number(fmtP(w.kpa, imperial).v)).sort((a, b) => a - b);
    const lo = vs[0].toFixed(imperial ? 0 : 1), hi = vs[vs.length - 1].toFixed(imperial ? 0 : 1);
    text = `${lo === hi ? lo : lo + "–" + hi} ${unit}`;
    tone = "ok";
  }
  const leaking = (st.alerts || []).find((a) => a.kind === "leak");
  if (leaking && tone === "ok") { text = `${leaking.wheel} slow leak`; tone = "warn"; }
  if (heard.length < 4 && !rapid) text += ` · ${heard.length} of 4`;
  if (sim) text += " · Simulated";
  else if (!live) { text += ` · ${agoText(Math.min(...heard.map((w) => w.age ?? Infinity)))}`; if (!rapid) tone = tone === "ok" ? "" : tone; }
  return { text, tone, sim };
}

// The car's own deflation warning and the sensors both speak for the tyres;
// whichever is worse is what Home says, and the source is on the words.
export function mergeTyre(radio, car) {
  if (!radio) return car;
  const r = RANK[radio.tone] ?? -1, c = RANK[car.tone] ?? -1;
  return c > r && c >= 1 ? car : radio;
}

// ---- the learn list -----------------------------------------------------------------------

// Rows for the learn panel: loudest first, each with the change in pressure
// since the panel opened (`baseline` is {id: kPa} taken then). Letting a
// little air out of one tyre makes that sensor's change go negative, and that
// is how the owner knows which id is which wheel.
export function learnRows(heard, baseline = {}, { imperial = true } = {}) {
  return (heard || []).map((r) => {
    const d = baseline[r.id] !== undefined && r.kpa !== null ? r.kpa - baseline[r.id] : null;
    const dp = d === null ? null : imperial ? d / PSI : d / 100;
    const p = fmtP(r.kpa, imperial);
    return {
      id: r.id, model: r.model || "?", wheel: r.wheel || null,
      rssi: r.rssi === null || r.rssi === undefined ? "—" : r.rssi.toFixed(0),
      rssiNum: r.rssi, pressure: p.v, unit: p.u,
      delta: dp === null ? "" : Math.abs(dp) < 0.05 ? "±0" : `${dp > 0 ? "+" : "−"}${Math.abs(dp).toFixed(imperial ? 1 : 2)}`,
      dropped: dp !== null && dp <= (imperial ? -1.0 : -0.07),
      age: agoText(r.age),
    };
  }).sort((a, b) => Number(b.dropped) - Number(a.dropped) || (b.rssiNum ?? -999) - (a.rssiNum ?? -999));
}

// ---- the banner and its one tone ---------------------------------------------------------------

// A rapid loss is a banner on every screen. A slow leak is not: it is on the
// Tires screen, Home's callout and the owner's phone, but a banner across the
// road for a tyre that lost a psi this week would teach the driver to ignore it.
export function bannerView(st) {
  if (!st || !st.present || !st.running) return null;
  const a = (st.alerts || []).find((x) => x.kind === "rapid");
  if (!a) return null;
  const w = (st.wheels || []).find((x) => x.wheel === a.wheel);
  return { wheel: a.wheel, key: `${a.wheel}:${Math.round(a.at)}`,
           title: `${NAMES[a.wheel]} tire is losing air fast`, at: a.at, sim: st.source === "sim",
           pct: Math.round((a.frac || 0) * 100), kpa: w ? w.kpa : null };
}

// One tone per event, and only while the car is moving: a child sleeps near
// the tablet, and a tyre going down in a parked car is not a thing a tone
// helps with. `seen` is the set of event keys already sounded.
export function shouldSound(b, { moving = false, seen = new Set() } = {}) {
  return !!b && moving && !seen.has(b.key);
}
