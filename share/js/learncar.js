// Learn my car: what the guided session says, and the two requests that drive it.
//
// The session itself runs on the tablet (lib/learncar.py): it listens to the
// bus while the owner does one thing at a marked moment, and writes down when.
// This is the pure half of the screen -- learnView() turns /api/learncar into
// exactly what to draw (test/js/learncar.test.js) -- plus the fetches.
//
// TWO RULES THE SCREEN KEEPS.
//  * Where the data came from is always on screen: "Simulated" in the warn
//    tone for the synthetic bus, "Listening to the car" for the real one.
//  * Parked only. store.lockedAsMoving greys every control that starts or
//    continues listening, with the reason, and never hides one. The session
//    itself refuses too (lib/learncar.py), so a stale screen cannot start one.

import { withToken } from "./core.js";

const pad = (n) => String(n);
export const secs = (s) => `${Math.max(0, Math.ceil(s - 1e-6))}`;

// "0x17C byte 3" / "0x1A6 bytes 0–1".
export function where(c) {
  const sp = c.span && c.span[0] !== c.span[1] ? `bytes ${c.span[0]}–${c.span[1]}` : `byte ${c.byte}`;
  return { id: `0x${c.id}`, bytes: sp };
}

const hex = (v) => (v === null || v === undefined ? "" : v.toString(16).toUpperCase().padStart(2, "0"));

// One candidate, ready to draw. `pct` is the confidence bar; `tone` says
// whether it earned "strong" (every window, at least three of them) or is a
// hint to repeat.
export function candidateView(c) {
  const w = where(c);
  // "changes only while the brake is held": the sentence without its address
  // (shown beside it) and without the fraction (shown as a meter).
  const head = `${w.id} ${w.bytes} `;
  const what = (c.text.startsWith(head) ? c.text.slice(head.length) : c.text).split(" — ")[0];
  return {
    id: w.id, bytes: w.bytes, text: c.text, what,
    frac: `${c.hits}/${c.windows} window${c.windows === 1 ? "" : "s"}`,
    pct: c.windows ? Math.round((100 * c.hits) / c.windows) : 0,
    strong: !!c.strong,
    tone: c.strong ? "ok" : "warn",
    value: c.kind === "switch" ? `${hex(c.rest_value)} → ${hex(c.active_value)}` : "analog",
    kind: c.kind,
    also: c.also_in && c.also_in.length ? `Also moves during ${c.also_in.join(", ")}` : "",
  };
}

// The results screen: one block per thing that was listened to, in script
// order, with at most `top` candidates each.
export function resultsView(res, { top = 3 } = {}) {
  if (!res || !res.labels) return { empty: true, blocks: [], captures: 0 };
  const blocks = Object.values(res.labels).map((l) => ({
    label: l.label, title: l.title, kicker: l.kicker, windows: l.windows,
    candidates: (l.candidates || []).slice(0, top).map(candidateView),
    more: Math.max(0, (l.candidates || []).length - top),
    // Nothing standing out is a result. Say why it might, in plain words.
    none: !(l.candidates || []).length,
    noneWhy: l.windows < 2
      ? "Only one pass. Run it again to be sure."
      : "No byte sat still and then moved only during this. A rolling counter or noisy value doesn't count.",
  }));
  return { empty: !blocks.length, blocks, captures: res.captures || 0 };
}

// The step rail: a lamp per step. `now` is the current step while running.
function lampFor(step, i, st) {
  if (st.status === "running" && i === st.idx) return "now";
  return step.state;   // pending | done | failed | skipped
}

function stepDetail(step) {
  const k = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(n));
  if (step.state === "done") return `${step.windows} windows · ${k(step.frames)} frames`;
  if (step.state === "failed") return step.why || "Nothing was heard";
  if (step.state === "skipped") return "Skipped";
  return step.engine ? "Engine running" : `${step.seconds} s`;
}

