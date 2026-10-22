// Cameras → Clips: the incidents the clip service (lib/clips.py) has kept.
//
// A list of what happened (time, trigger, peak g, speed before and after) and a
// player that plays every camera that was recording side by side, in step, over
// a strip of the car's own telemetry with the trigger marked.
//
// NOTHING HERE OPENS A CAMERA. These are files on disk, played through
// <video> elements that can seek because the server answers Range
// (lib/serve.py). The clock is the incident's: seconds from the trigger, -30 to
// +30, and each camera's file says where on that clock it starts, so three
// cameras whose segments began at different seconds still agree.
//
// SIX CONNECTIONS, again (see views/cameras.js). This view holds no live
// stream, but a playing clip holds its connection for as long as Chromium is
// still reading ahead: three videos, drowsy mode's cabin stream while the car
// rolls and the phone layer's picture are five of the six, leaving one for the
// polls. So the videos are attached only when an incident is opened, are let
// go of when it is closed or the tab is left, and a poll here is one request
// at a time and abandoned after LIVE_TIMEOUT_MS. Do not add a stream.
//
// DELETE IS PARKED-ONLY: greyed, never hidden, with the reason drawn under it
// (store.lockedAsMoving, the same lock as the settings), and refused by the
// server as well (lib/clipsroutes.py). Export copies to ~/Videos/OmaCar.

import { h, clear, icon, store, U, toast, confirmDialog } from "../core.js";
import { ICONS } from "../icons.js";
import { sparkPath, makeSpark } from "../spark.js";
import { postJSON } from "../camapi.js";
import { listClips, getClip, mediaUrl, saveClipNow } from "../clipsapi.js";
import { LIVE_TIMEOUT_MS } from "../drowsyrun.js";
import { ROLES, ROLE_LABEL, BEFORE, AFTER, triggerInfo, whenParts, peakText, speedLine, bytesText,
         serviceState, series, valueAt, stripPath, speedTop, readoutKeys, partAt, span, needsSeek, clampDt,
         dtText } from "../clipslogic.js";

const G = {
  play: ["M6 3 20 12 6 21z"],
  pause: ["M14 4h4v16h-4z", "M6 4h4v16H6z"],
  back: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"],
  fwd: ["M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8", "M21 3v5h-5"],
  bolt: ["M13 2 3 14h9l-1 8 10-12h-9z"],
  save: ["M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z", "M17 21v-8H7v8", "M7 3v5h8"],
  export: ["M12 3v12", "m7 10 5 5 5-5", "M5 21h14"],
  trash: ["M3 6h18", "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"],
};
const conv = (k) => (U.imperial ? k * 0.621371 : k);
const unit = () => (U.imperial ? "mph" : "km/h");
const PW = 900, PH = 160;           // the telemetry strip's drawing box
const NS = "http://www.w3.org/2000/svg";

const READOUT = {
  speed: { k: "Speed", v: (x) => `${Math.round(conv(x))} ${unit()}` },
  rpm: { k: "Engine", v: (x) => `${Math.round(x)} rpm` },
  throttle: { k: "Throttle", v: (x) => `${Math.round(x)}%` },
  soc: { k: "Pack", v: (x) => `${Math.round(x)}%` },
  ima_amps: { k: "IMA", v: (x) => `${x > 0 ? "+" : ""}${Math.round(x)} A` },
};

