// The meetup demo's Agent and Work screens, and the voice they speak with
// (Task 6 of doc/design/2026-09-30-meetup-demo-plan.md).
//
// NOTHING HERE MAKES A SOUND. Every screen is handed a fake `say` and a fake
// radio; the one test that runs the real say() gets its wav from a fake fetch,
// and the runner's Chromium is --mute-audio besides.
import { eq, ok } from "./assert.js";
import { store } from "../js/core.js";
import { MUSIC_DB, audioContext } from "../js/audiobus.js";
import { VOICE_DB as ALERT_VOICE_DB } from "../js/alertplayer.js";
import { savedLook } from "../js/looks.js";
import { orientation, loadCatalogue, spanOf } from "../js/homecards.js";
import { say, LINES, linesReady, voiceIO } from "../demo/js/voice.js";
import agentView, {
  loadScript, matchScript, stream, pace, nightLayout, NIGHT, NIGHT_LOOK, PARK_REASON,
  FALLBACK, applyNightLayout, restoreHome, agentActions, register as registerAgent,
} from "../demo/js/views/agent.js";
import workView, {
  tick, loadWork, resetWork, workClock, register as registerWork, workActions,
} from "../demo/js/views/work.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const FAST = { think: [4, 8], cps: 4000, listen: 20, typeCps: 4000 };
const REAL = Object.assign({}, pace);

// A radio that records what it was asked to do and plays nothing.
function fakeRadio() {
  const subs = new Set();
  const r = {
    tracks: [{ title: "Hello, World", artist: "Ryan R. Hughes", file: "01.mp3" }],
    index: 0, playing: false, calls: [],
    async load() { r.calls.push(["load"]); },
    play(i) { r.calls.push(["play", i]); if (i !== undefined) r.index = i; r.playing = true; ping(); },
    pause() { r.calls.push(["pause"]); r.playing = false; ping(); },
    toggle() { r.playing ? r.pause() : r.play(); },
    position: () => 0, duration: () => 180,
    subscribe(fn) { subs.add(fn); fn(state()); return () => subs.delete(fn); },
  };
  const state = () => ({ index: r.index, title: r.tracks[r.index].title, artist: r.tracks[r.index].artist,
                         playing: r.playing, position: 0, duration: 180 });
  const ping = () => subs.forEach((f) => f(state()));
  return r;
}

function live(values, demo) {
  store.live = { connected: true, values, demo };
  store.emit("live");
}

function reset() {
  store.live = null;
  store.emit("live");
  Object.assign(pace, REAL);
}

// A mounted screen, with everything it would say or play caught.
function mountAgent({ parked = true, speed = 0 } = {}) {
  Object.assign(pace, FAST);
  live({ SPEED: speed }, { parked });
  const said = [], radio = fakeRadio(), posts = [];
  const api = {
    home: async () => DEFAULT_HOME,
    saveHome: async (b) => { posts.push(b); return b; },
  };
  const root = document.createElement("div");
  document.body.appendChild(root);
  const un = agentView({ radio, say: async (id) => { said.push(id); }, api, applyLook: () => {} })(root, { arg: null });
  return { root, said, radio, posts,
           done: () => { if (un) un(); root.remove(); reset(); } };
}

async function mountWork({ speed = 0 } = {}) {
  await loadWork();
  Object.assign(pace, FAST);
  live({ SPEED: speed }, { parked: speed <= 3 });
  const said = [];
  const root = document.createElement("div");
  document.body.appendChild(root);
  const un = workView({ say: async (id) => { said.push(id); } })(root, { arg: null });
  return { root, said, done: () => { if (un) un(); root.remove(); reset(); } };
}

const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : null);
const lastReply = (root) => {
  const all = root.querySelectorAll(".ag-msg.bot .ag-text");
  return all.length ? all[all.length - 1].textContent : null;
};

// The live layout's shape (lib/homelayout.py home_layout()), the default.
const DEFAULT_HOME = {
  landscape: { cards: [["dial", "m"], ["car", "l"], ["nav", "m"], ["coolant", "s"], ["volts", "s"],
                       ["fuel", "s"], ["charge", "s"], ["phone", "m"], ["dashcam", "m"], ["agent", "m"]],
               hidden: [] },
  portrait:  { cards: [["dial", "m"], ["nav", "m"], ["car", "l"], ["coolant", "s"], ["volts", "s"],
                       ["fuel", "s"], ["charge", "s"], ["phone", "m"], ["dashcam", "m"], ["agent", "m"]],
               hidden: [] },
};

