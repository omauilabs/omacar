// Vehicle → Live → Learn my car.
//
// A guided, PARKED, PASSIVE routine: it asks the owner to do ONE thing at a
// marked moment while the tablet listens to the bus, then ranks the bytes that
// moved only while that thing was being done (lib/learncar.py, doc/learn-my-car.md).
// Nothing here writes to the car, ever.
//
// Three screens on one stage: the script (start), the guided step (a big
// instruction, a countdown, a listening lamp) and the results.

import { h, icon, store } from "../core.js";
import { learnView, readLearn, act } from "../learncar.js";

const PREFS_KEY = "omacar.learncar";
const EAR = ["M6 9.5a6 6 0 0 1 12 0c0 2.6-1.4 3.7-2.6 4.8-.9.8-1.4 1.6-1.4 2.7a2.5 2.5 0 0 1-4.5 1.5",
             "M9.2 9.6a2.8 2.8 0 0 1 5.6 0"];

function loadPrefs() {
  try { return Object.assign({ sim: false, trans: "manual" }, JSON.parse(localStorage.getItem(PREFS_KEY) || "{}")); }
  catch { return { sim: false, trans: "manual" }; }
}
function savePrefs(p) { try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch { /* private window */ } }

const seg = (name, value, opts, onpick) => {
  const track = h("div.lc-seg", { role: "radiogroup", "aria-label": name });
  for (const [v, label] of opts) {
    track.appendChild(h("button.lc-segb", {
      type: "button", role: "radio", "aria-checked": v === value ? "true" : "false",
      onclick: () => onpick(v),
    }, label));
  }
  return h("div.lc-opt", h("span.lc-optk", name), track);
};

