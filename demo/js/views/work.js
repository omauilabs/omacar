// WORK, AS THE MEETUP DEMO SHOWS IT (mockup 8; Task 6 of
// doc/design/2026-09-30-meetup-demo-plan.md).
//
// Your coding sessions on your Omarchy machines, from the car: which is
// running, which is ready for review, which needs you. A CONCEPT, and labelled
// one: the sessions are demo/data/work.json, their steps tick along on a timer
// (one every 20-40 s, looping), and nothing here reaches an agent.
//
// Parked, it is the full screen: the accounts, the sessions, one session's
// checklist, a voice chat and the review waiting for you. Moving (SPEED over
// 3 km/h, the app's own threshold) it is voice first: how many are running,
// one spoken instruction, and "Code review available when parked."

import { h, icon, store, toast } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";
import { say as coreSay, LINES, linesReady } from "../voice.js";
import { pace, stream, sleep, between, clock12, waveform, meMsg, botMsg, GLYPH } from "./agent.js";

const CAR_PIC = "/demo-media/crz-home.png";
export const GAP_MS = [20000, 40000];

const ICON = { car: ICONS.drive, doc: ICONS.report, book: ICONS.learn, phone: ICONS.phone };
const OPEN = ["M15 3h6v6", "M10 14 21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"];
const BRANCH = ["M6 3v12", "M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
                "M18 9a9 9 0 0 1-9 9"];
const STATUS = {
  running: { label: "Running", tone: "ok" },
  review: { label: "Review", tone: "info" },
  input: { label: "Needs input", tone: "warn" },
  paused: { label: "Paused", tone: "idle" },
};

// ONE STEP OF A SESSION'S CHECKLIST, IF ITS TIME HAS COME. The first call only
// schedules; after that a step finishes every 20-40 s, and a finished list
// starts again. Says whether anything changed.
export function tick(s, now, rnd = Math.random) {
  const gap = () => GAP_MS[0] + Math.round(rnd() * (GAP_MS[1] - GAP_MS[0]));
  if (!s.next) { s.next = now + gap(); return false; }
  if (now < s.next) return false;
  if (s.done >= s.steps.length) { s.done = 0; s.at = []; }
  else { s.done += 1; s.at = (s.at || []).concat(now); }
  s.next = now + gap();
  return true;
}

// The clock the steps tick on, so a test can hold it.
export const workClock = {
  now: () => Date.now(),
  every: (fn, ms) => setInterval(fn, ms),
  stop: (id) => clearInterval(id),
};

// The sessions, once per page: leaving the screen and coming back finds them
// where they were. `initial` is work.json's own copy, for resetWork().
let world = null, loading = null, initial = null;

// A session as work.json starts it: its finished steps spread over the half
// hour before now, and its clock not yet scheduled.
function fresh(s, now) {
  return Object.assign(JSON.parse(JSON.stringify(s)), {
    at: Array.from({ length: s.done }, (_, i) => now - (s.done - i) * 6 * 60000),
    since: now - 2 * 60000, next: 0, paused: false,
  });
}

export function loadWork() {
  if (world) return Promise.resolve(world);
  if (!loading) {
    loading = fetch(new URL("../../data/work.json", import.meta.url), { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`work.json: ${r.status}`); return r.json(); })
      .then((doc) => {
        const now = workClock.now();
        initial = JSON.parse(JSON.stringify(doc.sessions));
        world = Object.assign({}, doc, { sessions: initial.map((s) => fresh(s, now)) });
        return world;
      })
      .catch((e) => { loading = null; throw e; });
  }
  return loading;
}

// EVERY SESSION BACK AS work.json STARTS IT, for the demo's restart (Task 8
// calls this beside the agent's restoreHome()). Without it the second tour
// opens on OmaSaber already running, 3/1/0, while "work-update" still says it
// needs your input. In place, so a screen that is up keeps its references,
// and that screen goes back to its first state too.
export function resetWork() {
  if (!world || !initial) return false;
  const now = workClock.now();
  world.sessions.forEach((s, i) => {
    for (const k of Object.keys(s)) delete s[k];
    Object.assign(s, fresh(initial[i], now));
  });
  if (active) active.reset();
  return true;
}

