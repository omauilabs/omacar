// Omarchy Radio for the meetup demo (doc/design/2026-09-30-meetup-demo-plan.md,
// Task 4): the player, the Now Playing screen and Home's phone card.
//
// The player is exercised with a fake audio element and a fake connect, so no
// test here ever makes a sound, opens the real audio stage's output or reads
// the private songs. The playlist is a committed fixture in the station's
// format (demo-radio/playlist.json, served beside this file; the runner copies
// this folder, not test/fixtures/), fetched over HTTP like the real one.
import { eq, ok } from "./assert.js";
import { createRadio, getRadio, mmss, connectToStage } from "../demo/js/radio.js";
import { dbToGain, MUSIC_DB } from "../js/audiobus.js";
import { nowPlayingView, register as registerScreen } from "../demo/js/views/nowplaying.js";
import { phoneCard, register as registerCard } from "../demo/js/cards/phonecard.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const FIXTURE = new URL("./demo-radio/", import.meta.url).pathname;

// An <audio> that remembers what it was told and never plays a sample.
function fakeAudio(log = []) {
  const on = {};
  const a = {
    src: "", preload: "", crossOrigin: "", volume: 1, currentTime: 0, duration: NaN, paused: true,
    plays: 0, pauses: 0, log, refuse: null,
    addEventListener(type, fn) { (on[type] = on[type] || []).push(fn); },
    fire(type) { for (const fn of on[type] || []) fn({ type }); },
    play() {
      log.push("play"); a.plays++;
      if (a.refuse) return Promise.reject(a.refuse);
      a.paused = false;
      return Promise.resolve();
    },
    pause() { log.push("pause"); a.pauses++; a.paused = true; },
  };
  return a;
}

// A radio on the fixture, made and loaded. `connects` counts calls to connect.
async function made(opts = {}) {
  const audio = fakeAudio();
  let connects = 0;
  const radio = createRadio({
    base: FIXTURE,
    makeAudio: () => audio,
    connect: (el) => { connects++; audio.log.push("connect"); if (opts.connect) opts.connect(el); },
    resume: () => { audio.log.push("resume"); return Promise.resolve("running"); },
  });
  await radio.load();
  return { radio, audio, connects: () => connects };
}

const sources = ["radio.js", "views/nowplaying.js", "cards/phonecard.js"];