export default function learnCarView(root) {
  const prefs = loadPrefs();
  let last = null, fetchedAt = Date.now(), viewing = false, localNote = null, busy = false;
  let key = "", refs = {}, gone = false, timer = null, anim = null;

  const rail = h("aside.lc-rail");
  const main = h("section.lc-main");
  const node = h("div.lc", rail, main);
  root.appendChild(node);

  const view = () => learnView(last, {
    locked: !!store.lockedAsMoving, elapsed: (Date.now() - fetchedAt) / 1000, prefs, viewing,
  });

  // ---- the step rail
  function paintRail(v) {
    const sig = v.steps.map((s) => s.lamp + s.detail).join("|");
    if (rail.dataset.sig === sig) return;
    rail.dataset.sig = sig;
    rail.textContent = "";
    rail.appendChild(h("div.lc-rail-k", "The routine"));
    for (const s of v.steps) {
      rail.appendChild(h("div.lc-step", { data: { lamp: s.lamp } },
        h("span.lc-lamp", { "aria-hidden": "true" }),
        h("div.lc-step-t", h("span.lc-step-n", `${s.n}`), h("span.lc-step-name", s.title),
          h("span.lc-step-d", `${s.kicker} · ${s.detail}`))));
    }
  }

  const src = (v) => h("span.fr-src.lc-src", { "data-tone": v.source.tone },
    h("span.fr-lamp", { "aria-hidden": "true" }), h("span", v.source.text));

  const tap = (fn) => async () => {
    if (busy) return;
    busy = true; localNote = null;
    try { await fn(); } finally { busy = false; }
    await refresh();
  };
  const post = (action, extra) => async () => {
    const r = await act(action, extra);
    if (!r.ok) localNote = r.error;
  };

  // ---- start
  function buildStart(v) {
    main.textContent = "";
    main.dataset.screen = "start";
    const startBtn = h("button.lc-key", { type: "button", "data-primary": "1", disabled: !v.canStart,
      onclick: tap(async () => {
        viewing = false;
        const r = await act("start", { sim: prefs.sim, transmission: prefs.trans });
        if (!r.ok) localNote = r.error;
      }) }, icon(EAR, 22), h("span", "Begin"));
    const note = h("div.lc-note", { role: "status" });
    const sub = h("div.lc-foot-opts");
    sub.append(
      seg("Source", prefs.sim ? "sim" : "car", [["car", "Car"], ["sim", "Simulated"]],
        (x) => { prefs.sim = x === "sim"; savePrefs(prefs); key = ""; paint(); }),
      seg("Gearbox", prefs.trans, [["manual", "Manual"], ["cvt", "CVT"]],
        (x) => { prefs.trans = x; savePrefs(prefs); key = ""; paint(); }));
    const results = h("button.lc-key", { type: "button", onclick: () => { viewing = true; key = ""; paint(); } },
      h("span", "See what it found"));
    refs = { note, startBtn, results };
    main.append(
      h("div.lc-head", h("span.lc-kicker", "Vehicle · Live"), src(v)),
      h("h1.lc-title", "Learn my car"),
      h("p.lc-lede", "Eight short things to do while you're parked. Press the brake, turn the wheel, flick the lights. "
        + "For each one this tablet listens to the car's bus and notes which bytes moved at that moment."),
      h("ul.lc-facts",
        h("li", h("b", "Only listens."), " It never sends anything to the car: no tests, no writes."),
        h("li", h("b", "Parked only."), " It refuses to start, or carry on, if the car is moving."),
        h("li", h("b", "Two steps run the engine."), " They ask you to confirm the parking brake first."),
        h("li", h("b", "The gauges pause"), " while it listens, and come back after each step.")),
      h("div"),
      h("div.lc-bottom", sub, h("div.lc-actions", startBtn, v.hasResults ? results : null, note)));
  }

  // ---- the guided step
  function buildGuide(v) {
    const g = v.guide;
    main.textContent = "";
    main.dataset.screen = "guide";
    main.dataset.phase = g.phase;
    main.dataset.kind = g.kind || "";
    const big = h("div.lc-big.display-num");
    const bigU = h("div.lc-bigu");
    const say = h("h1.lc-say", g.say);
    const lampText = h("span.lc-ltxt");
    const lamp = h("span.lc-listen", { "data-lamp": g.lamp }, h("span.lc-llamp", { "aria-hidden": "true" }), lampText);
    const segs = h("div.lc-segs", { "aria-hidden": "true" });
    const bars = g.segments.map((s) => {
      const fill = h("span.lc-segfill");
      const el = h("span.lc-seg-b", { data: { kind: s.kind, cur: s.cur ? "1" : "", past: s.past ? "1" : "" },
        style: { flexGrow: String(s.seconds) } }, fill);
      segs.appendChild(el);
      return { el, fill, cur: s.cur };
    });
    const note = h("div.lc-note", { role: "status" });
    const abort = h("button.lc-quiet", { type: "button", onclick: tap(post("abort")) }, "Abort");
    const skip = g.skip ? h("button.lc-quiet", { type: "button", onclick: tap(post("skip")) }, "Skip this step") : null;
    const retry = g.retry ? h("button.lc-key", { type: "button", onclick: tap(post("retry")) }, h("span", "Do it again")) : null;
    let confirm = null;
    if (g.phase === "ready" && g.engine) {
      confirm = h("button.lc-confirm", { type: "button", "aria-pressed": g.confirmed ? "true" : "false",
        onclick: tap(post("confirm")) },
      h("span.lc-tick", g.confirmed ? "✓" : ""),
      h("span", h("b", "Parking brake is ON"), h("small", "Gearbox in neutral (or P), foot clear of the pedals")));
    }
    const primary = g.primary ? h("button.lc-key", { type: "button", "data-primary": "1", disabled: !g.primaryOn,
      onclick: tap(post(g.phase === "review" ? "next" : "go")) }, h("span", g.primary)) : null;
    refs = { big, bigU, lamp, lampText, bars, note, primary };
    main.append(
      h("div.lc-head", h("span.lc-kicker", `Step ${g.n} of ${g.of} · ${g.kicker}`), src(v)),
      h("div.lc-stage",
        h("div.lc-say-w", say, g.phase === "ready" ? h("p.lc-prep", g.prep) : null,
          confirm),
        g.big !== null ? h("div.lc-count", big, bigU) : null),
      g.phase === "listening" || g.phase === "countdown" ? segs : h("div"),
      h("div.lc-foot", lamp, h("div.lc-spacer"), note, abort, skip, retry, primary));
  }

  // ---- results
  function buildResults(v) {
    main.textContent = "";
    main.dataset.screen = "results";
    const r = v.results;
    const grid = h("div.lc-grid");
    for (const b of r.blocks) {
      const card = h("section.lc-card",
        h("div.lc-card-h", h("h2.lc-card-t", b.title), h("span.lc-card-k", `${b.kicker} · ${b.windows} window${b.windows === 1 ? "" : "s"}`)));
      for (const c of b.candidates) {
        card.appendChild(h("div.lc-cand", { data: { tone: c.tone } },
          h("div.lc-cand-top",
            h("span.lc-cand-id", h("b", c.id), ` ${c.bytes}`),
            h("span.lc-cand-v", c.value)),
          h("div.lc-cand-t", c.what),
          h("div.lc-cand-m",
            h("div.lc-meter", { role: "img", "aria-label": c.frac }, h("span.lc-meter-f", { style: { width: `${c.pct}%` } })),
            h("span.lc-cand-f", c.strong ? c.frac : `${c.frac} · a hint`)),
          c.also ? h("div.lc-also", c.also) : null));
      }
      if (b.none) card.appendChild(h("div.lc-none", b.noneWhy));
      if (b.more) card.appendChild(h("div.lc-more", `+${b.more} more in the file`));
      grid.appendChild(card);
    }
    refs = {};
    main.append(
      h("div.lc-head", h("span.lc-kicker", `What it found · ${r.captures} capture${r.captures === 1 ? "" : "s"}`), src(v)),
      h("div.lc-rhead",
        h("h1.lc-title", "Candidates, not findings"),
        h("p.lc-lede", "Each line is a byte that stayed still while nothing was happening and moved only during the thing you did. "
          + "Confirm one by watching it on the car before trusting it."),
        h("div.lc-actions",
          h("button.lc-key", { type: "button", "data-primary": "1", onclick: () => { viewing = false; key = ""; last = Object.assign({}, last, { status: "idle" }); paint(); } }, h("span", "Run it again")))),
      grid);
  }

  // ---- paint: rebuild on a change of screen, update numbers in place
  function paint() {
    if (gone) return;
    const v = view();
    node.dataset.screen = v.screen;
    node.dataset.src = v.source.text === "Simulated" ? "sim" : "car";
    const g = v.guide;
    const k = v.screen + (g ? `${g.phase}|${g.n}|${g.kind}|${g.say}|${g.confirmed}|${g.primaryOn}|${g.retry}` : "")
      + (v.screen === "start" ? `${prefs.sim}${prefs.trans}${v.hasResults}${v.canStart}` : "")
      + (v.screen === "results" ? v.results.captures + JSON.stringify(v.results.blocks.length) : "");
    rail.hidden = v.screen === "results";
    paintRail(v);
    if (k !== key) {
      key = k;
      if (v.screen === "guide") buildGuide(v);
      else if (v.screen === "results") buildResults(v);
      else buildStart(v);
      main.classList.remove("lc-in"); void main.offsetWidth; main.classList.add("lc-in");
    }
    if (v.screen === "start") {
      const n = localNote ? { tone: "bad", text: localNote } : v.notice;
      refs.note.textContent = (n && n.text) || v.startNote || "";
      refs.note.dataset.tone = (n && n.tone) || "";
    } else if (g) {
      if (refs.big && g.big !== null) { refs.big.textContent = g.big; refs.bigU.textContent = g.bigUnit; }
      refs.lamp.dataset.lamp = g.lamp;
      refs.lampText.textContent = g.lampText;
      const cur = refs.bars.find((b) => b.cur);
      if (cur) cur.fill.style.transform = `scaleX(${g.segProgress.toFixed(3)})`;
      const n = localNote || g.note;
      refs.note.textContent = n || "";
      refs.note.dataset.tone = localNote ? "bad" : "";
    }
  }

  async function refresh() {
    try { last = await readLearn(); fetchedAt = Date.now(); } catch { /* keep the last */ }
    paint();
  }

  const loop = async () => {
    await refresh();
    if (gone) return;
    const running = last && last.status === "running";
    timer = setTimeout(loop, running ? 600 : 2000);
  };
  loop();
  // Countdowns run between polls.
  anim = setInterval(() => { if (last && last.status === "running") paint(); }, 100);
  return () => { gone = true; clearTimeout(timer); clearInterval(anim); };
}
