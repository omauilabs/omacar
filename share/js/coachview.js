// Hybrid coach: what the screens say about a scored drive (lib/coach.py).
//
// coachView() is pure (test/js/coach.test.js); the card (coachcard.js) and the
// full screen (views/coach.js) both draw from it, so they cannot disagree.
//
// THREE RULES, KEPT HERE SO NO SCREEN CAN BREAK THEM.
//   1. Where a number came from is always said. "Measured" is read straight off
//      the car; "Estimated" rests on an assumption of ours; "Simulated" is the
//      simulator, and it overrides both because nothing in it was measured.
//   2. A missing input is never a zero. A sub-score with too little data has
//      score null and says why; the headline is "partial" when it was worked
//      out from fewer than three, and null (shown as "—") with fewer than two.
//   3. No hint while moving. The hint is held back whenever the app's moving
//      lock is on, and the Home card shows only "Drive in progress".

export const KEYS = ["regen", "smooth", "glide"];

export const EXPLAIN = {
  regen: {
    label: "Regen capture",
    what: "How much of the speed you shed went back into the pack, not into the brakes as heat.",
    how: { measured: "", estimated: "From the pack's charge reading (measured) and an assumed pack size, so an estimate." },
  },
  smooth: {
    label: "Smoothness",
    what: "How gently speed and pedal changed. Fewer jolts means less energy wasted.",
    how: { measured: "From speed and throttle, read off the car.", estimated: "" },
  },
  glide: {
    label: "Gliding",
    what: "How much of the time you moved with your foot off the pedal, rolling.",
    how: { measured: "From throttle and speed, read off the car.", estimated: "" },
  },
};

export const SOURCE_WORD = { measured: "Measured", estimated: "Estimated", sim: "Simulated" };

export function toneOf(score) {
  if (score === null || score === undefined) return "none";
  return score >= 75 ? "ok" : score >= 50 ? "warn" : "bad";
}

export function band(score) {
  if (score === null || score === undefined) return "No score";
  return score >= 85 ? "Excellent" : score >= 75 ? "Good" : score >= 60 ? "Fair" : score >= 40 ? "Heavy-footed" : "Rough";
}

function when(t0) {
  if (!t0) return "";
  const d = new Date(t0 * 1000);
  return d.toLocaleDateString(undefined, { weekday: "short" }) + " "
    + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function distance(km, imperial) {
  if (km === null || km === undefined) return "";
  return imperial ? `${(km / 1.609344).toFixed(1)} mi` : `${km.toFixed(1)} km`;
}

// `data` is /api/coach. `pick` the index into data.drives (default: the last).
export function coachView(data, { locked = false, imperial = true, pick = null } = {}) {
  if (!data || !data.present) {
    return { state: "absent", simulated: false, source: "", sourceTone: "", moving: locked,
             headline: { n: null, text: "—", band: "No score yet", partial: false, note: "" },
             subs: [], facts: [], hint: null, hintNote: "", trend: [], index: -1, count: 0,
             title: "Hybrid coach",
             line: "No drive scored yet. Park after a drive, or run: omacar coach sim" };
  }
  const sim = !!data.simulated;
  const drives = data.drives || [];
  const index = pick === null || pick < 0 || pick >= drives.length ? drives.length - 1 : pick;
  const d = drives[index] || data.last;
  const hd = d.headline || {};
  const word = (src) => (sim ? SOURCE_WORD.sim : SOURCE_WORD[src] || "");
  const subs = KEYS.map((k) => {
    const s = (d.subs && d.subs[k]) || { score: null, why: "not enough data", source: "measured" };
    const ex = EXPLAIN[k];
    const have = s.score !== null && s.score !== undefined;
    return {
      key: k, label: ex.label, what: ex.what,
      score: have ? s.score : null, tone: toneOf(have ? s.score : null),
      value: have ? valueText(k, s) : "",
      source: word(s.source), sourceKind: sim ? "sim" : s.source,
      how: sim ? "Made up by the simulator." : ex.how[s.source] || "",
      why: have ? "" : cap(s.why || "not enough data"),
    };
  });
  const facts = [];
  const a = d.facts && d.facts.autostop;
  if (a) {
    facts.push({ key: "autostop", label: "Engine off at stops", source: word("measured"),
      text: a.share !== null && a.share !== undefined ? `${a.share}% of ${fmtSecs(a.seconds)} stopped` : cap(a.why || "not enough data"),
      have: a.share !== null && a.share !== undefined });
  }
  const e = d.facts && d.facts.economy;
  if (e) {
    const have = e.pct !== null && e.pct !== undefined;
    facts.push({ key: "economy", label: "Fuel against your usual", source: word("estimated"),
      text: have ? (e.pct === 0 ? "Same as your usual" : `${Math.abs(e.pct)}% ${e.pct > 0 ? "less" : "more"} than your usual (${e.n} drives of this length)`)
                 : cap(e.why || "not enough data"),
      have });
  }
  const trend = drives.map((x, i) => ({ i, score: x.score !== undefined ? x.score : (x.headline || {}).score, t0: x.t0 }));
  const hint = d.hint && d.hint.text ? d.hint : null;
  const n = hd.score === null || hd.score === undefined ? null : hd.score;
  return {
    state: locked ? "moving" : "ready",
    simulated: sim, moving: locked,
    source: sim ? "Simulated" : "From the drive log",
    sourceTone: sim ? "warn" : "ok",
    title: "Hybrid coach",
    headline: {
      n, text: n === null ? "—" : String(n), band: band(n), tone: toneOf(n),
      partial: !!hd.partial,
      note: n === null ? cap(hd.note || "not enough data") : hd.partial ? cap(hd.note || "Partial score") : "",
    },
    subs, facts,
    // Held back while moving, never merely hidden by CSS: it is not in the view.
    hint: locked ? null : hint,
    hintNote: locked ? "Hints wait until you are parked." : "",
    trend, index, count: drives.length,
    isLast: index === drives.length - 1,
    when: when(d.t0), km: distance(d.km, imperial), minutes: d.minutes,
    line: "",
  };
}

function valueText(k, s) {
  if (k === "regen") return `${s.value}% recovered`;
  if (k === "smooth") return s.value !== null ? `${s.value} m/s³ jerk` : "";
  return `${s.value}% foot off`;
}
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const fmtSecs = (s) => (s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);

// Home's card says less, and says nothing but "Drive in progress" on the move.
export function cardView(data, { locked = false, imperial = true } = {}) {
  if (locked) return { state: "moving", title: "Hybrid coach", big: "", line: "Drive in progress",
                       source: "", sourceTone: "", hint: "", tone: "none" };
  const v = coachView(data, { locked, imperial });
  if (v.state === "absent") return { state: "absent", title: v.title, big: "—", line: v.line,
                                     source: "", sourceTone: "", hint: "", tone: "none" };
  return {
    state: "ready", title: v.title, big: v.headline.text,
    line: v.headline.n === null ? v.headline.note : `${v.headline.band}${v.headline.partial ? " · partial" : ""}`,
    source: v.source, sourceTone: v.sourceTone, tone: v.headline.tone || "none",
    hint: v.hint ? v.hint.text : "", when: [v.when, v.km].filter(Boolean).join(" · "),
  };
}
