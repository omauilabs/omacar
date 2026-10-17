// What the Environment card and screen say (lib/envnode.py is where the
// numbers come from). Everything here is a pure function of /api/env, so the
// words can be tested without a sensor, a clock or a DOM.
//
// WHERE A NUMBER CAME FROM IS ALWAYS SAID. "Sensor" from a node on USB,
// "From the car" from the OBD ambient thermometer, "Simulated" from
// `omacar env sim`, or "Last seen N min ago". A side with nothing says what to
// plug in, never a zero.

export const CO2_BANDS = [
  { key: "good", from: 0, label: "Fresh", tone: "ok" },
  { key: "ok", from: 800, label: "Fine", tone: "info" },
  { key: "stuffy", from: 1200, label: "Stuffy", tone: "warn" },
  { key: "high", from: 2000, label: "High", tone: "bad" },
];
export const AQI_TONE = { good: "ok", moderate: "info", usg: "warn", unhealthy: "bad", very: "bad", hazardous: "bad" };
export const CO2_TONE = { good: "ok", ok: "info", stuffy: "warn", high: "bad" };

export const fmtTemp = (c, imperial) => {
  if (c === null || c === undefined) return "—";
  return imperial ? String(Math.round(c * 9 / 5 + 32)) : c.toFixed(1).replace(/^-0\.0$/, "0.0");
};
export const tempUnit = (imperial) => (imperial ? "°F" : "°C");
// A temperature DIFFERENCE does not take the 32 offset.
export const fmtDelta = (c, imperial) => (imperial ? (c * 9 / 5).toFixed(1) : c.toFixed(1));

export function fmtPressure(hpa, imperial) {
  if (hpa === null || hpa === undefined) return { v: "—", u: imperial ? "inHg" : "hPa" };
  return imperial ? { v: (hpa * 0.02953).toFixed(2), u: "inHg" } : { v: String(Math.round(hpa)), u: "hPa" };
}
export const fmtInt = (n) => (n === null || n === undefined ? "—" : Math.round(n).toLocaleString("en-US"));