// `get`, `post`, `every` and `stopEvery` are for the tests; the page uses the defaults.
export default function clipsView(root, { get = listClips, getItem = getClip, post = postJSON,
                                          every = (fn, ms) => setInterval(fn, ms),
                                          stopEvery = (id) => clearInterval(id) } = {}) {
  let alive = true;
  let list = null;                 // the last /api/clips, or null when it could not be reached
  let sig = "";
  let sel = null;                  // the open incident's id
  let item = null;                 // { incident, telemetry } for it
  let dt = -5, playing = false, dragging = false;
  let lastTick = performance.now();

  // ---- the list ----------------------------------------------------------------
  const lamp = h("span.cl-lamp");
  const svcText = h("span.cl-svc-t");
  const count = h("span.cl-count");
  const saveBtn = h("button.cl-save", { type: "button", onclick: onSave }, icon(G.save, 22),
    h("span.cl-save-t", h("span.cl-save-k", "Save clip"), h("span.cl-save-v", "Keep the minute around now")));
  const rows = h("div.cl-rows", { role: "list" });
  const head = h("header.cl-head",
    h("div.cl-title", h("h2", "Incidents"), count),
    h("div.cl-svc", lamp, svcText),
    saveBtn);
  const aside = h("aside.cl-list", head, rows);

  // ---- the players ----------------------------------------------------------------
  const tiles = {};
  for (const role of ROLES) {
    const video = h("video.cl-video", { playsinline: true, muted: true, preload: "auto" });
    video.muted = true;
    const none = h("div.cl-none", "No footage at this moment");
    const sim = h("span.cl-simtag", { hidden: true }, "SIMULATED");
    const t = {
      video, none, file: null,
      node: h("div.cl-vid", { data: { role } }, video, none,
        h("div.cl-cap", h("span.cl-cap-k", ROLE_LABEL[role]), sim)),
      sim,
    };
    video.addEventListener("error", () => {
      if (t.file) { t.none.textContent = "That clip could not be played"; t.node.dataset.none = "1"; }
    });
    tiles[role] = t;
  }
  const vids = h("div.cl-vids", ...ROLES.map((r) => tiles[r].node));

  // ---- the strip ----------------------------------------------------------------------
  const spark = makeSpark(PW, PH);
  const thr = document.createElementNS(NS, "path");
  thr.setAttribute("class", "cl-thr");
  spark.svg.appendChild(thr);
  const grid = [0.25, 0.5, 0.75].map((f) => {
    const l = document.createElementNS(NS, "line");
    l.setAttribute("class", "cl-grid");
    l.setAttribute("x1", "0"); l.setAttribute("x2", String(PW));
    l.setAttribute("y1", String(PH * f)); l.setAttribute("y2", String(PH * f));
    spark.svg.insertBefore(l, spark.svg.firstChild.nextSibling);
    return l;
  });
  void grid;
  const yTop = h("span"), yMid = h("span"), yLow = h("span", "0");
  const flag = h("div.cl-flag", h("span.cl-flag-t"), h("span.cl-flag-v"));
  const head0 = h("div.cl-mark", flag);
  const needle = h("div.cl-needle");
  const plot = h("div.cl-plot", spark.svg, head0, needle);
  const readouts = h("div.cl-reads");
  const axis = h("div.cl-axis", h("span", `−${BEFORE} s`), h("span", "trigger"), h("span", `+${AFTER} s`));
  const strip = h("section.cl-strip",
    h("div.cl-strip-top",
      h("div.cl-strip-k", h("span", "Car telemetry"),
        h("span.cl-legend", h("span.cl-key-line"), "Speed", h("span.cl-key-line.thr"), "Throttle")),
      readouts),
    h("div.cl-chart", h("div.cl-y", yTop, yMid, yLow), plot),
    axis);

  plot.addEventListener("pointerdown", (e) => {
    dragging = true;
    plot.setPointerCapture(e.pointerId);
    seekFromPointer(e);
  });
  plot.addEventListener("pointermove", (e) => { if (dragging) seekFromPointer(e); });
  const endDrag = () => { dragging = false; };
  plot.addEventListener("pointerup", endDrag);
  plot.addEventListener("pointercancel", endDrag);
  function seekFromPointer(e) {
    if (!item) return;
    const r = plot.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    seek(-BEFORE + f * (BEFORE + AFTER));
  }

  // ---- the controls ---------------------------------------------------------------------
  const when = h("span.cl-when", "−5.0 s");
  const scrub = h("input.cl-scrub", { type: "range", min: String(-BEFORE), max: String(AFTER), step: "0.1",
    value: "-5", "aria-label": "Position in the incident" });
  scrub.addEventListener("input", () => seek(Number(scrub.value)));
  const playBtn = h("button.cl-play", { type: "button", "aria-label": "Play", onclick: () => toggle() }, icon(G.play, 28));
  const tbtn = (g, label, fn) => h("button.cl-key", { type: "button", "aria-label": label, title: label, onclick: fn }, icon(g, 24));
  const jumpBtn = h("button.cl-jump", { type: "button", onclick: () => seek(-3) }, icon(G.bolt, 20), h("span", "To the moment"));
  const exportBtn = h("button.cl-act", { type: "button", onclick: onExport }, icon(G.export, 22), h("span", "Export"));
  const delNote = h("span.cl-act-note", "Parked only");
  const delBtn = h("button.cl-act.cl-del", { type: "button", onclick: onDelete }, icon(G.trash, 22), h("span", "Delete"), delNote);
  const bar = h("div.cl-bar",
    h("div.cl-scrubrow", when, scrub),
    h("div.cl-transport", tbtn(G.back, "Back 5 seconds", () => seek(dt - 5)), playBtn,
      tbtn(G.fwd, "Forward 5 seconds", () => seek(dt + 5)), jumpBtn),
    h("div.cl-actions", exportBtn, delBtn));

  const stage = h("section.cl-stage", vids, strip, bar);
  const empty = h("section.cl-empty");
  const page = h("div.clips", aside, stage, empty);
  root.appendChild(page);

  // ---- painting -----------------------------------------------------------------------------
  function paintList() {
    const svc = serviceState(list);
    lamp.dataset.tone = svc.tone;
    svcText.textContent = svc.text;
    const items = (list && list.incidents) || [];
    const tot = list && list.totals;
    count.textContent = list ? (items.length ? `${items.length} · ${bytesText(tot && tot.bytes)}` : "none yet") : "";
    const next = JSON.stringify([items.map((i) => [i.id, i.state, i.peak_g, i.speed_after]), sel, U.imperial, svc.text]);
    if (next === sig) return;
    sig = next;
    clear(rows);
    for (const inc of items) rows.appendChild(rowFor(inc));
    page.dataset.empty = items.length ? "0" : "1";
    paintEmpty(items.length);
  }

  function paintEmpty(n) {
    clear(empty);
    if (n) return;
    const svc = serviceState(list);
    empty.append(...[
      h("div.cl-empty-ico", icon(ICONS.camera, 44)),
      h("h3", list ? "No incidents yet" : "Cannot reach OmaCar's server"),
      h("p", list
        ? "A hard stop, a jolt or a tap on Save clip keeps the minute around it from every camera that is recording, with the car's own numbers."
        : "The incident list comes from the server on this tablet."),
      list && !list.demo ? h("p.cl-empty-svc", h("span.cl-lamp", { data: { tone: svc.tone } }), svc.text) : null,
      list && list.accel && list.accel.present === false && list.accel.why
        ? h("p.cl-empty-sub", list.accel.why) : null,
    ].filter(Boolean));
  }

  function rowFor(inc) {
    const tr = triggerInfo(inc);
    const w = whenParts(inc.t);
    const pk = peakText(inc);
    const sl = speedLine(inc, conv, unit());
    const sp = makeSpark(80, 26);
    const pts = (inc.spark || []).map((v, i, a) => (v === null ? null : [i, v])).filter(Boolean);
    sp.draw(pts.length > 1 ? sparkPath(pts, 80, 26, 0, Math.max(40, Math.max(...pts.map((p) => p[1])))) : "");
    const state = inc.state === "recording" ? "Keeping…" : inc.state === "no-video" ? "No video"
      : inc.state === "partial" ? "Partial" : null;
    const b = h("button.cl-row", { type: "button", role: "listitem", data: { tone: tr.tone, id: inc.id },
      "aria-current": inc.id === sel ? "true" : "false", onclick: () => open(inc.id) },
      h("span.cl-row-lamp"),
      h("span.cl-row-main",
        h("span.cl-row-when", h("span.cl-row-time", w.time), h("span.cl-row-secs", ":" + w.secs)),
        h("span.cl-row-what", tr.word, inc.sim && inc.trigger !== "sim" ? " · Simulated" : "",
          inc.also && inc.also.length ? " + tap" : ""),
        h("span.cl-row-day", [w.day, state].filter(Boolean).join(" · "))),
      h("span.cl-row-spark", sp.svg),
      h("span.cl-row-vals",
        h("span.cl-row-g", pk || h("span.cl-row-g-none", "—")),
        h("span.cl-row-sp", sl || "")));
    return b;
  }

  function paintStage() {
    const inc = item && item.incident;
    stage.hidden = !inc;
    if (!inc) return;
    const tel = item.telemetry;
    const tr = triggerInfo(inc);
    // The strip: speed as the line, throttle behind it as a thin one.
    const top = speedTop(tel);
    const sp = series(tel, "speed");
    spark.draw(stripPath(sp, PW, PH, 0, top));
    thr.setAttribute("d", stripPath(series(tel, "throttle"), PW, PH, 0, 100));
    yTop.textContent = String(Math.round(conv(top)));
    yMid.textContent = String(Math.round(conv(top / 2)));
    yLow.textContent = "0";
    plot.dataset.tone = tr.tone;
    flag.querySelector(".cl-flag-t").textContent = tr.word;
    flag.querySelector(".cl-flag-v").textContent = [peakText(inc), speedLine(inc, conv, unit())].filter(Boolean).join(" · ");
    head0.style.left = (100 * BEFORE) / (BEFORE + AFTER) + "%";
    // The readouts: only what the car reported for this minute.
    clear(readouts);
    for (const key of readoutKeys(tel)) {
      const v = h("span.cl-read-v");
      readouts.appendChild(h("div.cl-read", { data: { key } }, h("span.cl-read-k", READOUT[key].k), v));
    }
    if (!sp.length) readouts.appendChild(h("div.cl-read-none", "No car data was recorded for this one"));
    // The cameras that existed.
    let n = 0;
    for (const role of ROLES) {
      const has = !!(inc.roles && inc.roles[role] && inc.roles[role].parts && inc.roles[role].parts.length);
      tiles[role].node.hidden = !has;
      tiles[role].sim.hidden = !inc.sim;
      if (has) n++;
    }
    vids.dataset.n = String(n);
    vids.hidden = n === 0;
    page.dataset.video = n ? "1" : "0";
    if (!n) vids.dataset.n = "0";
    stage.dataset.novideo = n ? "0" : "1";
    paintLive();
    paintActions();
  }

  // What changes as it plays: the needle, the readouts, the clock, the scrubber.
  function paintLive() {
    if (!item) return;
    const f = (dt + BEFORE) / (BEFORE + AFTER);
    needle.style.left = Math.min(100, Math.max(0, f * 100)) + "%";
    when.textContent = dtText(dt);
    scrub.value = String(dt);
    for (const el of readouts.querySelectorAll(".cl-read")) {
      const key = el.dataset.key;
      const val = valueAt(item.telemetry, key, dt, 1.2);
      el.lastChild.textContent = val === null ? "—" : READOUT[key].v(val);
    }
    clear(playBtn);
    playBtn.appendChild(icon(playing ? G.pause : G.play, 28));
    playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
    playBtn.dataset.on = playing ? "1" : "0";
  }

  function paintActions() {
    const locked = !!store.lockedAsMoving;
    delBtn.disabled = locked || !item;
    delNote.hidden = !locked;
    exportBtn.disabled = !item;
  }

  // ---- the cameras, in step ----------------------------------------------------------------------
  function load(role, p) {
    const t = tiles[role];
    t.none.textContent = "No footage at this moment";
    if (t.file === p.file) return;
    t.file = p.file;
    t.video.src = mediaUrl(item.incident.id, p.file);
    t.video.addEventListener("loadedmetadata", () => {
      const cur = partAt(item.incident, role, dt);
      if (cur && cur.file === t.file) t.video.currentTime = Math.min(cur.at, (t.video.duration || cur.at) - 0.05);
      if (playing) t.video.play().catch(() => {});
    }, { once: true });
  }

  function release() {
    for (const role of ROLES) {
      const t = tiles[role];
      t.video.pause();
      t.video.removeAttribute("src");
      t.video.load();
      t.file = null;
      t.node.dataset.none = "0";
    }
  }

  function seek(to) {
    if (!item) return;
    dt = clampDt(to, span(item.incident));
    for (const role of ROLES) {
      const p = partAt(item.incident, role, dt);
      const t = tiles[role];
      t.node.dataset.none = p ? "0" : "1";
      if (!p) { t.video.pause(); continue; }
      load(role, p);
      if (t.video.readyState > 0 && needsSeek(t.video.currentTime, p.at, 0.05)) t.video.currentTime = p.at;
    }
    paintLive();
  }

  function play() {
    if (!item) return;
    const [, hi] = span(item.incident);
    if (dt >= hi - 0.2) seek(-BEFORE);
    playing = true;
    lastTick = performance.now();
    for (const role of ROLES) {
      const p = partAt(item.incident, role, dt);
      if (p && tiles[role].file === p.file) tiles[role].video.play().catch(() => {});
    }
    paintLive();
  }

  function pause() {
    playing = false;
    for (const role of ROLES) tiles[role].video.pause();
    paintLive();
  }

  function toggle() { if (playing) pause(); else play(); }

  // The follower loop: the first camera with footage here is the clock; the
  // others are moved only when they drift. With no video at all (a service
  // without ffmpeg), the telemetry still plays, on the wall clock.
  function tick() {
    const now = performance.now();
    const el = (now - lastTick) / 1000;
    lastTick = now;
    if (!item || !playing || dragging) { paintActions(); return; }
    const inc = item.incident;
    let master = null;
    for (const role of ROLES) {
      const p = partAt(inc, role, dt);
      const t = tiles[role];
      if (p && t.file === p.file && t.video.readyState >= 2 && !t.video.paused && !t.video.seeking) {
        master = { t, p };
        break;
      }
    }
    if (master) dt = master.p.part.start - inc.t + master.t.video.currentTime;
    else dt += el;
    const [, hi] = span(inc);
    if (dt >= hi - 0.05) { dt = hi - 0.05; pause(); return; }
    for (const role of ROLES) {
      const p = partAt(inc, role, dt);
      const t = tiles[role];
      t.node.dataset.none = p ? "0" : "1";
      if (!p) { if (!t.video.paused) t.video.pause(); continue; }
      if (t.file !== p.file) { load(role, p); continue; }
      if (t.video.readyState > 0) {
        if (needsSeek(t.video.currentTime, p.at, 0.3)) t.video.currentTime = p.at;
        if (t.video.paused && !t.video.seeking) t.video.play().catch(() => {});
      }
    }
    paintLive();
    paintActions();
  }

  // ---- opening and closing an incident ---------------------------------------------------------------
  async function open(id) {
    if (id === sel && item) return;
    pause();
    release();
    sel = id;
    sig = "";
    paintList();
    let got = null;
    try { got = await getItem(id); } catch { /* shown below */ }
    if (!alive || sel !== id) return;
    if (!got) { item = null; paintStage(); toast("That incident could not be opened.", "bad"); return; }
    item = got;
    dt = -5;
    paintStage();
    seek(-5);                      // the first frame, not playing
  }

  async function onSave() {
    saveBtn.disabled = true;
    await saveClipNow();
    setTimeout(() => { saveBtn.disabled = false; }, 1200);
  }

  async function onExport() {
    if (!item) return;
    exportBtn.disabled = true;
    try {
      const r = await post("/api/clips/export", { id: item.incident.id });
      toast(`Copied to ${r.display || r.path}`, "ok", 6000);
    } catch (e) { toast("Could not export: " + e.message, "bad"); }
    exportBtn.disabled = !item;
  }

  async function onDelete() {
    if (!item || store.lockedAsMoving) return;
    const inc = item.incident;
    const tr = triggerInfo(inc);
    const w = whenParts(inc.t);
    const ok = await confirmDialog({
      title: "Delete this incident?",
      body: `${tr.word}, ${w.day.toLowerCase()} at ${w.time}. The video from every camera and its telemetry will be removed. `
        + "Export it first if you want to keep a copy.",
      confirm: "Delete",
    });
    if (!ok || !alive) return;
    try {
      await post("/api/clips/delete", { id: inc.id });
    } catch (e) { toast("Could not delete: " + e.message, "bad"); return; }
    pause();
    release();
    item = null;
    sel = null;
    sig = "";
    await refresh();
    const first = list && list.incidents && list.incidents[0];
    if (first) open(first.id); else paintStage();
  }

  // ---- the poll: one at a time, and given up ---------------------------------------------------------------
  const out = new Set();
  let pending = null;
  async function refresh() {
    if (pending) return pending;
    pending = (async () => {
      const ctl = new AbortController();
      out.add(ctl);
      const bail = setTimeout(() => ctl.abort(), LIVE_TIMEOUT_MS);
      try { list = await get({ signal: ctl.signal }); } catch { list = null; }
      finally { clearTimeout(bail); out.delete(ctl); pending = null; }
      if (!alive) return;
      paintList();
      if (!sel && list && list.incidents && list.incidents.length) open(list.incidents[0].id);
      // An incident still being kept finishes in the background: reload it
      // once it has, so its video appears without leaving the screen.
      const cur = list && list.incidents && list.incidents.find((i) => i.id === sel);
      if (cur && item && item.incident.state !== cur.state && !playing) {
        const keep = dt;
        const id = sel;
        let got = null;
        try { got = await getItem(id); } catch { /* keep what is open */ }
        if (got && alive && sel === id) { release(); item = got; paintStage(); seek(keep); }
      }
    })();
    return pending;
  }

  paintList();
  paintStage();
  refresh();
  const timers = [every(refresh, 4000), every(tick, 100)];

  return () => {
    alive = false;
    for (const t of timers) stopEvery(t);
    for (const ctl of out) ctl.abort();
    playing = false;
    release();
  };
}