const engine = [
  // ---- the formatter --------------------------------------------------------
  ["m:ss: 0 is 0:00, 61.4 is 1:01, 3600 is 60:00", () =>
    eq([mmss(0), mmss(61.4), mmss(3600)], ["0:00", "1:01", "60:00"])],
  ["m:ss shows nothing false for a clock that has no time yet", () =>
    eq([mmss(NaN), mmss(-3), mmss(undefined), mmss(Infinity), mmss(9.99)], ["0:00", "0:00", "0:00", "0:00", "0:09"])],

  // ---- loading --------------------------------------------------------------
  ["load reads the station's seven tracks, in order, credited as the playlist credits them", async () => {
    const { radio } = await made();
    eq(radio.tracks.length, 7);
    eq(radio.tracks[0], { title: "Track One", artist: "Ryan R. Hughes", file: "track-1.mp3" });
    eq(radio.tracks[6].title, "Track Seven");
    eq(radio.tracks.every((t) => t.artist === "Ryan R. Hughes"), true);
  }],
  ["nothing autoplays: loading, subscribing, moving on and seeking never start a sample", async () => {
    const { radio, audio } = await made();
    radio.subscribe(() => {});
    radio.next(); radio.prev(); radio.seek(30);
    eq([audio.plays, radio.playing], [0, false]);
  }],
  ["the element is asked to preload and to be read across origins, before it is given a source", async () => {
    let seen = null;
    const audio = fakeAudio();
    Object.defineProperty(audio, "src", { set(v) { seen = [audio.preload, audio.crossOrigin, v]; }, get() { return ""; } });
    const radio = createRadio({ base: FIXTURE, makeAudio: () => audio, connect() {}, resume: async () => {} });
    await radio.load();
    eq(seen, ["auto", "anonymous", FIXTURE + "track-1.mp3"]);
  }],
  ["load is one request however many callers ask, and says so when the playlist is not there", async () => {
    const audio = fakeAudio();
    const a = createRadio({ base: FIXTURE, makeAudio: () => audio, connect() {} });
    ok(a.load() === a.load(), "the same promise");
    const bad = createRadio({ base: "/demo-radio-nowhere/", makeAudio: () => fakeAudio(), connect() {} });
    let threw = false;
    try { await bad.load(); } catch { threw = true; }
    ok(threw, "a missing playlist rejects load");
    ok(bad.error && bad.error.length > 0, "and leaves a line to show");
    eq(bad.tracks.length, 0);
  }],

  // ---- playing --------------------------------------------------------------
  ["play(2) makes track 2 the current one and starts it", async () => {
    const { radio, audio } = await made();
    radio.play(2);
    eq([radio.index, radio.playing, audio.plays], [2, true, 1]);
    ok(audio.src.endsWith("/track-3.mp3"), "the third file is the source: " + audio.src);
  }],
  ["play() with no number starts the current track, and play before load loads first", async () => {
    const audio = fakeAudio();
    const radio = createRadio({ base: FIXTURE, makeAudio: () => audio, connect() {}, resume: async () => {} });
    eq(await radio.play(0), true);
    eq([radio.tracks.length, radio.index, radio.playing, audio.plays], [7, 0, true, 1]);
  }],
  ["play(i) on the track already current continues it rather than restarting it", async () => {
    const { radio, audio } = await made();
    radio.play(3);
    audio.currentTime = 42;
    radio.pause();
    const src = audio.src;
    radio.play(3);
    eq([audio.currentTime, audio.src], [42, src]);
  }],
  ["next at 6 wraps to 0 and prev at 0 goes to 6", async () => {
    const { radio } = await made();
    radio.play(6);
    radio.next();
    eq(radio.index, 0);
    radio.prev();
    eq(radio.index, 6);
  }],
  ["moving on keeps playing when it was playing, and stays silent when it was not", async () => {
    const { radio, audio } = await made();
    radio.play(0);
    radio.next();
    eq([radio.index, radio.playing, audio.plays], [1, true, 2]);
    radio.pause();
    radio.next();
    eq([radio.index, radio.playing, audio.plays], [2, false, 2]);
  }],
  ["ended goes to the next track and keeps playing, and wraps after the last", async () => {
    const { radio, audio } = await made();
    radio.play(0);
    audio.paused = true; audio.fire("ended");
    eq([radio.index, radio.playing, audio.plays], [1, true, 2]);
    ok(audio.src.endsWith("/track-2.mp3"), audio.src);
    radio.play(6);
    audio.fire("ended");
    eq(radio.index, 0);
  }],
  ["toggle plays, then pauses", async () => {
    const { radio, audio } = await made();
    radio.toggle();
    eq(radio.playing, true);
    radio.toggle();
    eq([radio.playing, audio.pauses], [false, 1]);
  }],
  ["seek moves the element, never past the end and never before the start", async () => {
    const { radio, audio } = await made();
    radio.play(0);
    audio.duration = 254;
    radio.seek(100); eq(audio.currentTime, 100);
    radio.seek(9999); eq(audio.currentTime, 254);
    radio.seek(-5); eq(audio.currentTime, 0);
    radio.seek(NaN); eq(audio.currentTime, 0);
  }],
  ["position and duration are the element's, and 0 while it has none", async () => {
    const { radio, audio } = await made();
    eq([radio.position(), radio.duration()], [0, 0]);
    audio.currentTime = 61.4; audio.duration = 300;
    eq([radio.position(), radio.duration()], [61.4, 300]);
  }],

  // ---- the audio stage ------------------------------------------------------
  ["connect is called once, and before the first sample, even after three plays", async () => {
    const { radio, audio, connects } = await made();
    radio.play(0); radio.pause(); radio.play(1); radio.pause(); radio.play(2);
    eq(connects(), 1);
    ok(audio.log.indexOf("connect") < audio.log.indexOf("play"), "connect came first: " + audio.log.join(","));
  }],
  ["the stage is resumed in the same tap as every play, before the sample", async () => {
    const { radio, audio } = await made();
    radio.play(0); radio.pause(); radio.play(0);
    eq(audio.log.filter((x) => x === "resume").length, 2);
    ok(audio.log.indexOf("resume") < audio.log.indexOf("play"), audio.log.join(","));
  }],
  ["if the stage cannot be joined the song still plays, at the stage's own music level, never full", async () => {
    const { radio, audio } = await made({ connect() { throw new Error("no audio graph"); } });
    radio.play(0);
    eq([radio.playing, audio.plays], [true, 1]);
    eq(audio.volume, dbToGain(MUSIC_DB));
  }],
  ["the real connect joins an element to the stage once and does not throw on the second call", () => {
    const el = new Audio();
    connectToStage(el);
    connectToStage(el);
  }],

  // ---- failure --------------------------------------------------------------
  ["a browser that refuses to start the sample leaves the radio paused, with a line to show", async () => {
    const { radio, audio } = await made();
    audio.refuse = Object.assign(new Error("gesture"), { name: "NotAllowedError" });
    eq(await radio.play(0), false);
    eq(radio.playing, false);
    ok(radio.error && radio.error.length > 0, "an error line");
    audio.refuse = null;
    eq(await radio.play(0), true);
    eq(radio.error, null);
  }],
  ["a play() cut short by the next track is not a failure", async () => {
    const { radio, audio } = await made();
    audio.refuse = Object.assign(new Error("interrupted"), { name: "AbortError" });
    await radio.play(0);
    eq([radio.playing, radio.error], [true, null]);
  }],
  ["a song that will not load stops the radio and says so, instead of skipping through the list", async () => {
    const { radio, audio } = await made();
    radio.play(0);
    audio.fire("error");
    eq([radio.playing, radio.index, audio.plays], [false, 0, 1]);
    ok(radio.error, "an error line");
  }],

  ["after a failure the next play asks for the file again", async () => {
    const { radio, audio } = await made();
    radio.play(0);
    audio.fire("error");
    audio.src = "not-the-file";
    await radio.play(0);
    ok(audio.src.endsWith("/track-1.mp3"), "cued again: " + audio.src);
    eq([radio.playing, radio.error], [true, null]);
  }],

  // ---- subscribing ----------------------------------------------------------
  ["subscribers get a state after play: index, title, artist, playing, position, duration", async () => {
    const { radio, audio } = await made();
    const heard = [];
    radio.subscribe((s) => heard.push(s));
    audio.currentTime = 1.5; audio.duration = 254;
    radio.play(1);
    ok(heard.length > 0, "told");
    eq(heard[heard.length - 1], { index: 1, title: "Track Two", artist: "Ryan R. Hughes", playing: true, position: 1.5, duration: 254 });
  }],
  ["subscribers hear every 500 ms while it plays, and nothing once it pauses or they leave", async () => {
    const { radio } = await made();
    let n = 0;
    const off = radio.subscribe(() => n++);
    radio.play(0);
    const started = n;
    await wait(1200);
    ok(n - started >= 2, `two ticks in 1.2 s, heard ${n - started}`);
    radio.pause();
    const paused = n;
    await wait(1100);
    eq(n, paused, "silence while paused");
    radio.play(0);
    off();
    const gone = n;
    await wait(1100);
    eq(n, gone, "silence after unsubscribing");
    radio.pause();
  }],
  ["a subscriber that throws does not stop the music or the others", async () => {
    const { radio } = await made();
    let heard = 0;
    radio.subscribe(() => { throw new Error("a bad painter"); });
    radio.subscribe(() => heard++);
    radio.play(0);
    eq([radio.playing, heard > 0], [true, true]);
    radio.pause();
  }],
  ["the state before anything happens is the first track, paused", async () => {
    const { radio } = await made();
    eq(radio.state(), { index: 0, title: "Track One", artist: "Ryan R. Hughes", playing: false, position: 0, duration: 0 });
  }],

  // ---- the page's one player -----------------------------------------------
  ["getRadio() is the same player every time, and making it makes no sound", () => {
    ok(getRadio() === getRadio(), "one instance per page");
    eq(getRadio().playing, false);
  }],

  // ---- the silo -------------------------------------------------------------
  ["no source here touches the system volume or the live audio route", async () => {
    for (const f of sources) {
      const src = await (await fetch(new URL("../demo/js/" + f, import.meta.url))).text();
      ok(!src.includes("wpctl"), f + " mentions wpctl");
      ok(!src.includes("pactl"), f + " mentions pactl");
      ok(!src.includes("/api/audio"), f + " mentions /api/audio");
      ok(!/\/api\/(?:phone|begin|write|clear|reset)/.test(src), f + " reaches a live route");
      // The names CarPlay and Android Auto are text; their makers' marks are not ours to draw.
      ok(!/apple\.com|google\.com|\.(?:png|jpe?g|webp|gif|svg)\b/i.test(src), f + " names a logo or an image file");
    }
  }],
];