export function ago(s) {
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

// The lamp: { text, tone }. tone is "" | ok | info | warn.
export function lampOf(side) {
  if (!side || side.state === "absent") return { text: "No sensor", tone: "" };
  if (side.state === "stale") return { text: `Last seen ${ago(side.age || 0)}`, tone: "" };
  if (side.source === "sim") return { text: "Simulated", tone: "warn" };
  if (side.source === "car") return { text: "From the car", tone: "info" };
  return { text: "Sensor", tone: "ok" };
}

function cabinView(c, imperial) {
  const v = (c && c.values) || {};
  const lamp = lampOf(c);
  const trail = (c && c.trail && c.trail.co2) || [];
  if (!c || c.state === "absent") {
    return { state: "absent", lamp, temp: "—", unit: tempUnit(imperial), rh: "—", tempNote: "",
             air: { k: "CO₂", v: "—", u: "", label: "", tone: "" }, trail: [],
             line: "Plug in the cabin sensor" };
  }
  const band = c.co2_band;
  return {
    state: c.state, lamp,
    temp: fmtTemp(v.t_c, imperial), unit: tempUnit(imperial), tempNote: "",
    rh: v.rh === undefined ? "—" : `${Math.round(v.rh)}`,
    air: v.co2 === undefined
      ? { k: "CO₂", v: "—", u: "", label: "No CO₂ reading yet", tone: "" }
      : { k: "CO₂", v: fmtInt(v.co2), u: "ppm", label: band ? band.label : "", tone: band ? CO2_TONE[band.key] : "" },
    trail, line: "",
  };
}

function outsideView(o, imperial) {
  const v = (o && o.values) || {};
  const lamp = lampOf(o);
  const trail = (o && o.trail && o.trail.pm25) || [];
  if (!o || o.state === "absent") {
    return { state: "absent", lamp, temp: "—", unit: tempUnit(imperial), rh: "—", tempNote: "",
             air: { k: "AQI", v: "—", u: "", label: "", tone: "" }, trail: [],
             line: "Plug in the outdoor sensor" };
  }
  const aqi = o.aqi;
  const carT = o.t_source === "car";
  return {
    state: o.state, lamp,
    temp: fmtTemp(v.t_c, imperial), unit: tempUnit(imperial),
    // A node that has no thermometer of its own borrows the car's; say so.
    tempNote: carT && o.source !== "car" ? "Temperature from the car" : "",
    rh: v.rh === undefined ? "—" : `${Math.round(v.rh)}`,
    air: aqi
      ? { k: "AQI", v: String(aqi.index), u: "", label: aqi.label, tone: AQI_TONE[aqi.key] || "" }
      : { k: "AQI", v: "—", u: "", label: o.source === "car" ? "No air sensor outside" : "No particle reading", tone: "" },
    trail, line: "",
  };
}

// st is /api/env. Never throws on a partial or empty answer.
export function envView(st, { imperial = true } = {}) {
  if (!st || !st.present) {
    return { present: false, cabin: cabinView(null, imperial), outside: outsideView(null, imperial),
             more: "", hint: "No sensors yet: omacar env scan" };
  }
  const o = st.outside || {};
  const ov = o.values || {};
  const bits = [];
  if (o.dew_c !== null && o.dew_c !== undefined) bits.push(`Dew point ${fmtTemp(o.dew_c, imperial)}°`);
  if (ov.hpa !== undefined) {
    const p = fmtPressure(ov.hpa, imperial);
    const tr = o.trend;
    bits.push(`${p.v} ${p.u}` + (tr && tr.available ? ` ${tr.word}` : ""));
  }
  return { present: true, source: st.source, cabin: cabinView(st.cabin, imperial),
           outside: outsideView(o, imperial), more: bits.join(" · "), hint: "" };
}

// ---- the calm prompt (share/js/envprompt.js) ------------------------------------

export const PROMPT_GAP_MS = 20 * 60 * 1000;

export function promptText(ppm) {
  return `Cabin air is stale (CO₂ ${fmtInt(ppm)} ppm). Switch to fresh air or crack a window.`;
}

// Whether to say it now. `drowsy` is the engine's state ({level, banner,
// testing}); anything but a quiet level 0 holds the prompt back, and it stays
// due, so it comes up the moment the alert has cleared.
export function shouldPrompt(st, drowsy, now, lastShown) {
  if (!st || !st.present || !st.prompt || !st.prompt.due || !st.moving) return false;
  if (drowsy && (drowsy.level > 0 || drowsy.banner || drowsy.testing)) return false;
  if (lastShown && now - lastShown < PROMPT_GAP_MS) return false;
  return true;
}

// ---- charts -----------------------------------------------------------------------

// A line over a fixed window [t0, t1], in a w x h box, as segments: a gap of
// more than `gap` seconds (the service was off, a node unplugged) breaks the
// line rather than drawing a slope across nothing. Returns { line, area }.
export function linePath(points, t0, t1, w, h, lo, hi, gap = 330) {
  const span = t1 - t0 || 1;
  const flat = hi === lo;
  const pts = (points || []).filter((p) => p[0] >= t0 && p[0] <= t1 && p[1] !== null);
  let line = "", area = "", seg = [];
  const flush = () => {
    if (seg.length > 1) {
      line += seg.map(([x, y], i) => (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1)).join("");
      area += `M${seg[0][0].toFixed(1)} ${h}` + seg.map(([x, y]) => `L${x.toFixed(1)} ${y.toFixed(1)}`).join("")
            + `L${seg[seg.length - 1][0].toFixed(1)} ${h}Z`;
    } else if (seg.length === 1) {
      // One lone minute: a short tick so it is not lost.
      const [x, y] = seg[0];
      line += `M${(x - 1).toFixed(1)} ${y.toFixed(1)}L${(x + 1).toFixed(1)} ${y.toFixed(1)}`;
    }
    seg = [];
  };
  let prev = null;
  for (const [t, v] of pts) {
    if (prev !== null && t - prev > gap) flush();
    const c = Math.min(hi, Math.max(lo, v));
    seg.push([((t - t0) / span) * w, flat ? h / 2 : h - ((c - lo) / (hi - lo)) * h]);
    prev = t;
  }
  flush();
  return { line, area };
}

// A y-range that always shows the data and never exaggerates a flat line:
// at least `minSpan` tall, padded 8 %, and kept to `floor`/`ceil` when given.
export function niceRange(vals, { minSpan = 1, floor = null, ceil = null, include = [] } = {}) {
  const all = vals.concat(include).filter((v) => v !== null && v !== undefined);
  if (!all.length) return { lo: 0, hi: 1 };
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  if (floor !== null) lo = Math.max(floor, lo);
  if (ceil !== null) hi = Math.min(ceil, hi);
  return { lo, hi };
}

// Share of the day (by minute rows) in each CO2 band, for the band history.
export function bandShares(points) {
  const n = points.length;
  const out = { good: 0, ok: 0, stuffy: 0, high: 0, n };
  for (const [, v] of points) {
    out[bandOf(v)] += 1;
  }
  return out;
}
export const bandOf = (v) => (v > 2000 ? "high" : v >= 1200 ? "stuffy" : v >= 800 ? "ok" : "good");

// ---- "what this means" -------------------------------------------------------------

export function explain(metric, st, { imperial = true } = {}) {
  const c = (st && st.cabin) || {}, o = (st && st.outside) || {};
  const cv = c.values || {}, ov = o.values || {};
  switch (metric) {
    case "cabin.co2": {
      const b = c.co2_band && c.co2_band.key;
      if (!b) return "No CO₂ reading yet. The sensor needs a few minutes to warm up.";
      return { good: "Under 800 ppm: air as fresh as outside, give or take.",
               ok: "800 to 1,200 ppm: a full cabin breathing for a while. Fine for now.",
               stuffy: "1,200 to 2,000 ppm: stuffy. Attention and alertness slip first. Open a window or switch to fresh air.",
               high: "Over 2,000 ppm: high. Fresh air now." }[b];
    }
    case "cabin.t_c":
      return cv.t_c === undefined ? "No cabin temperature."
        : cv.t_c > 30 ? "Hot cabin. Park in shade or vent before setting off."
        : cv.t_c < 10 ? "Cold cabin."
        : "Comfortable range, roughly 18 to 26 °C (64 to 79 °F).";
    case "cabin.rh":
      return cv.rh === undefined ? "No cabin humidity."
        : cv.rh > 60 ? "Humid: windows fog and the air feels close."
        : cv.rh < 25 ? "Dry: dry throat and eyes on long drives."
        : "Between 30 and 60 % is comfortable, and windows stay clear.";
    case "cabin.pm25":
      return cv.pm25 === undefined ? "No cabin particle reading."
        : cv.pm25 < 12 ? "Low dust and smoke inside. Recirculation is working."
        : "Raised fine particles inside: smoke, dust or a dirty cabin filter.";
    case "outside.t_c":
      return ov.t_c === undefined ? "No outdoor temperature."
        : o.t_source === "car" ? "From the car's own thermometer, which sits behind the bumper and reads warm after a long park."
        : o.car_t_c !== null && o.car_t_c !== undefined
          ? `The shielded sensor. The car says ${fmtTemp(o.car_t_c, imperial)}°, and it reads high in traffic and after a hot park.`
          : "The shielded outdoor sensor.";
    case "outside.rh":
      return ov.rh === undefined ? "No outdoor humidity."
        : "Humidity outside. The dew point is what you feel: over 16 °C (60 °F) is muggy, under 10 °C (50 °F) is dry.";
    case "outside.hpa": {
      const tr = o.trend;
      if (!tr) return "Air pressure outside.";
      if (!tr.available) return tr.reason === "trend unavailable while driving"
        ? "Trend unavailable while driving: pressure follows altitude, about 12 hPa per 100 m, not weather."
        : `No trend yet: ${tr.reason}.`;
      return tr.word === "falling" ? "Falling over three hours: weather is turning, often wetter or windier."
           : tr.word === "rising" ? "Rising over three hours: usually clearing and drier."
           : "Steady over three hours: no change coming from the pressure.";
    }
    case "outside.aqi": {
      const a = o.aqi;
      if (!a) return "No outdoor particle reading.";
      return { good: "Air quality is good. Nothing to do.",
               moderate: "Acceptable. Unusually sensitive people may notice it.",
               usg: "Sensitive groups (asthma, heart or lung disease, children) should cut long exposure. Recirculate if you are stuck in it.",
               unhealthy: "Unhealthy. Keep the cabin on recirculate.",
               very: "Very unhealthy. Recirculate and avoid lingering.",
               hazardous: "Hazardous. Recirculate, and avoid being outside." }[a.key];
    }
    case "cabin.band":
      return "Share of the day by CO₂ band: under 800 fresh, 800 to 1,200 fine, 1,200 to 2,000 stuffy, over 2,000 high.";
    default: return "";
  }
}
