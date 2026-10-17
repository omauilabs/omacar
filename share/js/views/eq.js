// The equalizer for the AUX output (lib/eq.py, doc/eq.md).
//
// Ten parametric bands on a frequency-response graph. Everything the tablet
// plays -- music, the radio and the drowsy-driver ALERTS -- goes through this
// on its way to the car radio, so two things hold whatever is drawn here:
// a band never goes past +-12 dB, and the preamp is set so the whole curve's
// maximum is at or below 0 dB. The curve on screen is computed with the same
// biquads PipeWire runs (eqmath.js), so the line you see is the line the
// preamp was worked out from.
//
// EDITING IS PARKED ONLY, with the mechanism Home's Customize uses: store.
// lockedAsMoving greys the controls, with the reason on them, and never hides
// them. Picking a saved preset, the bypass and the hold-to-compare stay live
// at speed -- one tap, and nothing to aim.
//
// A slider on its way goes to /api/eq/live (parameters only, no restart); the
// whole state is written when the gesture ends.

import { h, clear, store, api, toast, confirmDialog } from "../core.js";
import * as M from "../eqmath.js";
import { meterIn, resume } from "../audiobus.js";

const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}, ...kids) => {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  for (const k of kids) n.appendChild(k);
  return n;
};
const clone = (o) => JSON.parse(JSON.stringify(o));
const MARGIN = { l: 40, r: 16, t: 14, b: 26 };
const FREQ_TICKS = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
const DB_TICKS = [12, 6, 0, -6, -12];
const CURVE_PTS = M.logGrid(180);
const NOISE_SECS = 10;
const NOISE_DBFS = -30;
const reduced = () => !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