const statusOf = (s) => STATUS[s.paused ? "paused" : s.status] || STATUS.running;

function counts(w) {
  const live = w.sessions.filter((s) => !s.paused);
  return [live.filter((s) => s.status === "running").length,
          live.filter((s) => s.status === "review").length,
          live.filter((s) => s.status === "input").length];
}

// A usage ring around a neutral glyph for the kind of account: a rounded "C"
// for Claude, a prompt for Codex, both in the app's own colours. Never either
// company's mark or its colour, the same rule as CarPlay and Android Auto.
function ring(a) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 48 48");
  svg.setAttribute("class", "wk-ring");
  svg.setAttribute("aria-hidden", "true");
  const used = Math.round(Math.max(0, Math.min(1, a.usage || 0)) * 100);
  const glyph = a.kind === "codex"
    ? `<path class="g codex" d="M17 18l6 6-6 6M25 30h7"/>`
    : `<path class="g mono" d="M29.5 18.6a7.8 7.8 0 1 0 0 10.8"/>`;
  svg.innerHTML = `<circle class="track" cx="24" cy="24" r="21"/>
    <circle class="used" cx="24" cy="24" r="21" pathLength="100" stroke-dasharray="${used} 100"
            transform="rotate(-90 24 24)"/>${glyph}`;
  return svg;
}

let active = null;
export const workActions = {
  // "Give me an update": spoken, and its words shown, parked or driving.
  update: () => (active ? active.update() : Promise.resolve(false)),
  send: () => (active ? active.send() : Promise.resolve(false)),
};