// What to draw. `st` is /api/learncar; `ctx`: locked (store.lockedAsMoving),
// elapsed (seconds since `st` was fetched, so countdowns run between polls),
// prefs ({sim, trans}) and `viewing` (the user asked for the results).
export function learnView(st, { locked = false, elapsed = 0, prefs = {}, viewing = false } = {}) {
  st = st || {};
  const running = st.status === "running" && !!st.current;
  const sim = running || st.status === "finished" ? st.source === "sim" : !!prefs.sim;
  const source = sim ? { text: "Simulated", tone: "warn" }
    : running || st.status === "finished" ? { text: "Listening to the car", tone: "ok" }
    : { text: "Car", tone: "" };
  const steps = (st.steps || []).map((s, i) => ({
    n: i + 1, id: s.id, title: s.title, kicker: s.kicker, engine: !!s.engine,
    lamp: lampFor(s, i, st), detail: stepDetail(s), teaches: s.teaches,
  }));
  const res = sim ? st.results_sim : st.results;
  const results = resultsView(res);
  const base = { source, steps, results, hasResults: !results.empty, locked };

  if (running) {
    const c = st.current, ph = st.phase;
    const left = (v) => (v === null || v === undefined ? null : Math.max(0, v - elapsed));
    const g = {
      n: c.n, of: c.of, title: c.title, kicker: c.kicker, phase: ph, prep: c.prep,
      engine: c.engine, confirmed: !!c.confirmed, frames: c.frames || 0,
      say: null, kind: null, big: null, bigUnit: "", lamp: "off", lampText: "",
      segments: (c.segments || []).map((s, i) => ({ kind: s.kind, state: s.state, seconds: s.seconds,
        cur: ph === "listening" && i === c.seg, past: ph === "listening" && i < c.seg })),
      segProgress: 0, primary: null, primaryOn: false, retry: false, note: "",
    };
    if (ph === "ready") {
      g.say = c.title;
      g.lamp = "off"; g.lampText = "Not listening yet";
      const needConfirm = c.engine && !c.confirmed;
      g.primary = "Start this step";
      g.primaryOn = !locked && !needConfirm;
      g.note = locked ? "Parked only: stop before continuing"
        : needConfirm ? "Confirm the parking brake first"
        : st.blocked || "";
      g.skip = true;
    } else if (ph === "countdown") {
      const r = left(c.countdown);
      g.say = "Get ready"; g.big = secs(r); g.bigUnit = "Starting";
      g.lamp = "arm"; g.lampText = "Starting to listen…";
    } else if (ph === "starting") {
      g.say = "Connecting to the bus…"; g.lamp = "arm"; g.lampText = "Starting to listen…";
    } else if (ph === "listening") {
      const r = left(c.remaining);
      g.say = c.say; g.kind = c.kind;
      g.big = secs(r); g.bigUnit = c.kind === "act" ? "Now" : "Hold still";
      g.lamp = "on";
      g.lampText = `Listening · ${(c.frames || 0).toLocaleString("en-US")} frames`;
      const seg = (c.segments || [])[c.seg] || { seconds: 1 };
      g.segProgress = Math.max(0, Math.min(1, 1 - (r === null ? 1 : r) / Math.max(0.1, seg.seconds)));
    } else if (ph === "saving") {
      g.say = "Saving what it heard…"; g.lamp = "arm"; g.lampText = "Saving";
    } else if (ph === "review") {
      const me = (st.steps || [])[st.idx] || {};
      const bad = me.state === "failed";
      g.say = bad ? "That one didn't take" : "Got it";
      g.lamp = "off"; g.lampText = bad ? (me.why || "Nothing was heard")
        : `Heard ${(me.frames || 0).toLocaleString("en-US")} frames in ${me.windows} windows`;
      g.primary = st.idx + 1 >= (st.steps || []).length ? "Done — see results" : "Done — next";
      g.primaryOn = true; g.retry = !locked;
    }
    return Object.assign(base, { screen: "guide", guide: g, notice: null });
  }

  // Not running: the start screen, or the results.
  if ((st.status === "finished" || viewing) && results.blocks.length)
    return Object.assign(base, { screen: "results", notice: null });
  let notice = null;
  if (st.status === "refused") notice = { tone: "bad", text: st.reason || "Not started" };
  else if (st.status === "aborted") notice = { tone: "", text: "Session stopped. Nothing was written to the car." };
  else if (st.status === "stalled") notice = { tone: "warn", text: st.reason || "The session stopped responding" };
  const canStart = !locked;
  return Object.assign(base, {
    screen: "start", notice, canStart,
    startNote: locked ? "Parked only: stop before starting" : "",
    prefs: { sim: !!prefs.sim, trans: prefs.trans || "manual" },
  });
}

// ---- the requests
export async function readLearn() {
  const r = await fetch(withToken("/api/learncar"), { cache: "no-store" });
  if (!r.ok) throw new Error(pad(r.status));
  return r.json();
}

// Resolves {ok, error}: a refusal (the car is moving) is an answer to show.
export async function act(action, extra = {}) {
  const r = await fetch(withToken("/api/learncar"), {
    method: "POST", cache: "no-store", body: JSON.stringify(Object.assign({ action }, extra)),
  });
  let body = {};
  try { body = await r.json(); } catch { /* not JSON */ }
  return { ok: r.ok, error: body.error || (r.ok ? "" : `Request failed (${r.status})`) };
}