// ---- the screen and the card ----------------------------------------------
// Both are built on the Radio contract alone, so they are tested against a
// fake one that records what it is asked and lets the test push state.
function fakeRadio(over = {}) {
  const subs = new Set();
  const r = {
    tracks: ["Track One", "Track Two", "Track Three", "Track Four", "Track Five", "Track Six", "Track Seven"]
      .map((title, i) => ({ title, artist: "Ryan R. Hughes", file: `track-${i + 1}.mp3` })),
    index: 0, playing: false, error: null, pos: 0, dur: 254, calls: [],
    load() { r.calls.push(["load"]); return Promise.resolve(); },
    play(i) { r.calls.push(["play", i]); if (typeof i === "number") r.index = i; r.playing = true; r.push(); return Promise.resolve(true); },
    pause() { r.calls.push(["pause"]); r.playing = false; r.push(); },
    toggle() { r.calls.push(["toggle"]); r.playing = !r.playing; r.push(); return Promise.resolve(true); },
    next() { r.calls.push(["next"]); r.index = (r.index + 1) % 7; r.push(); },
    prev() { r.calls.push(["prev"]); r.index = (r.index + 6) % 7; r.push(); },
    seek(s) { r.calls.push(["seek", s]); r.pos = s; r.push(); },
    position: () => r.pos,
    duration: () => r.dur,
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    get subscribers() { return subs.size; },
    push() {
      const t = r.tracks[r.index];
      const s = { index: r.index, title: t.title, artist: t.artist, playing: r.playing, position: r.pos, duration: r.dur };
      for (const fn of [...subs]) fn(s);
    },
    ...over,
  };
  return r;
}