export default function eqView(root) {
  let alive = true;
  const S = {
    loaded: false, reachable: true, present: false, why: "Loading",
    running: false, applied: false,
    state: { enabled: false, preset: "Flat", bands: M.defaultBands(), preamp: null, user_presets: {} },
    builtin: { Flat: M.defaultBands() },
    sel: 3, auto: 0, pre: 0,
  };
  let disp = null;          // bands as drawn while a preset is morphing
  let W = 0, H = 0;         // the plot's inner size
  let flatHeld = false;
  const timers = new Set();
  const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); return t; };

  const bands = () => S.state.bands;
  const shown = () => disp || S.state.bands;
  const locked = () => store.lockedAsMoving;

  // ---------------------------------------------------------------- the server
  async function load() {
    try {
      const d = await api.eq();
      if (!alive) return;
      S.reachable = true;
      adopt(d, true);
    } catch {
      if (!alive) return;
      S.reachable = false;
      S.why = "OmaCar's server can't be reached";
    }
    S.loaded = true;
    recalc(); paintAll();
  }

  function adopt(d, withState) {
    S.present = !!d.present; S.why = d.why || "";
    S.running = !!d.running; S.applied = !!d.applied;
    if (d.presets) S.builtin = d.presets.builtin;
    if (withState && d.state) S.state = d.state;
    else if (d.state) S.state.user_presets = d.state.user_presets;
  }

  let inflight = false, again = false, commitTimer = null;
  function commitSoon(ms = 420) {
    clearTimeout(commitTimer);
    commitTimer = setTimeout(commitNow, ms);
    timers.add(commitTimer);
  }
  async function commitNow() {
    clearTimeout(commitTimer); commitTimer = null;
    if (inflight) { again = true; return; }
    inflight = true;
    try {
      const d = await api.saveEq(S.state);
      S.reachable = true;
      if (alive) adopt(d, false);
    } catch (e) {
      S.reachable = false;
      if (alive) toast("The equalizer could not be saved: " + (e.message || e), "bad");
    }
    inflight = false;
    if (again) { again = false; return commitNow(); }
    if (alive) paintHead();
  }

  // Slider traffic: at most one request in flight, always carrying the latest.
  const pend = { bands: {}, any: false };
  let liveBusy = false, liveTimer = null;
  function sendLive(i, patch) {
    if (!S.present || !S.state.enabled) return;
    pend.bands[i] = Object.assign(pend.bands[i] || {}, patch);
    pend.any = true;
    if (!liveTimer && !liveBusy) liveTimer = setTimeout(flushLive, 55);
  }
  async function flushLive() {
    liveTimer = null;
    if (!pend.any || !alive) return;
    const body = { bands: pend.bands };
    if (S.state.preamp !== null) body.preamp = S.state.preamp;
    pend.bands = {}; pend.any = false; liveBusy = true;
    try { await api.eqLive(body); } catch { /* the commit that follows carries the final values */ }
    liveBusy = false;
    if (pend.any && alive) liveTimer = setTimeout(flushLive, 55);
  }

  // ---------------------------------------------------------------- the maths
  function recalc() {
    S.auto = M.autoPreamp(bands());
    S.pre = M.effectivePreamp(bands(), S.state.preamp);
  }

  function edit(i, patch, { structural = false } = {}) {
    if (locked()) return;
    const b = bands()[i];
    if (patch.freq !== undefined) patch.freq = Math.min(M.FREQ_MAX, Math.max(M.FREQ_MIN, Math.round(patch.freq * 10) / 10));
    if (patch.gain !== undefined) patch.gain = Math.min(M.GAIN_MAX, Math.max(-M.GAIN_MAX, Math.round(patch.gain * 10) / 10));
    if (patch.q !== undefined) patch.q = Math.min(M.Q_MAX, Math.max(M.Q_MIN, Math.round(patch.q * 100) / 100));
    Object.assign(b, patch);
    if (M.isCut(b.type)) b.gain = 0;
    S.state.preset = null;
    disp = null;
    recalc(); drawGraph(); paintKeys(); paintInspector(); paintHead(); paintPresets();
    if (structural) { commitNow(); return; }
    const live = {};
    for (const k of ["freq", "gain", "q"]) if (patch[k] !== undefined) live[k] = b[k];
    sendLive(i, live);
    commitSoon();
  }

  // ---------------------------------------------------------------- morphing
  let morph = null;
  function morphTo(target) {
    const from = clone(shown());
    cancelAnimationFrame(morph);
    const done = () => { disp = null; drawGraph(); };
    if (reduced()) { done(); return; }
    const t0 = performance.now(), D = 200;
    const step = (now) => {
      const t = Math.min(1, (now - t0) / D), e = t * t * (3 - 2 * t);
      disp = target.map((b, i) => ({
        type: t < 0.5 ? from[i].type : b.type, on: b.on,
        freq: Math.exp(Math.log(from[i].freq) + (Math.log(b.freq) - Math.log(from[i].freq)) * e),
        gain: from[i].gain + (b.gain - from[i].gain) * e,
        q: from[i].q + (b.q - from[i].q) * e }));
      drawGraph();
      if (t < 1 && alive) morph = requestAnimationFrame(step); else done();
    };
    morph = requestAnimationFrame(step);
  }

  function pickPreset(name) {
    const user = S.state.user_presets[name];
    const src = S.builtin[name] || (user && user.bands);
    if (!src) return;
    const target = clone(src).map((b) => Object.assign({ on: true }, b));
    disp = clone(bands());
    S.state.bands = target;
    S.state.preset = name;
    S.state.preamp = user ? user.preamp : null;
    recalc();
    morphTo(target);
    paintAll();
    commitNow();
  }

  // ---------------------------------------------------------------- the screen
  const lamp = h("span.eq-lamp");
  const statusV = h("div.eq-v");
  const roomV = h("div.eq-v.eq-num");
  const trimDown = h("button.eq-well.eq-well-sm", { type: "button", "aria-label": "Lower the preamp", onclick: () => trim(-0.5) }, "−");
  const trimUp = h("button.eq-well.eq-well-sm", { type: "button", "aria-label": "Raise the preamp towards automatic", onclick: () => trim(0.5) }, "+");
  const abBtn = h("button.eq-key.eq-ab", { type: "button", title: "Hold to hear it without the equalizer" },
    h("span.eq-k", "Compare"), h("span.eq-v2", "Hold for flat"));
  const onBtn = h("button.eq-seg", { type: "button", onclick: () => setEnabled(true) }, "On");
  const offBtn = h("button.eq-seg", { type: "button", onclick: () => setEnabled(false) }, "Bypass");

  const head = h("header.eq-head",
    h("div.eq-id", lamp, h("div.eq-cap", h("div.eq-k", "Equalizer"), statusV)),
    h("div.eq-room", h("div.eq-cap", h("div.eq-k", "Headroom"), roomV), h("div.eq-trim", trimDown, trimUp)),
    abBtn,
    h("div.eq-track.eq-onoff", { role: "group", "aria-label": "Equalizer" }, onBtn, offBtn));

  function trim(d) {
    if (locked()) return;
    const cur = S.state.preamp === null ? S.auto : Math.min(S.auto, S.state.preamp);
    const next = Math.min(S.auto, Math.max(-24, Math.round((cur + d) * 10) / 10));
    S.state.preamp = next >= S.auto ? null : next;
    S.state.preset = null;
    recalc(); paintHead(); paintPresets();
    if (S.present && S.state.enabled) api.eqLive({ preamp: S.state.preamp === null ? S.auto : S.state.preamp }).catch(() => {});
    commitSoon();
  }

  function setEnabled(on) {
    S.state.enabled = on;
    paintHead(); commitNow();
  }

  // The plot.
  const svg = sv("svg", { class: "eq-svg", role: "img", "aria-label": "Frequency response, 20 hertz to 20 kilohertz, plus or minus 12 decibels" });
  const gradId = "eq-fade";
  const defs = sv("defs", {}, sv("linearGradient", { id: gradId, x1: 0, y1: 0, x2: 0, y2: 1 },
    sv("stop", { offset: 0, class: "eq-stop-top" }), sv("stop", { offset: 1, class: "eq-stop-bot" })));
  const gGrid = sv("g", { class: "eq-grid" });
  const gLabels = sv("g", { class: "eq-labels" });
  const pSpec = sv("path", { class: "eq-spec" });
  const pOwnArea = sv("path", { class: "eq-own-area" });
  const pOwn = sv("path", { class: "eq-own" });
  const pArea = sv("path", { class: "eq-area", fill: `url(#${gradId})` });
  const pLine = sv("path", { class: "eq-line" });
  const gHandles = sv("g", { class: "eq-handles" });
  const gPlot = sv("g", { transform: `translate(${MARGIN.l} ${MARGIN.t})` });
  gPlot.append(gGrid, pSpec, pOwnArea, pOwn, pArea, pLine, gHandles);
  svg.append(defs, gLabels, gPlot);
  const handles = Array.from({ length: M.NBANDS }, (_, i) => {
    const hit = sv("circle", { class: "eq-hit", r: 26 });
    const ring = sv("circle", { class: "eq-ring", r: 13 });
    const dot = sv("circle", { class: "eq-dot", r: 7 });
    const num = sv("text", { class: "eq-hn", "text-anchor": "middle", dy: "0.35em" });
    num.textContent = String(i + 1);
    const g = sv("g", { class: "eq-h", "data-i": i }, hit, ring, dot, num);
    gHandles.appendChild(g);
    return g;
  });
  const tip = h("div.eq-tip", { hidden: true });
  const plot = h("div.eq-plot", svg, tip);
  const graph = h("section.eq-graph", { "aria-label": "Frequency response" }, plot);

  function sizePlot() {
    const r = plot.getBoundingClientRect();
    const w = Math.max(60, Math.round(r.width - MARGIN.l - MARGIN.r));
    const hh = Math.max(60, Math.round(r.height - MARGIN.t - MARGIN.b));
    if (w === W && hh === H) return;
    W = w; H = hh;
    svg.setAttribute("viewBox", `0 0 ${r.width} ${r.height}`);
    clear(gGrid); clear(gLabels);
    for (const db of DB_TICKS) {
      const y = M.gainToY(db, H);
      gGrid.appendChild(sv("line", { x1: 0, x2: W, y1: y, y2: y, class: db === 0 ? "eq-zero" : "eq-gl" }));
      const t = sv("text", { x: MARGIN.l - 8, y: MARGIN.t + y, class: "eq-lab", "text-anchor": "end", dy: "0.35em" });
      t.textContent = (db > 0 ? "+" : db < 0 ? "−" : "") + Math.abs(db);
      gLabels.appendChild(t);
    }
    for (const f of FREQ_TICKS) {
      const x = M.freqToX(f, W);
      gGrid.appendChild(sv("line", { x1: x, x2: x, y1: 0, y2: H, class: "eq-gl" }));
      const t = sv("text", { x: MARGIN.l + x, y: MARGIN.t + H + 17, class: "eq-lab",
        "text-anchor": f === 20 ? "start" : f === 20000 ? "end" : "middle" });
      t.textContent = M.fmtFreqShort(f);
      gLabels.appendChild(t);
    }
    drawGraph();
  }

  function drawGraph() {
    if (!W) return;
    const bs = shown();
    const db = M.curveDb(bs, CURVE_PTS);
    const line = M.linePath(CURVE_PTS, db, W, H);
    pLine.setAttribute("d", line);
    pArea.setAttribute("d", M.areaPath(line, W, H));
    const sel = bs[S.sel];
    if (sel && sel.on) {
      const own = M.linePath(CURVE_PTS, CURVE_PTS.map((f) => M.bandDb(sel, f)), W, H);
      pOwn.setAttribute("d", own);
      const z = M.gainToY(0, H);
      pOwnArea.setAttribute("d", own + `L${W} ${z}L0 ${z}Z`);
    } else { pOwn.setAttribute("d", ""); pOwnArea.setAttribute("d", ""); }
    bs.forEach((b, i) => {
      const [x, y] = M.handleAt(b, W, H);
      handles[i].setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
      handles[i].classList.toggle("sel", i === S.sel);
      handles[i].classList.toggle("off", !b.on);
    });
    gHandles.appendChild(handles[S.sel]);        // the selected one on top
    graph.classList.toggle("eq-flat", flatHeld || !S.state.enabled);
  }

  // Pointer: one finger drags a handle, two pinch the selected band's Q.
  const fingers = new Map();
  let drag = null, pinch = null;
  const local = (e) => { const r = svg.getBoundingClientRect(); return [e.clientX - r.left - MARGIN.l, e.clientY - r.top - MARGIN.t]; };
  function showTip(i) {
    const b = bands()[i], [x, y] = M.handleAt(b, W, H);
    tip.hidden = false;
    clear(tip);
    tip.append(h("b", M.fmtFreq(b.freq)),
      M.isCut(b.type) ? h("span", "Q " + b.q.toFixed(2)) : h("span", M.fmtDb(b.gain) + " dB"));
    const px = Math.min(Math.max(MARGIN.l + x, 70), MARGIN.l + W - 70);
    const above = MARGIN.t + y > 74;
    tip.style.left = px + "px";
    tip.style.top = (MARGIN.t + y + (above ? -30 : 30)) + "px";
  }
  const hideTip = () => { tip.hidden = true; };
  svg.addEventListener("pointerdown", (e) => {
    const [x, y] = local(e);
    fingers.set(e.pointerId, [x, y]);
    if (fingers.size === 2 && !locked()) {
      const [a, b] = [...fingers.values()];
      pinch = { q: bands()[S.sel].q, d: Math.hypot(a[0] - b[0], a[1] - b[1]) };
      drag = null;
      svg.setPointerCapture(e.pointerId);
      return;
    }
    const i = M.nearestHandle(shown(), x, y, W, H, S.sel);
    if (i < 0) return;
    select(i);
    if (locked()) return;
    const [hx, hy] = M.handleAt(bands()[i], W, H);
    drag = { id: e.pointerId, i, dx: hx - x, dy: hy - y };
    handles[i].classList.add("grab");
    svg.setPointerCapture(e.pointerId);
    showTip(i);
    e.preventDefault();
  });
  svg.addEventListener("pointermove", (e) => {
    if (!fingers.has(e.pointerId)) return;
    const [x, y] = local(e);
    fingers.set(e.pointerId, [x, y]);
    if (pinch && fingers.size >= 2) {
      const [a, b] = [...fingers.values()];
      edit(S.sel, { q: M.pinchQ(pinch.q, pinch.d, Math.hypot(a[0] - b[0], a[1] - b[1])) });
      showTip(S.sel);
    } else if (drag && drag.id === e.pointerId) {
      edit(drag.i, M.dragBand(bands()[drag.i], x + drag.dx, y + drag.dy, W, H));
      showTip(drag.i);
    }
  });
  const lift = (e) => {
    fingers.delete(e.pointerId);
    if (drag && drag.id === e.pointerId) { handles[drag.i].classList.remove("grab"); drag = null; commitNow(); }
    if (fingers.size < 2 && pinch) { pinch = null; commitNow(); }
    if (!drag && !pinch) later(hideTip, 700);
  };
  svg.addEventListener("pointerup", lift);
  svg.addEventListener("pointercancel", lift);
  svg.addEventListener("wheel", (e) => {
    if (locked()) return;
    e.preventDefault();
    edit(S.sel, { q: bands()[S.sel].q * (e.deltaY > 0 ? 0.92 : 1.08) });
    commitSoon();
  }, { passive: false });

  // The band keys.
  const keys = Array.from({ length: M.NBANDS }, (_, i) =>
    h("button.eq-seg.eq-bk", { type: "button", onclick: () => select(i) },
      h("span.eq-k", String(i + 1)), h("span.eq-v2.eq-num", "")));
  const keyTrack = h("div.eq-track.eq-keys", { role: "group", "aria-label": "Bands" }, keys);
  function select(i) {
    S.sel = i; drawGraph(); paintKeys(); paintInspector();
  }

  // The inspector.
  const inspTitle = h("div.eq-cap", h("div.eq-k", "Band"), h("div.eq-v.eq-num"));
  const typeBtns = M.TYPES.map((t) =>
    h("button.eq-seg", { type: "button", data: { t }, onclick: () => setType(t) }, M.TYPE_NAMES[t]));
  const typeTrack = h("div.eq-track.eq-types", { role: "group", "aria-label": "Band type" }, typeBtns);
  const bOn = h("button.eq-seg", { type: "button", onclick: () => setBandOn(true) }, "On");
  const bOff = h("button.eq-seg", { type: "button", onclick: () => setBandOn(false) }, "Off");
  const onTrack = h("div.eq-track.eq-bandon", { role: "group", "aria-label": "Band on or off" }, bOn, bOff);

  function setType(t) {
    const b = bands()[S.sel];
    if (b.type === t) return;
    edit(S.sel, { type: t, gain: M.isCut(t) ? 0 : b.gain }, { structural: true });
  }
  function setBandOn(on) { edit(S.sel, { on }, { structural: M.isCut(bands()[S.sel].type) }); if (!M.isCut(bands()[S.sel].type)) commitSoon(150); }

  function makeStepper(caption, spec) {
    const v = h("div.eq-big.eq-num");
    const minus = h("button.eq-well", { type: "button", "aria-label": "Lower " + caption.toLowerCase() }, "−");
    const plus = h("button.eq-well", { type: "button", "aria-label": "Raise " + caption.toLowerCase() }, "+");
    const range = h("input.eq-range", { type: "range", min: 0, max: spec.max, step: spec.sstep || 1,
      "aria-label": caption });
    range.addEventListener("input", () => { if (!locked()) edit(S.sel, spec.fromSlider(+range.value)); });
    range.addEventListener("change", () => commitNow());
    for (const [btn, dir] of [[minus, -1], [plus, 1]]) {
      let rep = null, hold = null;
      const stop = () => { clearTimeout(hold); clearInterval(rep); hold = rep = null; };
      btn.addEventListener("pointerdown", (e) => {
        if (btn.disabled) return;
        e.preventDefault(); btn.setPointerCapture(e.pointerId);
        spec.step(dir);
        hold = setTimeout(() => { rep = setInterval(() => spec.step(dir), 85); }, 380);
      });
      for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) btn.addEventListener(ev, () => { if (hold || rep) { stop(); commitNow(); } });
      btn.addEventListener("contextmenu", (e) => e.preventDefault());
      btn.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); spec.step(dir); commitSoon(); } });
    }
    const el = h("div.eq-st", h("div.eq-k", caption),
      h("div.eq-st-row", minus, v, plus), range);
    return { el, v, range, minus, plus };
  }
  const bandNow = () => bands()[S.sel];
  const stF = makeStepper("Frequency", { max: 1000,
    fromSlider: (s) => ({ freq: M.sliderToFreq(s) }),
    step: (d) => edit(S.sel, { freq: bandNow().freq * Math.pow(2, d / 12) }) });
  const stG = makeStepper("Gain", { max: 240,
    fromSlider: (s) => ({ gain: (s - 120) / 10 }),
    step: (d) => edit(S.sel, { gain: bandNow().gain + d * 0.5 }) });
  const stQ = makeStepper("Width (Q)", { max: 1000,
    fromSlider: (s) => ({ q: M.sliderToQ(s) }),
    step: (d) => edit(S.sel, { q: bandNow().q * (d > 0 ? 1.1 : 1 / 1.1) }) });
  const steppers = h("div.eq-steppers", stF.el, stG.el, stQ.el);
  const insp = h("section.eq-insp", h("div.eq-insp-top", inspTitle, typeTrack, onTrack), steppers);

  // The presets.
  const presetTrack = h("div.eq-track.eq-presets", { role: "group", "aria-label": "Presets" });
  const saveBtn = h("button.eq-key.eq-save", { type: "button", onclick: () => openSave("save") },
    h("span.eq-k", "Your own"), h("span.eq-v2", "Save as…"));
  const manageBtn = h("button.eq-key.eq-save", { type: "button", onclick: () => openSave("manage") },
    h("span.eq-k", "Preset"), h("span.eq-v2", "Rename…"));
  const presetRow = h("div.eq-prow", presetTrack, manageBtn, saveBtn);
  const nameIn = h("input.eq-name", { type: "text", maxlength: 24, "aria-label": "Preset name", autocomplete: "off" });
  const savePanel = h("div.eq-savepanel", { hidden: true });
  let saveMode = null;
  function openSave(mode) {
    if (locked()) return;
    saveMode = mode;
    clear(savePanel);
    const cur = S.state.preset;
    nameIn.value = mode === "manage" ? cur : nextName();
    savePanel.append(h("div.eq-k", mode === "save" ? "Save this curve as" : "Rename or delete"), nameIn,
      h("div.eq-sp-row",
        mode === "save"
          ? h("button.eq-key.eq-go", { type: "button", onclick: doSave }, h("span.eq-v2", "Save"))
          : [h("button.eq-key.eq-go", { type: "button", onclick: doRename }, h("span.eq-v2", "Rename")),
             h("button.eq-key.eq-del", { type: "button", onclick: doDelete }, h("span.eq-v2", "Delete"))],
        h("button.eq-key", { type: "button", onclick: closeSave }, h("span.eq-v2", "Cancel"))));
    savePanel.hidden = false;
    nameIn.focus(); nameIn.select();
  }
  function closeSave() { savePanel.hidden = true; saveMode = null; }
  function nextName() {
    for (let n = 1; n < 99; n++) if (!S.state.user_presets["My EQ " + n] && !S.builtin["My EQ " + n]) return "My EQ " + n;
    return "My EQ";
  }
  const cleanName = () => nameIn.value.replace(/\s+/g, " ").trim().slice(0, 24);
  function doSave() {
    const name = cleanName();
    if (!name) return toast("Give it a name first", "warn");
    if (S.builtin[name]) return toast(`"${name}" is a built-in preset's name`, "warn");
    S.state.user_presets[name] = { bands: clone(bands()), preamp: S.state.preamp };
    S.state.preset = name;
    closeSave(); paintPresets(); commitNow();
    toast(`Saved "${name}"`);
  }
  function doRename() {
    const old = S.state.preset, name = cleanName();
    if (!old || !S.state.user_presets[old]) return;
    if (!name || name === old) return closeSave();
    if (S.builtin[name] || S.state.user_presets[name]) return toast(`"${name}" is already a preset`, "warn");
    S.state.user_presets[name] = S.state.user_presets[old];
    delete S.state.user_presets[old];
    S.state.preset = name;
    closeSave(); paintPresets(); commitNow();
  }
  async function doDelete() {
    const old = S.state.preset;
    if (!old || !S.state.user_presets[old]) return;
    closeSave();
    const yes = await confirmDialog({ title: `Delete "${old}"?`, body: "The curve you are hearing stays as it is; only the saved preset goes.", confirm: "Delete" });
    if (!yes || !alive) return;
    delete S.state.user_presets[old];
    S.state.preset = null;
    paintPresets(); commitNow();
  }

  // The foot: the promise about alerts, and the check.
  const noiseBtn = h("button.eq-key.eq-noise", { type: "button", onclick: toggleNoise },
    h("span.eq-k", "Check"), h("span.eq-v2", "Play pink noise"));
  const foot = h("footer.eq-foot",
    h("p.eq-note", "Alerts play through this EQ too. It never boosts past 0 dB, so they keep their headroom."),
    noiseBtn);

  root.classList.add("eq-root");
  clear(root);
  root.append(h("div.eqv", head, graph, keyTrack, insp, presetRow, savePanel, foot));

  // ---------------------------------------------------------------- painting
  function setSeg(btn, on) {
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", on);
  }
  function paintHead() {
    const en = S.state.enabled;
    let status, tone;
    if (!S.reachable) { status = "Not saved: OmaCar's server can't be reached"; tone = "warn"; }
    else if (!S.loaded) { status = "Loading…"; tone = ""; }
    else if (!S.present) { status = "Not applied: " + (S.why || "PipeWire isn't running here"); tone = "warn"; }
    else if (!en) { status = "Bypassed — the sound is untouched"; tone = ""; }
    else if (flatHeld) { status = "Hearing it flat"; tone = "ok"; }
    else if (S.applied) { status = S.state.preset ? "On — " + S.state.preset : "On — your curve"; tone = "ok"; }
    else { status = "Starting… if this stays, run: omacar eq apply"; tone = "warn"; }
    statusV.textContent = status;
    lamp.className = "eq-lamp" + (tone ? " " + tone : "");
    const trimmed = S.state.preamp !== null && S.state.preamp < S.auto;
    roomV.textContent = M.fmtDb(S.pre) + " dB " + (trimmed ? "trimmed" : "auto");
    trimUp.disabled = locked() || !trimmed;
    trimDown.disabled = locked() || S.pre <= -24;
    setSeg(onBtn, en); setSeg(offBtn, !en);
    abBtn.disabled = !S.present || !en;
    abBtn.classList.toggle("held", flatHeld);
    abBtn.querySelector(".eq-v2").textContent = flatHeld ? "Flat — release" : "Hold for flat";
    graph.classList.toggle("eq-flat", flatHeld || !en);
  }
  function paintKeys() {
    bands().forEach((b, i) => {
      const k = keys[i];
      setSeg(k, i === S.sel);
      k.classList.toggle("off", !b.on);
      k.classList.toggle("live", b.on && !M.isCut(b.type) && Math.abs(b.gain) >= 0.05);
      k.querySelector(".eq-v2").textContent = M.fmtFreqShort(b.freq);
    });
  }
  function paintInspector() {
    const b = bands()[S.sel], lk = locked();
    const cap = inspTitle.querySelector(".eq-k");
    cap.textContent = lk ? `Band ${S.sel + 1} \u00B7 Editing is available when you stop` : `Band ${S.sel + 1}`;
    cap.classList.toggle("eq-warn", lk);
    inspTitle.querySelector(".eq-v").textContent = `${M.TYPE_NAMES[b.type]} · ${M.fmtFreq(b.freq)}`;
    for (const t of typeBtns) setSeg(t, t.dataset.t === b.type);
    setSeg(bOn, b.on); setSeg(bOff, !b.on);
    stF.v.textContent = M.fmtFreq(b.freq);
    stF.range.value = M.freqToSlider(b.freq);
    const cut = M.isCut(b.type);
    stG.v.textContent = cut ? "—" : M.fmtDb(b.gain) + " dB";
    stG.range.value = cut ? 120 : Math.round(b.gain * 10) + 120;
    stQ.v.textContent = b.q.toFixed(2);
    stQ.range.value = M.qToSlider(b.q);
    const dis = (el, d) => { el.disabled = d; el.setAttribute("aria-disabled", d ? "true" : "false"); };
    for (const t of typeBtns) dis(t, lk);
    dis(bOn, lk); dis(bOff, lk);
    for (const s of [stF, stQ]) { dis(s.minus, lk); dis(s.plus, lk); dis(s.range, lk); }
    for (const x of [stG.minus, stG.plus, stG.range]) dis(x, lk || cut);
    stG.el.classList.toggle("na", cut);
    insp.classList.toggle("eq-locked", lk);
    insp.title = lk ? "Available when you stop" : "";
  }
  function paintPresets() {
    clear(presetTrack);
    const names = [...Object.keys(S.builtin), ...Object.keys(S.state.user_presets)];
    for (const n of names) {
      const b = h("button.eq-seg", { type: "button", onclick: () => pickPreset(n) }, n);
      setSeg(b, S.state.preset === n);
      presetTrack.appendChild(b);
    }
    const user = !!(S.state.preset && S.state.user_presets[S.state.preset]);
    manageBtn.hidden = !user;
    const lk = locked();
    for (const b of [saveBtn, manageBtn]) { b.disabled = lk; b.title = lk ? "Available when you stop" : ""; }
    const cur = presetTrack.querySelector(".on");
    if (cur) cur.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  function paintLock() {
    const lk = locked();
    if (root.classList.contains("eq-moving") === lk) return;
    root.classList.toggle("eq-moving", lk);
    if (lk) closeSave();
    paintHead(); paintInspector(); paintPresets();
    noiseBtn.disabled = lk;
  }
  function paintAll() {
    paintHead(); paintKeys(); paintInspector(); paintPresets(); drawGraph();
  }

  // ---------------------------------------------------------------- hold to compare
  function flat(on) {
    if (flatHeld === on) return;
    flatHeld = on;
    paintHead();
    const send = () => api.eqLive({ flat: on });
    send().catch(() => send().catch(() => { /* the unit stopped under us */ }));
  }
  abBtn.addEventListener("pointerdown", (e) => {
    if (abBtn.disabled) return;
    e.preventDefault(); abBtn.setPointerCapture(e.pointerId); flat(true);
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) abBtn.addEventListener(ev, () => flat(false));
  abBtn.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); flat(true); } });
  abBtn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") flat(false); });
  const onBlur = () => flat(false);
  window.addEventListener("blur", onBlur);

  // ---------------------------------------------------------------- pink noise
  // On a button, never by itself: quiet (-30 dBFS RMS), and over after ten
  // seconds. It plays through the page's output like everything else, so it goes
  // through the equalizer -- which is the point of listening to it.
  let noise = null;
  function pinkBuffer(ctx) {
    const n = ctx.sampleRate * 4, buf = ctx.createBuffer(2, n, ctx.sampleRate);
    for (let c = 0; c < 2; c++) {
      const d = buf.getChannelData(c);
      let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0, sum = 0;
      for (let i = 0; i < n; i++) {
        const w = Math.random() * 2 - 1;
        b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759;
        b2 = 0.96900 * b2 + w * 0.1538520; b3 = 0.86650 * b3 + w * 0.3104856;
        b4 = 0.55000 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.0168980;
        const p = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362; b6 = w * 0.115926;
        d[i] = p; sum += p * p;
      }
      const g = Math.pow(10, NOISE_DBFS / 20) / Math.sqrt(sum / n);
      for (let i = 0; i < n; i++) d[i] *= g;
    }
    return buf;
  }
  async function toggleNoise() {
    if (noise) { stopNoise(); return; }
    if (locked()) return;
    await resume();
    const an = meterIn(), ctx = an.context;
    if (ctx.state !== "running") { toast("The browser won't play sound until the screen is tapped once more", "warn"); return; }
    const src = ctx.createBufferSource(), g = ctx.createGain();
    src.buffer = pinkBuffer(ctx); src.loop = true;
    g.gain.setValueAtTime(0, ctx.currentTime);
    g.gain.linearRampToValueAtTime(1, ctx.currentTime + 0.12);
    src.connect(g); g.connect(an);
    src.start();
    noise = { src, g, ctx, left: NOISE_SECS, tick: setInterval(() => {
      if (--noise.left <= 0) stopNoise(); else paintNoise();
    }, 1000) };
    paintNoise();
  }
  function stopNoise() {
    if (!noise) return;
    const { src, g, ctx, tick } = noise;
    noise = null; clearInterval(tick);
    try {
      g.gain.cancelScheduledValues(ctx.currentTime);
      g.gain.setValueAtTime(g.gain.value, ctx.currentTime);
      g.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.12);
      src.stop(ctx.currentTime + 0.15);
    } catch { /* already stopped */ }
    paintNoise();
  }
  function paintNoise() {
    noiseBtn.classList.toggle("held", !!noise);
    noiseBtn.querySelector(".eq-v2").textContent = noise ? `Stop (${noise.left} s)` : "Play pink noise";
  }

  // ---------------------------------------------------------------- the spectrum
  // What is actually leaving the page, faint behind the curve: only while this
  // screen is up and the tab is visible.
  let raf = 0;
  const SPEC_PTS = M.logGrid(96);
  let bins = null;
  function spectrum() {
    raf = 0;
    if (!alive) return;
    if (!document.hidden && W) {
      try {
        const an = meterIn();
        if (an.context.state === "running") {
          if (!bins || bins.length !== an.frequencyBinCount) bins = new Float32Array(an.frequencyBinCount);
          an.getFloatFrequencyData(bins);
          const hz = an.context.sampleRate / an.fftSize;
          let d = "";
          SPEC_PTS.forEach((f, i) => {
            const lo = Math.max(1, Math.floor(f / 1.04 / hz)), hi = Math.min(bins.length - 1, Math.max(lo, Math.ceil(f * 1.04 / hz)));
            let m = -140; for (let k = lo; k <= hi; k++) if (bins[k] > m) m = bins[k];
            const v = Math.min(1, Math.max(0, (m + 96) / 66));          // -96 .. -30 dBFS
            d += (i ? "L" : "M") + M.freqToX(f, W).toFixed(1) + " " + (H - v * H * 0.62).toFixed(1);
          });
          pSpec.setAttribute("d", d + `L${W} ${H}L0 ${H}Z`);
        } else pSpec.setAttribute("d", "");
      } catch { pSpec.setAttribute("d", ""); }
    }
    raf = requestAnimationFrame(spectrum);
  }

  // ---------------------------------------------------------------- go
  const ro = new ResizeObserver(() => sizePlot());
  ro.observe(plot);
  const offLive = store.on("live", paintLock);
  paintAll();
  sizePlot();
  load();
  raf = requestAnimationFrame(spectrum);
  const onVis = () => { if (!document.hidden && !raf && alive) raf = requestAnimationFrame(spectrum); };
  document.addEventListener("visibilitychange", onVis);

  return () => {
    alive = false;
    cancelAnimationFrame(raf); cancelAnimationFrame(morph);
    ro.disconnect(); offLive();
    document.removeEventListener("visibilitychange", onVis);
    window.removeEventListener("blur", onBlur);
    stopNoise();
    if (flatHeld) { flatHeld = false; api.eqLive({ flat: false }).catch(() => {}); }
    for (const t of timers) clearTimeout(t);
    clearTimeout(liveTimer);
    // A gesture the finger was still in: write what it had reached.
    if (commitTimer || pend.any) { api.saveEq(S.state).catch(() => {}); }
    root.classList.remove("eq-root", "eq-moving");
  };
}
