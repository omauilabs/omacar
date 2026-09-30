// The meetup demo's Agent and Work screens, and the voice they speak with
// (Task 6 of doc/design/2026-09-30-meetup-demo-plan.md).
//
// NOTHING HERE MAKES A SOUND. Every screen is handed a fake `say` and a fake
// radio; the one test that runs the real say() gets its wav from a fake fetch,
// and the runner's Chromium is --mute-audio besides.
import { eq, ok } from "./assert.js";
import { store } from "../js/core.js";
import { MUSIC_DB } from "../js/audiobus.js";
import { say, LINES, linesReady, voiceIO } from "../demo/js/voice.js";
import agentView, {
  loadScript, matchScript, stream, pace, nightLayout, NIGHT, NIGHT_LOOK, PARK_REASON,
  FALLBACK, applyNightLayout, restoreHome, agentActions, register as registerAgent,
} from "../demo/js/views/agent.js";
import workView, { tick, loadWork, register as registerWork, workActions } from "../demo/js/views/work.js";

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
      for (const tile of ["nav", "dial", "music", "tiles"]) ok(card.querySelector(`.nl-${tile}`), `draws ${tile}`);
      eq(card.querySelectorAll(".nl-tile").length, 3, "three tiles");
      ok(card.querySelector(".ag-previewbtn") && card.querySelector(".ag-apply"), "both buttons");
      card.querySelector(".ag-previewbtn").click();
      ok(document.querySelector(".ag-overlay .nl"), "Preview opens it full size");
      document.querySelector(".ag-overlay .ag-close").click();
      ok(!document.querySelector(".ag-overlay"), "and closes");
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
  ["the night arrangement puts nav, dial, phone and three tiles first, and keeps the rest", () => {
    const body = nightLayout(DEFAULT_HOME);
    for (const o of ["landscape", "portrait"]) {
      eq(body[o].cards.slice(0, 6).map(([c]) => c), ["nav", "dial", "phone", "charge", "coolant", "fuel"], o);
      eq(body[o].cards.slice(0, 6), NIGHT[o], `${o} sizes`);
      const ids = body[o].cards.map(([c]) => c);
      eq(ids.length, new Set(ids).size, `${o} holds each card once`);
      eq([...ids].sort(), DEFAULT_HOME[o].cards.map(([c]) => c).sort(), `${o} keeps every card`);
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