export function workView(deps = {}) {
  const io = { say: deps.say || coreSay };

  return function mount(root) {
    let alive = true, driving = null, selected = "omacar", busy = null, overlay = null;
    const cleanups = [];
    const view = h("div.wk");
    root.appendChild(view);

    const stateEl = h("span.wk-state");
    const head = h("div.wk-head", stateEl, h("span.wk-concept", "CONCEPT · DEMO SESSIONS"));
    const body = h("div.wk-body");
    view.append(head, body);

    let park = null, drive = null;
    function start(w) {
      if (!alive) return;
      park = parkedView(w);
      drive = drivingView(w);
      paint();
      const timer = workClock.every(() => {
        const now = workClock.now();
        let moved = false;
        for (const s of w.sessions) if (!s.paused && tick(s, now)) moved = true;
        if (moved) { park.paint(); drive.paint(); }
      }, 1000);
      cleanups.push(() => workClock.stop(timer));
    }
    // At once when the sessions are in hand, so coming back to the screen
    // never draws an empty frame first.
    if (world) start(world);
    else loadWork().then(start).catch((e) => {
      body.replaceChildren(h("div.card", h("div.title", "The demo sessions did not load"),
        h("p.muted", String((e && e.message) || e))));
    });

    function paint() {
      const now = (store.values.SPEED || 0) > 3;
      stateEl.textContent = now ? "DRIVING" : "PARKED";
      stateEl.dataset.driving = now ? "1" : "0";
      if (!park || now === driving) return;
      driving = now;
      body.replaceChildren(now ? drive.node : park.node);
      (now ? drive : park).paint();
    }
    cleanups.push(store.on("live", paint), store.on("car", paint));
    paint();

    // ---------------------------------------------------------------- parked
    function parkedView(w) {
      const sel = () => w.sessions.find((s) => s.id === selected) || w.sessions[0];
      const account = (id) => w.accounts.find((a) => a.id === id) || { name: "" };

      const accts = w.accounts.map((a) => {
        const dot = h("span.wk-dot");
        const el = h("button.wk-acct", { type: "button", title: `${Math.round((a.usage || 0) * 100)}% of this week's use`,
                                          onclick: () => { const s = w.sessions.find((x) => x.account === a.id); if (s) select(s.id); } },
          ring(a), h("span.wk-acct-t", h("b", a.name), h("small", a.plan)), dot);
        return { a, el, dot };
      });

      const list = h("div.wk-rows");
      const listCard = h("section.wk-list",
        h("div.wk-list-h", h("span", "Your sessions"),
          h("button.wk-btn.sm", { type: "button", onclick: () => toast("Demo sessions only") },
            icon(ICONS.plus, 16), h("span", "New session"))),
        list);

      const dIcon = h("span.wk-d-i"), dName = h("b"), dBranch = h("span");
      const steps = h("div.wk-steps");
      const log = h("div.wk-log",
        h("div.wk-hint", "Tap Speak and ask: “Give me an update.”"));
      const wave = waveform(23, "wk-wave");
      const heard = h("span.wk-heard");
      const pauseLabel = h("span", "Pause session");
      const pauseIcon = h("span.wk-pi", icon(GLYPH.pause, 18));
      const reviewing = w.sessions.find((s) => s.review);
      const detail = h("section.wk-detail",
        h("div.wk-d-h", dIcon, h("div", dName, h("small.wk-branch", icon(BRANCH, 14), dBranch))),
        steps,
        h("div.wk-voice", log, h("div.wk-wave-row", wave.node, heard),
          h("div.wk-acts",
            h("button.wk-btn.primary", { type: "button", onclick: () => update() }, icon(GLYPH.mic, 18), h("span", "Speak")),
            h("button.wk-btn", { type: "button", onclick: () => togglePause() }, pauseIcon, pauseLabel),
            h("button.wk-btn", { type: "button", onclick: () => openReview() }, icon(OPEN, 18), h("span", "Open review")))),
        reviewing ? h("div.wk-review",
          h("span.wk-sess-i", icon(ICONS.report, 22)),
          h("div.wk-review-t", h("b", "Ready for review"), h("span", reviewing.name),
            h("small", `${reviewing.review.files.length} files changed`)),
          h("button.wk-btn.sm", { type: "button", onclick: () => openReview() },
            h("span", "Review changes"), icon(ICONS.chevron, 16))) : null);

      const node = h("div.wk-park",
        h("div.wk-accts", accts.map((x) => x.el)),
        h("div.wk-cols", listCard, detail));

      function stepRow(kind, words, when) {
        const mark = kind === "done" ? icon(ICONS.check, 18)
          : kind === "run" ? h("span.wk-spin") : kind === "paused" ? icon(GLYPH.pause, 16) : h("span", "···");
        return h("div.wk-step", { data: { kind } }, h("span.wk-st", mark), h("span.wk-sw", words), h("small", when));
      }

      function paintSteps(s) {
        const rows = [];
        const n = s.steps.length, done = Math.min(s.done, n);
        for (let i = Math.max(0, done - 2); i < done; i++) {
          rows.push(stepRow("done", s.steps[i], s.at[i] ? clock12(new Date(s.at[i])) : ""));
        }
        if (done < n) {
          const began = s.at[done - 1] || s.since;
          rows.push(stepRow(s.paused ? "paused" : "run", s.steps[done], s.paused ? "Paused" : clock12(new Date(began))));
          rows.push(stepRow("more", n - done - 1 ? `${n - done - 1} more` : "Last step", s.paused ? "Paused" : "In progress"));
        } else {
          rows.push(stepRow("more", "All steps done", ""));
        }
        steps.replaceChildren(...rows);
      }

      function paint() {
        const s = sel();
        for (const x of accts) {
          const on = w.sessions.find((y) => y.account === x.a.id);
          x.dot.dataset.tone = on ? statusOf(on).tone : "idle";
          x.el.classList.toggle("on", !!on && on.id === s.id);
        }
        list.replaceChildren(...w.sessions.map((t) => {
          const st = statusOf(t);
          return h("button.wk-sess" + (t.id === s.id ? ".on" : ""), { type: "button", onclick: () => select(t.id) },
            h("span.wk-sess-i", icon(ICON[t.icon] || ICONS.drive, 22)),
            h("span.wk-sess-n", h("b", t.name), h("small", account(t.account).name)),
            h("span.wk-sess-s", t.summary),
            h("span.wk-pill", { data: { tone: st.tone } }, h("i"), st.label),
            h("span.wk-chev", icon(ICONS.chevron, 18)));
        }));
        dIcon.replaceChildren(icon(ICON[s.icon] || ICONS.drive, 22));
        dName.replaceChildren(h("span", s.name), h("span.wk-dot-sep", " · "), h("span", s.title));
        dBranch.textContent = s.branch;
        paintSteps(s);
        pauseLabel.textContent = s.paused ? "Resume session" : "Pause session";
        pauseIcon.replaceChildren(icon(s.paused ? GLYPH.play : GLYPH.pause, 18));
      }

      function togglePause() {
        const s = sel();
        s.paused = !s.paused;
        if (!s.paused) s.next = 0;
        paint();
        toast(s.paused ? `${s.name} paused` : `${s.name} resumed`);
      }

      async function askUpdate() {
        const hint = log.querySelector(".wk-hint");
        if (hint) hint.remove();
        wave.set("listen");
        heard.textContent = "Listening…";
        await sleep(pace.listen);
        if (!alive) return;
        heard.textContent = "";
        log.appendChild(meMsg(w.update.ask));
        const b = botMsg();
        log.appendChild(b.node);
        while (log.children.length > 4) log.firstChild.remove();
        log.scrollTop = log.scrollHeight;
        await sleep(between(pace.think));
        if (!alive) return;
        b.dots.remove();
        await linesReady;
        const spoken = Promise.resolve(io.say(w.update.voice)).catch(() => {});
        wave.set("speak");
        await stream(LINES[w.update.voice] || "", (t) => { b.text.textContent = t; log.scrollTop = log.scrollHeight; },
                     { alive: () => alive });
        b.time.textContent = clock12();
        await spoken;
        wave.set("idle");
      }

      function reset() {
        log.replaceChildren(h("div.wk-hint", "Tap Speak and ask: “Give me an update.”"));
        heard.textContent = "";
        wave.set("idle");
        paint();
      }

      return { node, paint, askUpdate, reset };
    }

    // ---------------------------------------------------------------- driving
    function drivingView(w) {
      const d = w.driving;
      const target = w.sessions.find((s) => s.id === d.target);
      let mode = "draft";

      const pic = h("img.wk-car", { alt: "", hidden: true, draggable: "false" });
      pic.onload = () => { pic.hidden = false; };
      pic.src = CAR_PIC;
      const nums = [h("b"), h("b"), h("b")];
      const tally = h("div.wk-counts",
        ["running", "ready", "needs you"].map((label, i) =>
          h("div.wk-count", { data: { tone: ["ok", "info", "warn"][i] } }, nums[i], h("span", label))));
      const wave = waveform(29, "wk-dwave");
      const status = h("div.wk-listen");
      const said = h("div.wk-said");
      const card = h("div.wk-target",
        h("span.wk-sess-i", icon(ICON[target.icon] || ICONS.learn, 22)),
        h("div", h("b", target.name, h("small", ` · ${(w.accounts.find((a) => a.id === target.account) || {}).name || ""}`)),
          h("span", d.task)),
        h("span.wk-dot", { data: { tone: "warn" } }));
      const sendBtn = h("button.wk-btn.primary.lg", { type: "button", onclick: () => send() },
        icon(GLYPH.send, 20), h("span", "Send instruction"));
      const cancelBtn = h("button.wk-btn.lg", { type: "button", onclick: () => cancel() },
        icon(ICONS.x, 20), h("span", "Cancel"));
      const acts = h("div.wk-dacts", sendBtn, cancelBtn);
      const again = h("button.wk-btn.lg.wk-again", { type: "button", onclick: () => relisten() },
        icon(GLYPH.mic, 20), h("span", "Speak an instruction"));
      const read = h("button.wk-read", { type: "button", onclick: () => update() },
        icon(GLYPH.mic, 20), h("span", "Read my updates"), icon(ICONS.chevron, 18));

      const node = h("div.wk-drive",
        h("div.wk-dl", h("div.wk-carbox", pic), h("h2.wk-dh", "Your agents are working"), tally),
        h("div.wk-dr", wave.node, status, said, card, acts, again,
          h("div.wk-dnote", icon(GLYPH.info, 15), h("span", "Code review available when parked.")),
          read));

      function paint() {
        counts(w).forEach((n, i) => { nums[i].textContent = String(n); });
        const draft = mode === "draft";
        card.hidden = !(draft || mode === "sent");
        card.dataset.sent = mode === "sent" ? "1" : "0";
        card.querySelector(".wk-dot").dataset.tone = mode === "sent" ? "ok" : "warn";
        acts.hidden = !draft;
        again.hidden = !(mode === "sent" || mode === "idle" || mode === "read");
        wave.set(mode === "draft" || mode === "listening" ? "listen" : mode === "reading" ? "speak" : "idle");
        if (draft) { status.textContent = "Listening…"; said.textContent = `“${d.instruction}”`; }
        else if (mode === "listening") { status.textContent = "Listening…"; said.textContent = ""; }
        else if (mode === "sent") { status.textContent = `Sent to ${target.name}`; said.textContent = `“${d.instruction}”`; }
        else if (mode === "idle") { status.textContent = "Tap to speak an instruction"; said.textContent = ""; }
        else if (mode === "reading" || mode === "read") { status.textContent = "Your updates"; }
      }

      async function send() {
        if (mode !== "draft") return false;
        target.status = "running";
        target.summary = d.after;
        target.paused = false;
        mode = "sent";
        paint();
        if (park) park.paint();
        await Promise.resolve(io.say(d.voice)).catch(() => {});
        return true;
      }
      function cancel() { mode = "idle"; paint(); }
      async function relisten() {
        mode = "listening";
        paint();
        await sleep(pace.listen);
        if (!alive || mode !== "listening") return;
        mode = "draft";
        paint();
      }
      async function readUpdates() {
        mode = "reading";
        said.textContent = "";
        paint();
        await linesReady;
        const spoken = Promise.resolve(io.say(w.update.voice)).catch(() => {});
        await stream(LINES[w.update.voice] || "", (t) => { said.textContent = t; }, { alive: () => alive });
        await spoken;
        if (!alive) return;
        mode = "read";
        paint();
      }

      function reset() { mode = "draft"; said.textContent = ""; paint(); }

      paint();
      return { node, paint, send, readUpdates, reset };
    }

    // ---------------------------------------------------------------- review
    function openReview() {
      const w = world;
      const s = w && w.sessions.find((x) => x.review);
      if (!s) return;
      closeReview();
      const close = () => closeReview();
      overlay = h("div.wk-overlay", { "data-demo-overlay": "", role: "dialog", "aria-modal": "true", "aria-label": `${s.name}, ready for review`,
                                      onclick: (e) => { if (e.target === overlay) close(); } },
        h("div.wk-sheet",
          h("div.wk-sheet-h",
            h("div", h("b", `${s.name} · Ready for review`),
              h("small.wk-branch", icon(BRANCH, 14), h("span", `${s.branch} · ${s.review.files.length} files changed · ${s.review.tests}`))),
            h("button.wk-btn", { type: "button", onclick: close }, icon(ICONS.x, 18), h("span", "Close"))),
          h("div.wk-files", s.review.files.map(([f, add, del]) =>
            h("div.wk-file", h("span.wk-fn", f), h("span.wk-add", `+${add}`), h("span.wk-del", `−${del}`)))),
          h("div.wk-dnote", icon(GLYPH.info, 15), h("span", "A concept: approving happens at your desk."))));
      document.body.appendChild(overlay);
    }
    function closeReview() { if (overlay) { overlay.remove(); overlay = null; } }
    const onKey = (e) => { if (e.key === "Escape" && overlay) { e.preventDefault(); closeReview(); } };
    document.addEventListener("keydown", onKey);

    function select(id) { selected = id; if (park) park.paint(); }

    async function update() {
      if (busy) return busy;
      await loadWork();
      if (!alive || !park) return false;
      busy = (driving ? drive.readUpdates() : park.askUpdate()).finally(() => { busy = null; });
      return busy;
    }

    const ctl = {
      update,
      send: () => (drive && driving ? drive.send() : Promise.resolve(false)),
      reset() {
        selected = "omacar";
        closeReview();
        if (park) park.reset();
        if (drive) drive.reset();
      },
    };
    active = ctl;

    return () => {
      alive = false;
      if (active === ctl) active = null;
      closeReview();
      document.removeEventListener("keydown", onKey);
      for (const c of cleanups) { try { c(); } catch { /* gone */ } }
    };
  };
}

export default workView;

// Task 8's boot.js calls this with deps = { radio, mountMap, back }. Fast: the
// screen changes with the car's speed.
export function register(D, deps = {}) {
  D.views.work = { mount: workView(deps), fast: true };
}
