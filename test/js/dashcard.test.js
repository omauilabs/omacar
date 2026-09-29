// Home's Dashcams card (share/js/dashcard.js): the front camera's picture, or
// in words why there is none, the drowsy chip, and the AUX line.
//
// The card is mounted on a FAKE ENGINE, a scripted poll and hand-fired timers,
// so nothing here polls a server or waits for a clock. Its picture is an <img>
// whose stream the runner's static server answers with a 404; no test looks at
// what the browser does with that. The last two tests mount Home itself.
import { eq, ok } from "./assert.js";
import { dashcamCard } from "../js/dashcard.js";
import { mountDrowsyUI, TONE } from "../js/drowsyui.js";
import { LIVE_TIMEOUT_MS } from "../js/drowsyrun.js";
import { audio, applyAudio } from "../js/audiostate.js";
import { liveUrl } from "../js/camapi.js";
import home from "../js/views/home.js";

const role = (o) => Object.assign({ device: "/dev/v4l/by-id/x", mode: { fmt: "MJPG", w: 1920, h: 1080, fps: 30 },
  sim: false, recording: true, live: true, fps: 29.9, error: null }, o);
const ov = (roles, running = true) => ({ running, storage: { used: 12.34e9, budget: 40e9 },
  roles: Object.assign({ front: role({}), rear: role({}), cabin: role({ mode: { fmt: "MJPG", w: 640, h: 480, fps: 30 } }) }, roles) });

const UNPLUGGED = "AUX disconnected — sound is on the tablet's speakers";
const STOPPED = { connected: true, kph: 0, moving: false, active: false };
// The six chip texts, each in a state the engine could be in when it says it.
const SIX = [
  ["Watching", { gate: { active: true } }],
  ["Can't see you", { gate: { active: true } }],
  ["Paused · stopped", { gate: STOPPED }],
  ["Paused · no car data", { gate: null }],
  ["Stopped · face tracker error", { gate: STOPPED, error: "The face tracker stopped after 3 failed frames" }],
  ["Off", { gate: null, cfg: { enabled: false } }],
];

// A stand-in for drowsyrun.js's engine: its state, `on`, and a count of who is listening.
function fake(patch = {}) {
  const fns = new Set();
  const e = {
    state: { chip: "Off", level: 0, trigger: null, banner: false, gate: null,
             cfg: { enabled: true, min_speed_mph: 30 }, error: null, aux: "", testing: false, testLevel: 0, ...patch },
    listeners: () => fns.size,
    on(fn) { fns.add(fn); fn(e.state); return () => fns.delete(fn); },
    set(p) { Object.assign(e.state, p); for (const fn of [...fns]) fn(e.state); },
  };
  return e;
}

// Timers the test fires by hand: ticks[0] is the card's poll; laters are the
// abort timers it sets (negative ids), each with the delay it asked for.
function timers() {
  const t = { ticks: [], laters: [], cancelled: [] };
  t.every = (fn) => { t.ticks.push(fn); return t.ticks.length; };
  t.later = (fn, ms) => { t.laters.push({ fn, ms }); return -t.laters.length; };
  t.cancel = (id) => t.cancelled.push(id);
  return t;
}

const settle = () => new Promise((r) => setTimeout(r, 0));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// The card on a scripted poll. `m.next` is what the poll answers: an overview,
// "down" (the request fails) or "hang" (it never answers, until it is aborted).
async function mount({ first = ov({}), engine = fake() } = {}) {
  const node = document.createElement("div");
  node.className = "card hc hc-cam";
  document.body.appendChild(node);
  const t = timers();
  const m = { node, engine, t, asked: [], next: first };
  const poll = (path, opts) => {
    m.asked.push({ path, opts });
    if (m.next === "hang") return new Promise((_, no) => opts.signal.addEventListener("abort", () => no(new Error("aborted"))));
    return m.next === "down" ? Promise.reject(new Error("503")) : Promise.resolve(m.next);
  };
  m.card = dashcamCard(node, { engine, poll, every: t.every, later: t.later, cancel: t.cancel });
  m.tick = async (next) => { if (next !== undefined) m.next = next; t.ticks[0](); await settle(); };
  m.done = () => { m.card.destroy(); node.remove(); };
  await settle();
  return m;
}

const q = (node, sel) => node.querySelector(sel);
// What is on the card: the picture (and where it comes from), the reason, REC and SIMULATED.
function seen(node) {
  const img = q(node, ".dc-img"), why = q(node, ".dc-why");
  return { img: !img.hidden, src: img.getAttribute("src"), why: why.hidden ? null : why.textContent,
           rec: !q(node, ".dc-rec").hidden, sim: !q(node, ".dc-sim").hidden };
}