export default [
  // ---------------------------------------------------------------- the script
  ["the script has the five chips, in the mockup's order, then Omarchy Radio", async () => {
    const s = await loadScript();
    eq(s.map((x) => x.chip), ["Explain a warning", "Review my drive", "Check vehicle health",
                              "Build me a focused night-drive layout", "Play Omarchy Radio"]);
  }],
  ["free text finds its reply by keywords", async () => {
    const s = await loadScript();
    const id = (q) => (matchScript(q, s) || { id: null }).id;
    eq([id("how's my car doing"), id("How’s my car doing?"), id("night"), id("radio"),
        id("is the check engine light on?"), id("how was my drive"), id("play some music")],
       ["health", "health", "night", "radio", "warning", "drive", "radio"]);
  }],
  ["and nonsense finds none", async () => {
    const s = await loadScript();
    eq([matchScript("purple monkey dishwasher", s), matchScript("", s), matchScript("   ", s)], [null, null, null]);
  }],
  ["every chip's own words find that chip", async () => {
    const s = await loadScript();
    eq(s.map((x) => matchScript(x.chip, s).id), s.map((x) => x.id));
  }],
  ["the replies name the demo's own numbers, word for word", async () => {
    const s = await loadScript();
    const r = Object.fromEntries(s.map((x) => [x.id, x.reply]));
    eq([r.drive, r.warning, r.health], [
      "Your last 30 minutes: 38.2 mpg, 64% of braking recovered by the IMA, one hard stop on CA-1 near Castroville.",
      "No warnings right now. The last one was P0420 in March, cleared after the cat was replaced.",
      "All systems normal: the IMA pack is balanced, coolant 190°F, 12V at 14.2 V."]);
    eq(FALLBACK, "In this demo I know a few questions. Try one of the suggestions below.");
  }],

  // ---------------------------------------------------------------- streaming
  ["a reply streams in order, and completely", async () => {
    const seen = [];
    const words = "Ready to preview.\nNavigation stays prominent, brightness is reduced.";
    await stream(words, (t) => seen.push(t), { cps: 45, wait: async () => {} });
    ok(seen.length > 5, `it streamed in pieces, not at once (${seen.length})`);
    for (let i = 1; i < seen.length; i++) {
      ok(seen[i].startsWith(seen[i - 1]) && seen[i].length > seen[i - 1].length,
         `piece ${i} carries on from the one before`);
    }
    eq(seen[seen.length - 1], words, "and ends on every word");
  }],
  ["at about 45 characters a second", async () => {
    let waited = 0;
    const words = "x".repeat(450);
    await stream(words, () => {}, { cps: 45, wait: async (ms) => { waited += ms; } });
    ok(Math.abs(waited - 10000) < 300, `450 characters took ${waited} ms`);
  }],

  // ---------------------------------------------------------------- the screen
  ["the screen is mockup 7's: the car, its context, the chips, and the label", async () => {
    const m = mountAgent();
    try {
      const t = text(m.root);
      for (const want of ["Your CR-Z", "2015 · Sport Hybrid", "Vehicle context",
                          "OBD-II · Demo", "Live data connected", "Honda enhanced · Demo", "OEM data & systems",
                          "Cameras · Demo", "3 cameras available", "What would you like to do?",
                          "Illustrative agent responses"]) {
        ok(t.includes(want), `says "${want}"`);
      }
      eq(m.root.querySelector(".ag-input input").placeholder, "Ask about your car or change your layout…");
      eq([...m.root.querySelectorAll(".ag-chip")].map(text).length, 5, "five chips");
    } finally { m.done(); }
  }],
  ["a chip asks: the user's line with its time, thinking, then the reply", async () => {
    const m = mountAgent();
    try {
      const asked = agentActions.ask("warning");
      await wait(1);
      const me = m.root.querySelector(".ag-msg.me");
      eq(text(me.querySelector(".ag-text")), "Explain a warning.");
      ok(/^\d{1,2}:\d\d [AP]M$/.test(text(me.querySelector(".ag-time"))), "stamped h:mm AM/PM");
      ok(m.root.querySelector(".ag-msg.bot .ag-dots"), "three dots while it thinks");
      await asked;
      ok(!m.root.querySelector(".ag-msg.bot .ag-dots"), "and none once it answers");
      eq(lastReply(m.root), "No warnings right now. The last one was P0420 in March, cleared after the cat was replaced.");
      eq(m.said, ["warning"]);
      ok(![...m.root.querySelectorAll(".ag-chip")].some((c) => text(c) === "Explain a warning"),
         "an asked chip leaves the row");
    } finally { m.done(); }
  }],
  ["free text that matches nothing gets the fallback", async () => {
    const m = mountAgent();
    try {
      const input = m.root.querySelector(".ag-input input");
      input.value = "purple monkey dishwasher";
      m.root.querySelector(".ag-input").dispatchEvent(new Event("submit", { cancelable: true }));
      await wait(200);
      eq(lastReply(m.root), FALLBACK);
    } finally { m.done(); }
  }],
  ["the mic listens, then asks the next question not yet asked", async () => {
    const m = mountAgent();
    try {
      await agentActions.ask("warning");
      m.root.querySelector(".ag-mic").click();
      await wait(2);
      ok(text(m.root.querySelector(".ag-input")).includes("Listening…"), "it says Listening…");
      await wait(300);
      const mine = [...m.root.querySelectorAll(".ag-msg.me .ag-text")].map(text);
      eq(mine, ["Explain a warning.", "Review my drive."]);
    } finally { m.done(); }
  }],

  // ---------------------------------------------------------------- night drive
  ["the night-drive reply carries the preview card, with Preview and Apply", async () => {
    const m = mountAgent();
    try {
      await agentActions.ask("night");
      const card = m.root.querySelector(".ag-preview");
      ok(card, "a preview card");
      ok(text(card).includes("Night drive") && text(card).includes("Landscape + portrait"), "titled");
      ok(card.querySelector(".ag-previewbtn") && card.querySelector(".ag-apply"), "both buttons");
      card.querySelector(".ag-previewbtn").click();
      ok(document.querySelector(".ag-overlay .nl"), "Preview opens it full size");
      ok(document.querySelector(".ag-overlay").hasAttribute("data-demo-overlay"),
         "marked as the demo's overlay, so Esc is its and not the presenter's menu's");
      document.querySelector(".ag-overlay .ag-close").click();
      ok(!document.querySelector(".ag-overlay"), "and closes");
    } finally { m.done(); }
  }],
  // Polish: the preview is the Home that Apply posts, card for card.
  ["the preview, small and full size, draws NIGHT's cards in NIGHT's order and sizes", async () => {
    const m = mountAgent();
    try {
      await agentActions.ask("night");
      agentActions.preview();
      const cat = await loadCatalogue();
      await wait(0);
      const o = orientation();
      for (const [where, box] of [["the card", m.root.querySelector(".ag-preview")],
                                  ["the full size", document.querySelector(".ag-overlay")]]) {
        const pieces = [...box.querySelectorAll(".nl [data-card]")];
        eq(pieces.map((n) => [n.dataset.card, n.dataset.size]), NIGHT[o], `${where}: NIGHT's cards`);
        eq(pieces.map((n) => [n.style.gridColumn, n.style.gridRow]),
           NIGHT[o].map(([id, size]) => spanOf(cat, id, size, o).map((k) => `span ${k}`)), `${where}: Home's spans`);
        eq(box.querySelector(".nl").style.gridTemplateColumns, `repeat(${o === "portrait" ? 6 : 12}, minmax(0px, 1fr))`,
           `${where}: Home's columns`);
      }
    } finally { m.done(); }
  }],
  ["Work's review sheet is marked as the demo's overlay, too", async () => {
    const m = await mountWork({ speed: 0 });
    try {
      await wait(20);
      const btn = [...m.root.querySelectorAll("button")].find((b) => /Open review|Review changes/.test(b.textContent));
      ok(btn, "a review button");
      btn.click();
      const ov = document.querySelector(".wk-overlay");
      ok(ov && ov.hasAttribute("data-demo-overlay"), "marked");
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      ok(!document.querySelector(".wk-overlay"), "and Esc closes it");
    } finally { m.done(); }
  }],
  ["Apply is disabled, with the reason, until the car is parked", async () => {
    const m = mountAgent({ parked: false, speed: 60 });
    try {
      await agentActions.ask("night");
      const btn = m.root.querySelector(".ag-apply");
      eq([btn.disabled, text(m.root.querySelector(".ag-why"))], [true, PARK_REASON]);
      eq(PARK_REASON, "Park to change your layout");
      live({ SPEED: 0 }, { parked: true });
      eq([btn.disabled, m.root.querySelector(".ag-why").hidden], [false, true]);
      eq(await agentActions.apply().then((r) => r.ok), true, "and applies once parked");
      live({ SPEED: 60 }, { parked: false });
      eq((await applyNightLayout({ api: {}, applyLook() {}, say() {} })).ok, false, "never while moving");
    } finally { m.done(); }
  }],
  // Fix round 1: the full-size preview has its own Apply, so its own reason.
  ["the full-size preview says why Apply waits, too", async () => {
    const m = mountAgent({ parked: false, speed: 60 });
    try {
      await agentActions.ask("night");
      agentActions.preview();
      const btn = document.querySelector(".ag-overlay .ag-apply");
      const why = document.querySelector(".ag-overlay .ag-why");
      eq([btn.disabled, !!why && why.hidden, text(why)], [true, false, PARK_REASON]);
      live({ SPEED: 0 }, { parked: true });
      eq([btn.disabled, why.hidden], [false, true]);
    } finally { m.done(); }
  }],
  // Task 8's fix: the first arrangement (nav 4x3, dial 4x3, phone 4x2, three
  // 3x1 tiles, then the rest where they were) left holes in Home's grid and ran
  // past the screen. This one tiles it, in both orientations.
  ["the night arrangement is nav, dial and car, the four tiles, then phone, dashcam and agent", () => {
    const body = nightLayout(DEFAULT_HOME);
    const ids = ["nav", "dial", "car", "charge", "coolant", "fuel", "volts", "phone", "dashcam", "agent"];
    eq(NIGHT.landscape, [["nav", "m"], ["dial", "m"], ["car", "l"], ["charge", "s"], ["coolant", "s"],
                         ["fuel", "s"], ["volts", "s"], ["phone", "m"], ["dashcam", "m"], ["agent", "m"]], "landscape");
    for (const o of ["landscape", "portrait"]) {
      eq(body[o].cards.map(([c]) => c), ids, o);
      eq(body[o].cards.slice(0, NIGHT[o].length), NIGHT[o], `${o} sizes`);
      eq([...ids].sort(), DEFAULT_HOME[o].cards.map(([c]) => c).sort(), `${o} keeps every card`);
    }
  }],
  ["the night arrangement fills Home's grid with no hole, in as many rows as the default", async () => {
    const cat = await (await fetch("../data/home-cards.json")).json();
    // CSS grid's row-dense auto-flow (home.css): each card at the first place,
    // row by row, where it fits.
    const place = (cards, orient, width) => {
      const grid = [];
      const free = (r, c, w, hh) => {
        for (let y = r; y < r + hh; y++) for (let x = c; x < c + w; x++) if (grid[y] && grid[y][x]) return false;
        return c + w <= width;
      };
      for (const [id, size] of cards) {
        const [w, hh] = cat.cards[id].sizes[size][orient];
        let r = 0, c = 0;
        for (;;) {
          if (free(r, c, w, hh)) break;
          c++;
          if (c + w > width) { c = 0; r++; }
        }
        for (let y = r; y < r + hh; y++) {
          grid[y] = grid[y] || Array(width).fill(null);
          for (let x = c; x < c + w; x++) grid[y][x] = id;
        }
      }
      return grid;
    };
    const body = nightLayout(DEFAULT_HOME);
    for (const [o, orient, width] of [["landscape", "land", 12], ["portrait", "port", 6]]) {
      const night = place(body[o].cards, orient, width);
      const holes = night.flatMap((row, y) => row.map((v, x) => (v ? null : `${y},${x}`))).filter(Boolean);
      eq(holes, [], `${o}: no hole`);
      eq(night.length, place(DEFAULT_HOME[o].cards, orient, width).length, `${o}: the default's rows`);
    }
  }],
  ["Apply keeps the Home layout, posts the night one, dims, and says so; restart puts it back", async () => {
    const calls = [];
    const io = {
      parked: true,
      api: { home: async () => { calls.push("GET"); return DEFAULT_HOME; },
             saveHome: async (b) => { calls.push(["POST", b.landscape.cards[0][0]]); return b; } },
      applyLook: (id) => calls.push(["look", id]),
      say: async (id) => { calls.push(["say", id]); },
    };
    const r = await applyNightLayout(io);
    eq(r.ok, true);
    eq(calls, ["GET", ["POST", "nav"], ["look", NIGHT_LOOK], ["say", "layout-applied"]]);
    eq(NIGHT_LOOK, "dim", "the darkest night look that keeps the palette");
    calls.length = 0;
    await applyNightLayout(io);
    calls.length = 0;
    await restoreHome(io);
    eq(calls, [["POST", "dial"], ["look", "normal"]], "the layout from before the first Apply, and the look");
  }],
  // Fix round 1: a restart after a reload has nothing kept, and still resets.
  ["with nothing kept (a reload since), restart resets Home and puts the saved look back", async () => {
    const calls = [];
    const io = { api: { saveHome: async (b) => { calls.push(["POST", b]); return b; } },
                 applyLook: (id) => calls.push(["look", id]) };
    await restoreHome(io);
    eq(calls, [["POST", { action: "reset" }], ["look", savedLook()]]);
  }],

  // ---------------------------------------------------------------- the radio
  ["Play Omarchy Radio plays track one and says what it is", async () => {
    const m = mountAgent();
    try {
      await agentActions.ask("radio");
      ok(m.radio.calls.some(([c, i]) => c === "play" && i === 0), "radio.play(0)");
      eq(lastReply(m.root), "Playing Omarchy Radio: Hello, World, by Ryan R. Hughes.");
      ok(text(m.root.querySelector(".ag-np")).includes("Hello, World"), "a now-playing chip");
      ok(m.said.includes("radio"), "and says so");
    } finally { m.done(); }
  }],
  ["register puts Agent on the advisor screen, Work on the work screen, both fast", () => {
    const D = { views: {}, extraViews: [], tabRoots: {}, cards: {} };
    registerAgent(D, { radio: fakeRadio() });
    registerWork(D, { radio: fakeRadio() });
    eq([typeof D.views.advisor.mount, D.views.advisor.fast], ["function", true]);
    eq([typeof D.views.work.mount, D.views.work.fast], ["function", true]);
  }],

  // ---------------------------------------------------------------- Work
  ["Work, parked: the four sessions, the accounts and the concept label", async () => {
    const m = await mountWork({ speed: 0 });
    try {
      ok(m.root.querySelector(".wk-park") && !m.root.querySelector(".wk-drive"), "the parked view");
      const t = text(m.root);
      for (const want of ["CONCEPT · DEMO SESSIONS", "Claude 01", "Claude 02", "Claude 03", "Codex",
                          "Max 20x", "Subscription", "Your sessions", "OmaCar", "Implementing voice controls",
                          "Bulletin Builder", "Tests passed, ready for review", "OmaSaber", "Needs input",
                          "OmaCar mobile", "Running", "Ready for review", "6 files changed"]) {
        ok(t.includes(want), `says "${want}"`);
      }
      eq(m.root.querySelectorAll(".wk-acct .wk-ring").length, 4, "four usage rings");
      // Fix round 1: neutral glyphs in the app's colours, never a company's mark.
      eq([m.root.querySelectorAll(".wk-ring .g.mono").length, m.root.querySelectorAll(".wk-ring .g.codex").length,
          m.root.querySelectorAll(".wk-ring .claude").length], [3, 1, 0], "a monogram for Claude, a prompt for Codex");
      eq(m.root.querySelectorAll(".wk-sess").length, 4, "four sessions");
      for (const b of ["Speak", "Pause session", "Open review"]) {
        ok([...m.root.querySelectorAll("button")].some((x) => text(x) === b), `a ${b} button`);
      }
    } finally { m.done(); }
  }],
  ["Work turns to the driving view above 3 km/h, and back", async () => {
    const m = await mountWork({ speed: 50 });
    try {
      const d = m.root.querySelector(".wk-drive");
      ok(d && !m.root.querySelector(".wk-park"), "the driving view");
      ok(text(d).includes("Your agents are working"), "titled");
      eq([...d.querySelectorAll(".wk-count b")].map(text), ["2", "1", "1"]);
      eq([...d.querySelectorAll(".wk-count span")].map(text), ["running", "ready", "needs you"]);
      ok(text(d).includes("Listening…"), "listening");
      ok([...d.querySelectorAll("button")].some((x) => text(x) === "Send instruction"), "Send instruction");
      live({ SPEED: 2 }, { parked: false });
      ok(m.root.querySelector(".wk-park") && !m.root.querySelector(".wk-drive"), "3 km/h and under is parked");
    } finally { m.done(); }
  }],
  ["Give me an update says work-update and shows its words", async () => {
    await linesReady;
    for (const speed of [0, 50]) {
      const m = await mountWork({ speed });
      try {
        await workActions.update();
        eq(m.said, ["work-update"], `said, at ${speed} km/h`);
        ok(text(m.root).includes(LINES["work-update"]), `and shown, at ${speed} km/h`);
      } finally { m.done(); }
    }
  }],
  ["a session's steps tick along every 20 to 40 s, and loop", () => {
    const s = { steps: ["a", "b", "c"], done: 1, at: [1000], next: 0 };
    tick(s, 0, () => 0);
    const first = s.next;
    ok(first >= 20000 && first <= 40000, `the next step is 20–40 s away (${first})`);
    tick(s, first - 1, () => 0);
    eq(s.done, 1, "not before");
    tick(s, first, () => 1);
    eq(s.done, 2, "one more at the time");
    ok(s.next - first >= 20000 && s.next - first <= 40000, "and the next after that 20–40 s on");
    tick(s, s.next, () => 0.5);
    eq(s.done, 3, "the last");
    tick(s, s.next, () => 0.5);
    eq(s.done, 0, "and round again");
  }],
  // Fix round 1: a second tour starts where the first did.
  ["resetWork puts every session back: after a send and some steps, 2/1/1 again", async () => {
    const doc = await fetch("/demo/data/work.json", { cache: "no-store" }).then((r) => r.json());
    const shape = (list) => list.map((x) => [x.id, x.status, x.summary, x.done, !!x.paused]);
    const m = await mountWork({ speed: 50 });
    try {
      const w = await loadWork();
      const d = m.root.querySelector(".wk-drive");
      eq(await workActions.send(), true, "sent");
      eq([...d.querySelectorAll(".wk-count b")].map(text), ["3", "1", "0"], "OmaSaber running after the send");
      for (const x of w.sessions) { x.next = 1; tick(x, 1, () => 0); }
      w.sessions[0].paused = true;
      ok(JSON.stringify(shape(w.sessions)) !== JSON.stringify(shape(doc.sessions)), "the sessions moved on");
      resetWork();
      eq(shape(w.sessions), shape(doc.sessions), "every session as work.json starts it");
      eq(w.sessions.map((x) => x.next), [0, 0, 0, 0], "with its clock unscheduled");
      eq([...d.querySelectorAll(".wk-count b")].map(text), ["2", "1", "1"], "2/1/1 on the screen");
      eq(d.querySelector(".wk-dacts").hidden, false, "and the instruction a draft again");
    } finally { m.done(); }
  }],
  // A session that is ready for review or waiting for you is not working: its
  // checklist does not move while you look at it (mockup 8, and "2 running, 1
  // ready, 1 needs you" stays true).
  ["only Running sessions tick: Review and Needs-input ones hold their checklist", async () => {
    const real = Object.assign({}, workClock);
    const timers = new Map();
    let n = 0, t = 1e12;
    Object.assign(workClock, { now: () => t, every: (fn) => { timers.set(++n, fn); return n; },
                               stop: (id) => { timers.delete(id); } });
    const fire = () => [...timers.values()].forEach((f) => f());
    try {
      const w = await loadWork();
      resetWork();
      const m = await mountWork({ speed: 0 });
      try {
        const by = (id) => w.sessions.find((x) => x.id === id);
        eq(w.sessions.map((x) => x.status), ["running", "review", "input", "running"], "work.json's states");
        const held = ["bulletin", "omasaber"].map((id) => [id, by(id).done, (by(id).at || []).length]);
        const ran = ["omacar", "mobile"].map((id) => by(id).done);
        fire();                                      // the first call only schedules
        for (let i = 0; i < 6; i++) { t += 45000; fire(); }   // and then 4½ minutes
        eq(["bulletin", "omasaber"].map((id) => [id, by(id).done, (by(id).at || []).length]), held,
           "Review and Needs input: the same steps, finished at the same times");
        eq(["bulletin", "omasaber"].map((id) => by(id).next), [0, 0], "their clocks were never even set");
        ok(["omacar", "mobile"].some((id, i) => by(id).done !== ran[i]), "while the running ones moved on");
      } finally { m.done(); }
    } finally { Object.assign(workClock, real); resetWork(); }
  }],
  ["a session that needs you starts ticking once you answer it (the driving instruction is sent)", async () => {
    const real = Object.assign({}, workClock);
    const timers = new Map();
    let n = 0, t = 1e12;
    Object.assign(workClock, { now: () => t, every: (fn) => { timers.set(++n, fn); return n; },
                               stop: (id) => { timers.delete(id); } });
    const fire = () => [...timers.values()].forEach((f) => f());
    try {
      const w = await loadWork();
      resetWork();
      const m = await mountWork({ speed: 50 });
      try {
        const saber = w.sessions.find((x) => x.id === "omasaber");
        fire();
        t += 45000; fire();
        eq(saber.done, 2, "waiting for direction: its checklist holds");
        eq(await workActions.send(), true, "the instruction is sent");
        eq(saber.status, "running", "running now");
        fire();                                      // schedules
        t += 45000; fire();
        eq(saber.done, 3, "and its next step lands");
      } finally { m.done(); }
    } finally { Object.assign(workClock, real); resetWork(); }
  }],
  ["Work's step clock stops when the screen goes", async () => {
    const real = Object.assign({}, workClock);
    const timers = new Map();
    let n = 0, t = 1e12;
    Object.assign(workClock, { now: () => t, every: (fn) => { timers.set(++n, fn); return n; },
                               stop: (id) => { timers.delete(id); } });
    const fire = () => [...timers.values()].forEach((f) => f());
    try {
      const w = await loadWork();
      resetWork();
      const m = await mountWork({ speed: 0 });
      const start = w.sessions.map((x) => x.done);
      eq(timers.size, 1, "one clock while the screen is up");
      fire();
      t += 60000;
      fire();
      const moved = w.sessions.map((x) => x.done);
      ok(JSON.stringify(moved) !== JSON.stringify(start), "and it moves the steps");
      m.done();
      eq(timers.size, 0, "none once the screen has gone");
      t += 600000;
      fire();
      eq(w.sessions.map((x) => x.done), moved, "so nothing ticks after");
    } finally { Object.assign(workClock, real); resetWork(); }
  }],

  // ---------------------------------------------------------------- the voice
  ["the lines other screens speak, word for word", async () => {
    await linesReady;
    eq([LINES["work-update"], LINES["layout-applied"], LINES.radio, LINES["drowsy-l2"], LINES.health], [
      "Two agents are running. Bulletin Builder passed its tests and is ready for your review. OmaSaber needs your input on the saber colour.",
      "Night drive is on. Navigation stays large, and I've dimmed the rest.",
      "Playing Omarchy Radio.",
      "James, are you with me?",
      "All systems normal."]);
  }],
  ["every reply with a voice has its line", async () => {
    await linesReady;
    const s = await loadScript();
    const missing = s.filter((x) => x.voice && !LINES[x.voice]).map((x) => x.id);
    eq(missing, []);
  }],
  ["say resolves at once when the fetch fails, and when the file is missing", async () => {
    const real = voiceIO.fetch;
    try {
      voiceIO.fetch = () => Promise.reject(new Error("offline"));
      eq(await Promise.race([say("health").then(() => "done"), wait(400).then(() => "hung")]), "done");
      voiceIO.fetch = async () => new Response("", { status: 404 });
      eq(await Promise.race([say("nope").then(() => "done"), wait(400).then(() => "hung")]), "done");
    } finally { voiceIO.fetch = real; }
  }],
  // Fix round 1: the duck is laid before the line is built, so the music must
  // come back however building or starting it fails.
  ["a line whose playback throws still brings the music back", async () => {
    const realFetch = voiceIO.fetch, realSchedule = voiceIO.schedule, realLevel = voiceIO.levelAt;
    const ctx = audioContext();
    const proto = Object.getPrototypeOf(ctx);
    const breaks = {
      createGain: () => { ctx.createGain = () => { throw new Error("no gain"); }; },
      start: () => {
        ctx.createBufferSource = () => {
          const src = proto.createBufferSource.call(ctx);
          src.start = () => { throw new Error("no start"); };
          return src;
        };
      },
    };
    try {
      voiceIO.fetch = async () => new Response(silentWav(0.05));
      voiceIO.levelAt = () => MUSIC_DB;
      // Decoded (and cached) first, with nothing broken. The page runs on
      // virtual time and decoding is real work it does not wait for, so a race
      // against the clock is only fair once there is nothing left to decode.
      voiceIO.schedule = () => true;
      await say("drowsy-l2");
      for (const [what, breakIt] of Object.entries(breaks)) {
        const plans = [];
        voiceIO.schedule = (bus, points) => { plans.push([bus, points]); return true; };
        breakIt();
        try {
          eq(await Promise.race([say("drowsy-l2").then(() => "done"), wait(3000).then(() => "hung")]), "done",
             `${what} throwing still resolves`);
        } finally { delete ctx.createGain; delete ctx.createBufferSource; }
        eq(plans.map(([bus, p]) => [bus, p.at(-1)[1]]), [["music", MUSIC_DB - 12], ["music", MUSIC_DB]],
           `${what} throwing: ducked, then the music bus back at MUSIC_DB`);
      }
    } finally { voiceIO.fetch = realFetch; voiceIO.schedule = realSchedule; voiceIO.levelAt = realLevel; }
  }],
  ["say ducks the music 12 dB over 0.4 s, and brings it back over 0.8 s", async () => {
    const realFetch = voiceIO.fetch, realSchedule = voiceIO.schedule, realLevel = voiceIO.levelAt;
    const plans = [];
    let fetches = 0, heard = null;
    const onSay = (e) => { heard = e.detail; };
    document.addEventListener("omacar-demo:say", onSay);
    try {
      voiceIO.fetch = async () => { fetches++; return new Response(silentWav(0.05)); };
      // The real bus is never moved: the plans are caught, and the music is
      // where it sits at rest.
      voiceIO.schedule = (bus, points, at) => { plans.push([bus, points]); return true; };
      voiceIO.levelAt = () => MUSIC_DB;
      await say("radio");
      await say("radio");
      eq(fetches, 1, "fetched and decoded once");
      eq(heard, { id: "radio", text: "Playing Omarchy Radio." }, "the caption goes out with it");
      const [down, up] = plans;
      eq([down[0], up[0]], ["music", "music"]);
      eq([down[1][0][1], down[1].at(-1)], [MUSIC_DB, [0.4, MUSIC_DB - 12]]);
      eq([up[1][0][1], up[1].at(-1)], [MUSIC_DB - 12, [0.8, MUSIC_DB]]);
    } finally {
      voiceIO.fetch = realFetch; voiceIO.schedule = realSchedule; voiceIO.levelAt = realLevel;
      document.removeEventListener("omacar-demo:say", onSay);
    }
  }],
  // The demo's lines go straight to the output, past the stage's limiter, so
  // they sit where the alert player's quietest voice does (Level 1: -7), not
  // where Level 2 and 3's do (-3).
  ["a line plays at the alert player's Level 1 voice level, -7 dB", async () => {
    const realFetch = voiceIO.fetch, realSchedule = voiceIO.schedule, realLevel = voiceIO.levelAt;
    const ctx = audioContext();
    const proto = Object.getPrototypeOf(ctx);
    const made = [];
    try {
      voiceIO.fetch = async () => new Response(silentWav(0.05));
      voiceIO.schedule = () => true;
      voiceIO.levelAt = () => MUSIC_DB;
      ctx.createGain = () => { const g = proto.createGain.call(ctx); made.push(g); return g; };
      await say("radio");
      eq(made.length, 1, "the line made its one gain node");
      const db = 20 * Math.log10(made[0].gain.value);
      eq(ALERT_VOICE_DB[1], -7, "Level 1's voice is -7");
      ok(Math.abs(db - ALERT_VOICE_DB[1]) < 0.01, `the line's gain is ${db.toFixed(2)} dB`);
    } finally {
      delete ctx.createGain;
      voiceIO.fetch = realFetch; voiceIO.schedule = realSchedule; voiceIO.levelAt = realLevel;
    }
  }],
];

// A mono 16-bit wav of silence, as Piper writes them.
function silentWav(secs, rate = 22050) {
  const n = Math.round(secs * rate), buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const s = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  s(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); s(8, "WAVE"); s(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
  v.setUint16(34, 16, true); s(36, "data"); v.setUint32(40, n * 2, true);
  return buf;
}