function screen(radio = fakeRadio()) {
  const root = document.createElement("div");
  document.body.appendChild(root);
  const unmount = nowPlayingView(root, { radio });
  const done = () => { if (unmount) unmount(); root.remove(); };
  return { root, radio, unmount, done, q: (sel) => root.querySelector(sel), all: (sel) => [...root.querySelectorAll(sel)] };
}

const ui = [
  // ---- Now Playing -----------------------------------------------------------
  ["the station card is set in type: OMARCHY RADIO over 'every song a pull request', no artwork", () => {
    const s = screen();
    try {
      const st = s.q(".np-station");
      eq(st.querySelector(".np-st-name").textContent, "OMARCHY RADIO");
      eq(st.querySelector(".np-st-tag").textContent, "every song a pull request");
      ok(st.querySelector(".np-st-name").compareDocumentPosition(st.querySelector(".np-st-tag")) & Node.DOCUMENT_POSITION_FOLLOWING,
         "the tagline sits under the name");
      eq(s.all("img, canvas, video, picture").length, 0, "no artwork anywhere on the screen");
      eq(s.q(".np-src").textContent, "radio.omarchy.org");
    } finally { s.done(); }
  }],
  ["the title is large, the artist is below it", () => {
    const s = screen();
    try {
      eq(s.q(".np-title").textContent, "Track One");
      eq(s.q(".np-artist").textContent, "Ryan R. Hughes");
      ok(s.q(".np-title").compareDocumentPosition(s.q(".np-artist")) & Node.DOCUMENT_POSITION_FOLLOWING, "artist follows title");
    } finally { s.done(); }
  }],
  ["opening the screen loads the station, and asks for no sound", () => {
    const s = screen();
    try { eq(s.radio.calls, [["load"]]); } finally { s.done(); }
  }],
  ["progress reads m:ss / m:ss and follows the radio", () => {
    const s = screen(fakeRadio({ pos: 61.4, dur: 254 }));
    try {
      eq([s.q(".np-pos").textContent, s.q(".np-dur").textContent], ["1:01", "4:14"]);
      s.radio.pos = 125; s.radio.push();
      eq(s.q(".np-pos").textContent, "2:05");
      eq(Math.round(parseFloat(s.q(".np-range").value)), 125);
      eq(s.q(".np-range").max, "254");
    } finally { s.done(); }
  }],
  ["until the length is known the bar is inert and the clock says 0:00", () => {
    const s = screen(fakeRadio({ dur: 0 }));
    try {
      eq([s.q(".np-pos").textContent, s.q(".np-dur").textContent], ["0:00", "0:00"]);
      eq(s.q(".np-range").disabled, true);
    } finally { s.done(); }
  }],
  ["dragging the bar previews the time, and letting go seeks", () => {
    const s = screen();
    try {
      const bar = s.q(".np-range");
      bar.value = "100";
      bar.dispatchEvent(new Event("input", { bubbles: true }));
      eq(s.q(".np-pos").textContent, "1:40");
      eq(s.radio.calls.filter((c) => c[0] === "seek").length, 0, "no seek while the finger is down");
      bar.dispatchEvent(new Event("change", { bubbles: true }));
      eq(s.radio.calls.filter((c) => c[0] === "seek"), [["seek", 100]]);
    } finally { s.done(); }
  }],
  ["previous, play/pause and next drive the radio, and the play button says which it is", () => {
    const s = screen();
    try {
      eq(s.q(".np-play").getAttribute("aria-label"), "Play");
      s.q(".np-play").click();
      eq(s.radio.playing, true);
      eq(s.q(".np-play").getAttribute("aria-label"), "Pause");
      s.q(".np-next").click();
      eq(s.q(".np-title").textContent, "Track Two");
      s.q(".np-prev").click(); s.q(".np-prev").click();
      eq(s.q(".np-title").textContent, "Track Seven");
      s.q(".np-play").click();
      eq(s.radio.playing, false);
      eq(s.q(".np-play").getAttribute("aria-label"), "Play");
      eq(s.radio.calls.map((c) => c[0]).filter((n) => n !== "load"), ["toggle", "next", "prev", "prev", "toggle"]);
    } finally { s.done(); }
  }],
  ["the list holds the seven, marks the playing one, and a tap plays that song", () => {
    const s = screen();
    try {
      eq(s.all(".np-track").length, 7);
      eq(s.all(".np-track").map((b) => b.querySelector(".np-tt").textContent).join("|"),
         "Track One|Track Two|Track Three|Track Four|Track Five|Track Six|Track Seven");
      eq(s.all('.np-track[aria-current="true"]').length, 1);
      s.all(".np-track")[4].click();
      eq(s.radio.calls.pop(), ["play", 4]);
      const cur = s.all(".np-track").findIndex((b) => b.getAttribute("aria-current") === "true");
      eq(cur, 4, "the marked row follows the song");
      s.all(".np-track")[4].click();
      eq(s.radio.calls.pop(), ["toggle"], "a tap on the playing row pauses it");
    } finally { s.done(); }
  }],
  ["a radio that cannot play says so on the screen", () => {
    const s = screen(fakeRadio({ error: "Tap play to start the music." }));
    try {
      s.radio.push();
      ok(s.q(".np-artist").textContent.includes("Tap play to start the music."), s.q(".np-artist").textContent);
    } finally { s.done(); }
  }],
  ["leaving the screen unsubscribes it and takes its markup with it", () => {
    const s = screen();
    eq(s.radio.subscribers, 1);
    s.unmount();
    eq(s.radio.subscribers, 0);
    eq(s.root.children.length, 0);
    s.root.remove();
  }],
  ["register adds the routable screen: nowplaying, Now Playing, Omarchy Radio, not fast", () => {
    const D = { extraViews: [], cards: {} };
    registerScreen(D);
    registerScreen(D);
    eq(D.extraViews.length, 1, "registering twice does not add it twice");
    const v = D.extraViews[0];
    eq([v.id, v.label, v.title, v.fast], ["nowplaying", "Now Playing", "Omarchy Radio", false]);
    ok(v.mount === nowPlayingView, "the mount is nowPlayingView");
  }],

  // ---- Home's phone card ------------------------------------------------------
  ["the phone card has three rows: Apple CarPlay, Android Auto, and Now Playing", () => {
    const c = phoneCard({ radio: fakeRadio() });
    try {
      const rows = [...c.node.querySelectorAll(".hc-row")];
      eq(rows.length, 3);
      ok(rows[0].textContent.includes("Apple CarPlay"), rows[0].textContent);
      ok(rows[1].textContent.includes("Android Auto"), rows[1].textContent);
      ok(rows[2].classList.contains("pc-radio"), "the third is the radio");
      ok(c.node.querySelector(".hc-title").textContent.includes("Phone integration"), "titled");
      eq(typeof c.paint, "function");
      eq(typeof c.destroy, "function");
    } finally { c.destroy(); }
  }],
  ["the CarPlay and Android Auto rows call what they were given", () => {
    const heard = [];
    const c = phoneCard({ radio: fakeRadio(), onCarPlay: () => heard.push("carplay"), onAndroidAuto: () => heard.push("aa") });
    try {
      c.node.querySelector(".pc-carplay").click();
      c.node.querySelector(".pc-android").click();
      eq(heard, ["carplay", "aa"]);
    } finally { c.destroy(); }
  }],
  ["by default they go to #carplay and #androidauto, and the radio row to #nowplaying", () => {
    const c = phoneCard({ radio: fakeRadio() });
    try {
      location.hash = "";
      c.node.querySelector(".pc-carplay").click();
      eq(location.hash, "#carplay");
      c.node.querySelector(".pc-android").click();
      eq(location.hash, "#androidauto");
      c.node.querySelector(".pc-radio").click();
      eq(location.hash, "#nowplaying");
    } finally { c.destroy(); history.replaceState(null, "", location.pathname); }
  }],
  ["the radio row shows the small OR tile, title and artist, and a play button that does not navigate", () => {
    const r = fakeRadio();
    const c = phoneCard({ radio: r });
    try {
      eq(c.node.querySelector(".pc-or").textContent, "OR");
      eq(c.node.querySelector(".pc-radio-text").textContent.replace(/\s+/g, " ").trim(), "Track One · Ryan R. Hughes");
      location.hash = "";
      const btn = c.node.querySelector(".pc-radio-play");
      eq(btn.getAttribute("aria-label"), "Play");
      btn.click();
      eq(location.hash, "", "the button plays without opening the screen");
      eq(r.playing, true);
      eq(btn.getAttribute("aria-label"), "Pause");
      btn.click();
      eq(r.playing, false);
    } finally { c.destroy(); history.replaceState(null, "", location.pathname); }
  }],
  // Task 8: the row's button says which song it opens, for a screen reader.
  ["the radio row's button names the song it opens", () => {
    const r = fakeRadio();
    const c = phoneCard({ radio: r });
    try {
      const open = c.node.querySelector(".pc-radio-open");
      eq(open.getAttribute("aria-label"), "Open Now Playing: Track One", "the first song");
      r.index = 2; r.push();
      eq(open.getAttribute("aria-label"), "Open Now Playing: Track Three", "follows the song");
    } finally { c.destroy(); }
  }],
  ["the card follows the song, on a push and on paint()", () => {
    const r = fakeRadio();
    const c = phoneCard({ radio: r });
    try {
      r.index = 3; r.push();
      eq(c.node.querySelector(".pc-radio-text").textContent.replace(/\s+/g, " ").trim(), "Track Four · Ryan R. Hughes");
      r.index = 5; r.playing = true;
      c.paint();
      eq(c.node.querySelector(".pc-radio-text").textContent.replace(/\s+/g, " ").trim(), "Track Six · Ryan R. Hughes");
      eq(c.node.querySelector(".pc-radio-play").getAttribute("aria-label"), "Pause");
    } finally { c.destroy(); }
  }],
  ["before the playlist is there the row says Omarchy Radio, and a failed load is not an unhandled rejection", async () => {
    const r = fakeRadio({ tracks: [], load() { return Promise.reject(new Error("offline")); } });
    const c = phoneCard({ radio: r });
    try {
      eq(c.node.querySelector(".pc-radio-text").textContent.replace(/\s+/g, " ").trim(), "Omarchy Radio");
      await wait(20);
    } finally { c.destroy(); }
  }],
  ["taps mean nothing while Home is being edited", () => {
    const heard = [];
    const grid = document.createElement("div");
    grid.className = "editing";
    document.body.appendChild(grid);
    const r = fakeRadio();
    const c = phoneCard({ radio: r, onCarPlay: () => heard.push("carplay"), onAndroidAuto: () => heard.push("aa") });
    grid.appendChild(c.node);
    try {
      location.hash = "";
      for (const sel of [".pc-carplay", ".pc-android", ".pc-radio", ".pc-radio-play"]) c.node.querySelector(sel).click();
      eq([heard, location.hash, r.playing], [[], "", false]);
    } finally { c.destroy(); grid.remove(); history.replaceState(null, "", location.pathname); }
  }],
  ["the glyphs are drawn here: inline SVG, no pictures", () => {
    const c = phoneCard({ radio: fakeRadio() });
    try {
      ok(c.node.querySelector(".pc-carplay svg"), "a CarPlay glyph");
      ok(c.node.querySelector(".pc-android svg"), "an Android Auto glyph");
      eq(c.node.querySelectorAll("img, picture, image, use").length, 0);
    } finally { c.destroy(); }
  }],
  ["destroy() lets go of the radio", () => {
    const r = fakeRadio();
    const c = phoneCard({ radio: r });
    eq(r.subscribers, 1);
    c.destroy();
    eq(r.subscribers, 0);
  }],
  ["register sets Home's phone card, which builds the {node, paint, destroy} shape", () => {
    const D = { extraViews: [], cards: {} };
    registerCard(D);
    eq(typeof D.cards.phone, "function");
    const c = D.cards.phone();
    try {
      ok(c.node instanceof HTMLElement, "a node");
      eq([typeof c.paint, typeof c.destroy], ["function", "function"]);
    } finally { c.destroy(); }
  }],
];

export default [...engine, ...ui];