// audiostate.js's applyAudio() posts /api/audio through the page's fetch; here
// fetch answers with `body` and nothing reaches a server.
async function audioNow(body) {
  const real = window.fetch;
  window.fetch = async () => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  try { await applyAudio(); } finally { window.fetch = real; }
}

async function until(fn, what) {
  for (let i = 0; i < 400 && !fn(); i++) await wait(5);
  if (!fn()) throw new Error("timed out waiting for " + what);
}

// Home, mounted for real: the runner's static server answers 404 to every /api/
// route, so Home falls back to the catalogue's default layout.
async function withHome(fn) {
  for (const id of ["toasts", "modal-host"]) {
    if (!document.getElementById(id)) { const d = document.createElement("div"); d.id = id; document.body.appendChild(d); }
  }
  const root = document.createElement("div");
  document.body.appendChild(root);
  const unmount = home(root);
  try {
    await until(() => root.querySelector('[data-card="dashcam"] .dc-stage'), "the Dashcams card to be built");
    await fn(root);
  } finally { unmount(); root.remove(); }
}

export default [
  // ---- the picture, or in words why there is none
  ["the card shows the front camera's picture with REC, and no reason", async () => {
    const m = await mount();
    try { eq(seen(m.node), { img: true, src: liveUrl("front"), why: null, rec: true, sim: false }); } finally { m.done(); }
  }],
  ["a test picture says SIMULATED beside REC", async () => {
    const m = await mount({ first: ov({ front: role({ sim: true }) }) });
    try { eq(seen(m.node), { img: true, src: liveUrl("front"), why: null, rec: true, sim: true }); } finally { m.done(); }
  }],
  ["with no front camera it says so in words, with no picture and no REC", async () => {
    const m = await mount({ first: ov({ front: role({ device: null, recording: false, live: false, error: "no camera" }) }) });
    try { eq(seen(m.node), { img: false, src: null, why: "No front camera", rec: false, sim: false }); } finally { m.done(); }
  }],
  ["with the recorder off it says so", async () => {
    const m = await mount({ first: ov({}, false) });
    try { eq(seen(m.node), { img: false, src: null, why: "Recorder off", rec: false, sim: false }); } finally { m.done(); }
  }],
  ["with no server it says that, not a blank", async () => {
    const m = await mount({ first: "down" });
    try { eq(seen(m.node), { img: false, src: null, why: "OmaCar cannot reach its server", rec: false, sim: false }); } finally { m.done(); }
  }],
  ["a camera that has not sent a frame yet is REC and says it is waiting, over no picture", async () => {
    const m = await mount({ first: ov({ front: role({ live: false }) }) });
    try { eq(seen(m.node), { img: false, src: null, why: "Waiting for the picture", rec: true, sim: false }); } finally { m.done(); }
  }],
  ["the picture goes when the recorder stops, and comes back when it does", async () => {
    const m = await mount();
    try {
      const first = seen(m.node);
      await m.tick(ov({}, false));
      const stopped = seen(m.node);
      await m.tick(ov({}));
      eq([first.src, stopped, seen(m.node).src], [liveUrl("front"), { img: false, src: null, why: "Recorder off", rec: false, sim: false }, liveUrl("front")]);
    } finally { m.done(); }
  }],
  ["a picture that failed to load is asked for again on the next poll, and dropped if the recorder is gone", async () => {
    const m = await mount();
    try {
      const img = q(m.node, ".dc-img");
      // The stream failed. The address is taken off so that asking again is visible.
      img.removeAttribute("src");
      img.dispatchEvent(new Event("error"));
      await m.tick();
      const again = img.getAttribute("src");
      img.dispatchEvent(new Event("error"));
      await m.tick(ov({}, false));
      eq([again, img.getAttribute("src"), img.hidden], [liveUrl("front"), null, true]);
    } finally { m.done(); }
  }],
  ["the caption row carries the title, SIMULATED, REC and the drowsy chip; the AUX line is below it", async () => {
    const m = await mount();
    try {
      eq([m.card.top.className, [...m.card.top.children].map((c) => c.className),
          q(m.node, ".dc-aux").parentNode === m.node, q(m.node, ".dc-title").textContent],
         ["dc-top", ["dc-title", "dc-sim", "dc-rec", "dc-drowsy"], true, "Dashcams"]);
    } finally { m.done(); }
  }],

  // ---- polling: one at a time, and given up
  ["it polls the recorder's overview, on a 3 s tick", async () => {
    const m = await mount();
    try { eq([m.asked.map((a) => a.path), m.t.ticks.length], [["/api/cams"], 1]); } finally { m.done(); }
  }],
  ["a poll that hangs is not stacked on, and is given up after the drowsy poll's five seconds", async () => {
    const m = await mount({ first: "hang" });
    try {
      await m.tick(); await m.tick(); await m.tick();
      const stacked = m.asked.length;
      eq([m.t.laters.map((l) => l.ms), LIVE_TIMEOUT_MS], [[LIVE_TIMEOUT_MS], 5000]);
      m.t.laters[0].fn();                               // five seconds pass
      await settle();
      const said = seen(m.node).why;
      m.next = ov({});
      await m.tick();
      eq([stacked, said, m.asked.length, seen(m.node).src], [1, "OmaCar cannot reach its server", 2, liveUrl("front")]);
    } finally { m.done(); }
  }],
  ["each poll's abort timer is cleared once it has answered", async () => {
    const m = await mount();
    try { eq(m.t.cancelled.includes(-1), true); } finally { m.done(); }
  }],

  // ---- the drowsy chip: the top bar's, never a second opinion
  ["the chip's text, tone and title are the top bar chip's, for each of the six texts, with the cable in or out", () => {
    const e = fake();
    const app = document.createElement("div"), bar = document.createElement("header"), right = document.createElement("div");
    right.className = "tb-right";
    bar.appendChild(right);
    app.appendChild(bar);
    document.body.appendChild(app);
    const node = document.createElement("div");
    document.body.appendChild(node);
    const ui = mountDrowsyUI({ engine: e, app, bar, dz: null });
    const card = dashcamCard(node, { engine: e, poll: () => new Promise(() => {}), every: () => 0, later: () => 0, cancel: () => {} });
    try {
      const dz = q(node, ".dc-drowsy"), top = ui.chip;
      for (const aux of ["", UNPLUGGED]) for (const [chip, patch] of SIX) {
        e.set({ chip, gate: null, cfg: { enabled: true, min_speed_mph: 30 }, error: null, aux, ...patch });
        eq([dz.textContent, dz.dataset.tone, dz.title], [top.textContent, top.dataset.tone, top.title], `${chip} (aux "${aux}")`);
        eq([dz.textContent, dz.dataset.tone], [chip, TONE[chip]], `${chip} against TONE`);
      }
      eq(Object.keys(TONE), SIX.map(([c]) => c), "the six texts, in TONE");
    } finally { card.destroy(); ui.off(); app.remove(); node.remove(); }
  }],
  ["the chip's title says what the chip means, and that AUX is out, the same sentence the top bar's says", async () => {
    const e = fake({ chip: "Watching", gate: { active: true }, aux: "" });
    const m = await mount({ engine: e });
    try {
      const dz = q(m.node, ".dc-drowsy");
      const plain = dz.title;
      e.set({ aux: UNPLUGGED });
      eq([plain, dz.title], ["Drowsy mode: Watching", `Drowsy mode: Watching. ${UNPLUGGED}.`]);
    } finally { m.done(); }
  }],
  ["the chip follows the engine as it changes, and the tone follows the text", async () => {
    const e = fake({ chip: "Watching", gate: { active: true } });
    const m = await mount({ engine: e });
    try {
      const dz = q(m.node, ".dc-drowsy");
      const a = [dz.textContent, dz.dataset.tone];
      e.set({ chip: "Stopped · face tracker error", error: "The face tracker stopped" });
      const b = [dz.textContent, dz.dataset.tone];
      e.set({ chip: "Off", error: null });
      eq([a, b, [dz.textContent, dz.dataset.tone]], [["Watching", "ok"], ["Stopped · face tracker error", "warn"], ["Off", ""]]);
    } finally { m.done(); }
  }],
  ["the card keeps no chip table of its own: it imports the top bar's tone and title", async () => {
    const src = (await (await fetch("../js/dashcard.js", { cache: "no-store" })).text()).replace(/^\s*\/\/.*$/gm, "");
    ok(/import \{[^}]*\bchipTone\b[^}]*\} from "\.\/drowsyui\.js"/.test(src), "dashcard.js must import chipTone from drowsyui.js");
    ok(/import \{[^}]*\bchipHint\b[^}]*\} from "\.\/drowsyui\.js"/.test(src), "dashcard.js must import chipHint from drowsyui.js");
    ok(!/Watching|Can't see you|Paused|Stopped ·/.test(src), "dashcard.js must not spell out a chip text of its own");
  }],

  // ---- the AUX line
  ["the AUX line shows only while the cable is known to be out, and hides for in or unknown", async () => {
    audio.last = null;
    const m = await mount();
    try {
      const a = q(m.node, ".dc-aux");
      const at = () => [a.hidden, a.textContent];
      const unknown = at();
      await audioNow({ aux: false, volume: 1 });
      const out = at();
      await audioNow({ aux: true, volume: 1 });
      const cable = at();
      await audioNow({ aux: false, volume: 1 });
      await audioNow({ aux: null });
      eq([unknown, out, cable, at()], [[true, ""], [false, UNPLUGGED], [true, ""], [true, ""]]);
    } finally { m.done(); audio.last = null; }
  }],
  ["a card mounted while the cable is already out says so at once, and over a reason as much as a picture", async () => {
    audio.last = { aux: false, volume: 1 };
    const m = await mount({ first: ov({ front: role({ device: null, recording: false, live: false, error: "no camera" }) }) });
    try {
      const a = q(m.node, ".dc-aux");
      eq([a.hidden, a.textContent, seen(m.node).why], [false, UNPLUGGED, "No front camera"]);
    } finally { m.done(); audio.last = null; }
  }],
  ["the AUX line is drowsy mode's showAux, not a second way of deciding", async () => {
    const src = (await (await fetch("../js/dashcard.js", { cache: "no-store" })).text()).replace(/^\s*\/\/.*$/gm, "");
    ok(/import \{[^}]*\bshowAux\b[^}]*\} from "\.\/audiostate\.js"/.test(src), "dashcard.js must import showAux");
    ok(!/\bauxLine\b|\bonAudio\b|\.aux\b/.test(src), "dashcard.js must not read the audio state itself");
  }],

  // ---- taking it down
  ["destroy stops the poll, the engine and the audio listeners, takes the picture away, and ignores a late answer", async () => {
    audio.last = null;
    const e = fake({ chip: "Watching", gate: { active: true } });
    const m = await mount({ engine: e, first: "hang" });
    try {
      const during = e.listeners();
      m.card.destroy();
      const a = q(m.node, ".dc-aux"), dz = q(m.node, ".dc-drowsy");
      await audioNow({ aux: false, volume: 1 });
      e.set({ chip: "Off" });
      // the poll that was out when it was taken down answers late, live
      m.t.laters[0].fn();
      await settle();
      eq([during, e.listeners(), m.t.cancelled.includes(1), a.hidden, dz.textContent, q(m.node, ".dc-img").getAttribute("src")],
         [1, 0, true, true, "Watching", null]);
    } finally { m.node.remove(); audio.last = null; }
  }],
  ["a poll that answers live after destroy puts nothing back", async () => {
    const node = document.createElement("div");
    document.body.appendChild(node);
    let answer;
    const t = timers();
    const card = dashcamCard(node, { engine: fake(), poll: () => new Promise((r) => { answer = r; }), ...t });
    card.destroy();
    answer(ov({}));
    await settle();
    const src = q(node, ".dc-img").getAttribute("src");
    node.remove();
    eq(src, null);
  }],

  // ---- Home's wiring
  ["home.js makes the card in one hunk between the branch's markers; Navigation keeps soonCard", async () => {
    const src = await (await fetch("../js/views/home.js", { cache: "no-store" })).text();
    const start = "// ---- redesign/cameras: the live front view";
    const body = (src.split(start)[1] || "").split("// ---- end redesign/cameras")[0];
    eq([body.includes("dashcam: () => {"), body.includes('import("../dashcard.js")'),
        body.includes('tappable(h("div.card.hc.hc-cam"), "cameras")'),
        /soonCard\(ICONS\.camera/.test(src), src.includes('nav: () => soonCard(ICONS.nav, "Navigation"'),
        (src.match(/redesign\/cameras/g) || []).length],
       [true, true, true, false, true, 2]);
  }],
  ["Home's Dashcams card is the live one: a tap opens Cameras, and it says why with no server", () =>
    withHome(async (root) => {
      const card = root.querySelector('[data-card="dashcam"]');
      await until(() => q(card, ".dc-why") && !q(card, ".dc-why").hidden && q(card, ".dc-why").textContent, "the card's first answer");
      const was = location.hash;
      location.hash = "";
      card.click();
      const went = location.hash;
      location.hash = was;
      eq([card.classList.contains("hc-cam"), card.getAttribute("role"), q(card, ".dc-why").textContent, went,
          q(card, ".hc-ph"), card.querySelectorAll("[data-state]").length, card.hasAttribute("data-state")],
         [true, "button", "OmaCar cannot reach its server", "#cameras", null, 0, false]);
    })],
];
