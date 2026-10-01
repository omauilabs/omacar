// The demo with no footage (hardening D): how the page knows (demo/js/footage.js,
// from GET /api/cams), Home's Dashcams card (demo/js/cards/dashcam.js) and the
// Cameras screen (demo/js/views/cameras.js), each drawing an empty state of its
// own while no camera is recording and handing over to the LIVE card and view
// (share/js/dashcard.js, share/js/views/cameras.js) when one is.
//
// The tour's half of it (a step that `needs` clips is jumped over) is in
// demo-tour.test.js; the drowsy moment's (it asks the cameras for nothing) is at
// the end of demo-drowsy.test.js.
//
// EVERYTHING OUTSIDE THEM IS A FAKE: the server is a function that answers as
// told, the timers are lists this file fires by hand, and the live card and view
// are stand-ins that note how they were called (the last tests of each section
// run the real ones, on a scripted server). Nothing here reaches a server.
import { eq, ok } from "./assert.js";
import { hasFootage, createFootage, getFootage, POLL_TIMEOUT_MS, RECHECK_MS,
         EMPTY_TITLE, EMPTY_LINE, NEEDS_CLIPS } from "../demo/js/footage.js";
import { dashcamCard, register as registerCard } from "../demo/js/cards/dashcam.js";
import { camerasView, register as registerView } from "../demo/js/views/cameras.js";
import { dashcamCard as liveDashcamCard } from "../js/dashcard.js";
import liveCamerasView from "../js/views/cameras.js";

const settle = () => new Promise((r) => setTimeout(r, 0));
const role = (o) => Object.assign({ device: "demo", mode: { fmt: "H264", w: 1920, h: 1080, fps: 30 },
  sim: false, recording: true, live: true, fps: 29.9, error: null }, o);
const off = (why = "no camera") => role({ device: null, mode: null, recording: false, live: false, fps: null, error: why });
// What GET /api/cams says with the camera feed up, and with nothing (cams.py overview()).
const UP = { running: true, sim: false, storage: { used: 12.34e9, budget: 40e9 },
             roles: { front: role({}), rear: role({}), cabin: role({}) } };
const DOWN = { running: false, sim: false, storage: { used: 0, budget: 40e9 },
               roles: { front: off(), rear: off(), cabin: off() } };

// Timers the test fires by hand: the repeating ones (`every`, with the interval
// asked for), the one-shot (`later`), and what was cancelled.
function timers() {
  const t = { ticks: [], laters: [], cancelled: [] };
  t.every = (fn, ms) => { t.ticks.push({ fn, ms }); return t.ticks.length; };
  t.later = (fn, ms) => { t.laters.push({ fn, ms }); return -t.laters.length; };
  t.cancel = (id) => t.cancelled.push(id);
  t.running = () => t.ticks.filter((_, i) => !t.cancelled.includes(i + 1));
  return t;
}

// A footage detector that says what it is told: `has()` (true, false or null), and
// `check()` answering when the test says.
function fakeFootage(first = null) {
  const f = { value: first, checks: 0, held: null };
  f.has = () => f.value;
  f.check = () => {
    f.checks++;
    return f.hold ? new Promise((yes) => { f.held = () => yes(f.value); }) : Promise.resolve(f.value);
  };
  return f;
}

const SAYS_NOTHING_WRONG = /no front camera|no camera|recorder off|checking|cannot reach|REC\b|simulated|error|\boff\b/i;

export default [
  // ---- how the page knows ---------------------------------------------------------
  ["hasFootage: a role is recording, and the recorder is running", () => {
    eq(hasFootage(UP), true, "the feed up");
    eq(hasFootage(DOWN), false, "nothing running");
    eq(hasFootage(null), false, "no answer");
    eq(hasFootage({}), false, "an empty answer");
    eq(hasFootage({ running: true }), false, "running, no roles");
    eq(hasFootage({ running: false, roles: UP.roles }), false, "a recorder that is not running has no footage");
    eq(hasFootage({ running: true, roles: { front: off(), rear: off(), cabin: off() } }), false, "running, none recording");
    eq(hasFootage({ running: true, roles: { front: role({}), rear: off(), cabin: off() } }), true,
       "one camera is enough: the rest say so themselves");
    eq(hasFootage({ running: true, roles: { cabin: role({}) } }), true, "any role");
    eq(hasFootage({ running: true, roles: { front: role({ recording: false, starting: true }) } }), false,
       "starting is not yet recording");
    eq(NEEDS_CLIPS, "clips", "the word tour.json uses");
  }],

  ["createFootage: knows nothing until it has asked, then what GET /api/cams said", async () => {
    const asked = [];
    let answer = DOWN;
    const t = timers();
    const f = createFootage({ get: (path, o) => { asked.push([path, !!(o && o.signal)]); return Promise.resolve(answer); },
                              later: t.later, cancel: t.cancel });
    eq(f.has(), null, "nothing asked: null");
    eq(await f.check(), false, "check() resolves to the answer");
    eq(f.has(), false, "no footage");
    answer = UP;
    eq(await f.check(), true, "and when the feed is up");
    eq(f.has(), true, "has() agrees");
    eq(asked, [["/api/cams", true], ["/api/cams", true]], "the one route, with a signal to give it up by");
    eq(t.cancelled.length, t.laters.length, "the give-up timer is cancelled after each answer");
  }],

  ["createFootage: one look at a time, and a caller that comes round meanwhile waits on that one", async () => {
    let calls = 0, answer;
    const f = createFootage({ get: () => { calls++; return new Promise((yes) => { answer = yes; }); }, later: () => 0, cancel: () => {} });
    const a = f.check(), b = f.check();
    eq(calls, 1, "one request");
    answer(UP);
    eq([await a, await b], [true, true], "both told");
    f.check();
    eq(calls, 2, "and the next look is a new one");
  }],

  ["createFootage: a failed or given-up look keeps what was known, and never rejects", async () => {
    const t = timers();
    let mode = "up";
    const f = createFootage({
      get: (path, { signal }) => {
        if (mode === "up") return Promise.resolve(UP);
        if (mode === "down") return Promise.reject(new Error("503"));
        return new Promise((_, no) => signal.addEventListener("abort", () => no(new Error("aborted"))));
      },
      later: t.later, cancel: t.cancel,
    });
    eq(await f.check(), true, "up");
    mode = "down";
    eq(await f.check(), true, "a 503: what it knew stands");
    mode = "hang";
    const hung = f.check();
    eq(t.laters[t.laters.length - 1].ms, POLL_TIMEOUT_MS, "it is given up after POLL_TIMEOUT_MS");
    t.laters[t.laters.length - 1].fn();
    eq(await hung, true, "a given-up look is a miss too");
    const fresh = createFootage({ get: () => Promise.reject(new Error("no server")), later: t.later, cancel: t.cancel });
    eq(await fresh.check(), null, "never answered, and still not: null, not false");
    eq(fresh.has(), null, "has() says so");
    eq(POLL_TIMEOUT_MS < RECHECK_MS, true, "a look is over before the next is due");
  }],

  ["getFootage is the page's one detector", () => {
    ok(getFootage() === getFootage(), "the same object");
    eq(typeof getFootage().check, "function", "it can look");
  }],

  // ---- Home's Dashcams card ----------------------------------------------------------------
  ["the card with no footage: the Dashcams title, a camera glyph and the two lines, in the card's own look", async () => {
    const f = fakeFootage(false);
    const c = dashcamCard({ footage: f, live: () => { throw new Error("no live card without footage"); }, ...timers() });
    document.body.appendChild(c.node);
    await settle();
    const text = c.node.textContent;
    ok(c.node.matches(".card.hc.hc-cam"), "a Home card, the Dashcams card's own class");
    ok(text.includes("Dashcams"), "its title: " + text);
    ok(text.includes(EMPTY_TITLE), "the first line: " + text);
    ok(text.includes(EMPTY_LINE), "the second: " + text);
    eq(EMPTY_TITLE, "Front, rear and cabin cameras", "the brief's words");
    eq(EMPTY_LINE, "Recorded in one-minute clips, and a hard stop saves the clip", "and the other");
    ok(c.node.querySelector("svg"), "a camera glyph");
    ok(!SAYS_NOTHING_WRONG.test(text.replace("Dashcams", "")), "no reason, no REC, no Off: " + text);
    eq(c.node.querySelectorAll("img, video").length, 0, "no picture to be broken");
    ok(c.node.classList.contains("nf-card"), "styled as its own (demo/css/nofootage.css)");
    c.destroy();
    c.node.remove();
  }],

  ["the card is a button to Cameras, as the live card is, and not while Home is being edited", async () => {
    const went = [];
    const c = dashcamCard({ footage: fakeFootage(false), go: (id) => went.push(id), live: () => ({}), ...timers() });
    document.body.appendChild(c.node);
    eq([c.node.getAttribute("role"), c.node.tabIndex], ["button", 0], "a button");
    c.node.click();
    c.node.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    c.node.dispatchEvent(new KeyboardEvent("keydown", { key: "x" }));
    eq(went, ["cameras", "cameras"], "a tap and Enter open Cameras");
    const edit = document.createElement("div");
    edit.className = "editing";
    document.body.appendChild(edit);
    edit.appendChild(c.node);
    c.node.click();
    eq(went.length, 2, "an edited layout's tap is the editor's");
    c.destroy();
    edit.remove();
  }],

  ["the card hands over to the live card when a camera is recording, and gives the node to it whole", async () => {
    const calls = [];
    const f = fakeFootage(true);
    const live = (node) => { calls.push(node); node.appendChild(Object.assign(document.createElement("img"), { className: "dc-img" }));
                             return { paint() { calls.push("paint"); }, destroy() { calls.push("destroy"); } }; };
    const c = dashcamCard({ footage: f, live, ...timers() });
    document.body.appendChild(c.node);
    await settle();
    eq(calls[0] === c.node, true, "the live card is made on the card's own node");
    ok(!c.node.textContent.includes(EMPTY_LINE), "no empty state under it");
    ok(!c.node.classList.contains("nf-card"), "and none of its look");
    ok(c.node.matches(".card.hc.hc-cam"), "the live look's classes are the card's");
    c.paint();
    eq(calls[1], "paint", "Home's paint reaches it");
    c.destroy();
    eq(calls[calls.length - 1], "destroy", "and its destroy");
    c.node.remove();
  }],

  ["no answer yet: the empty state shows, never a black or empty card, and then the answer decides", async () => {
    const f = fakeFootage(null);
    f.hold = true;
    let made = 0;
    const c = dashcamCard({ footage: f, live: (n) => { made++; return { paint() {}, destroy() {} }; }, ...timers() });
    document.body.appendChild(c.node);
    ok(c.node.textContent.includes(EMPTY_LINE), "the empty state while it asks");
    eq(f.checks, 1, "it asked as it was made: Home mounted");
    f.value = true;
    f.held();
    await settle();
    eq(made, 1, "footage: the live card");
    ok(!c.node.textContent.includes(EMPTY_LINE), "and the empty state is gone");
    c.destroy();
    c.node.remove();
  }],

  ["while empty it looks again every RECHECK_MS, and switches back by itself when a camera is recording", async () => {
    const f = fakeFootage(false);
    const t = timers();
    let made = 0;
    const c = dashcamCard({ footage: f, live: () => { made++; return { paint() {}, destroy() {} }; }, ...t });
    document.body.appendChild(c.node);
    await settle();
    eq(t.running().map((x) => x.ms), [RECHECK_MS], "one repeating look, every " + RECHECK_MS + " ms");
    eq(RECHECK_MS, 5000, "5 s");
    t.ticks[0].fn();
    await settle();
    eq([made, f.checks], [0, 2], "looked again: still none");
    f.value = true;
    t.ticks[0].fn();
    await settle();
    eq(made, 1, "the clips came: the live card");
    ok(!c.node.textContent.includes(EMPTY_LINE), "the empty state is gone");
    eq(t.running().length, 0, "and the looking stops: the live card polls for itself");
    c.destroy();
    c.node.remove();
  }],

  ["a card taken out of Home stops looking, and a late answer starts nothing", async () => {
    const f = fakeFootage(false);
    f.hold = true;
    const t = timers();
    let made = 0, destroyed = 0;
    const c = dashcamCard({ footage: f, live: () => { made++; return { paint() {}, destroy() { destroyed++; } }; }, ...t });
    document.body.appendChild(c.node);
    f.value = true;
    c.destroy();
    eq(t.running().length, 0, "the repeating look is cancelled");
    f.held();
    await settle();
    eq(made, 0, "a look answered after destroy() builds nothing");
    c.node.remove();
    // and a live card that was made is destroyed with it
    const g = fakeFootage(true);
    const d = dashcamCard({ footage: g, live: () => ({ paint() {}, destroy() { destroyed++; } }), ...timers() });
    await settle();
    d.destroy();
    d.destroy();
    eq(destroyed, 1, "once, however often it is asked");
  }],

  ["the real live card, handed over to, draws its picture and REC as the live app does", async () => {
    const f = fakeFootage(true);
    const t = timers();
    const asked = [];
    const engine = { state: { chip: "Watching", level: 0, trigger: null, gate: { active: true }, cfg: { enabled: true, min_speed_mph: 30 }, aux: "" },
                     on(fn) { fn(engine.state); return () => {}; } };
    const live = (node) => liveDashcamCard(node, { engine, poll: (path) => { asked.push(path); return Promise.resolve(UP); },
                                                    every: t.every, later: t.later, cancel: t.cancel });
    const c = dashcamCard({ footage: f, live, ...t });
    document.body.appendChild(c.node);
    await settle();
    await settle();
    eq(asked, ["/api/cams"], "the live card's own poll");
    const img = c.node.querySelector("img.dc-img");
    ok(img && img.getAttribute("src") && img.getAttribute("src").includes("/api/cams/front/live"), "the front camera's live picture");
    eq(c.node.querySelector(".dc-rec").hidden, false, "REC");
    ok(!c.node.textContent.includes(EMPTY_LINE), "and none of the empty state");
    c.destroy();
    eq(c.node.querySelector("img.dc-img").hasAttribute("src"), false, "destroying lets go of the stream");
    c.node.remove();
  }],

  ["register: dresses Home's `dashcam` card through the door, and nothing else", () => {
    const D = { cards: { car: () => 1 }, views: {} };
    const f = fakeFootage(false);
    registerCard(D, { footage: f, ...timers() });
    eq(Object.keys(D.cards).sort(), ["car", "dashcam"], "one more card");
    const c = D.cards.dashcam();
    ok(c.node && typeof c.paint === "function" && typeof c.destroy === "function", "home.js's card shape");
    c.destroy();
    eq(Object.keys(D.views), [], "no view");
  }],

  // ---- the Cameras screen ---------------------------------------------------------------
  ["the screen with no footage: its empty state, and none of the live view's feeds, timeline or buttons", async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    let mounted = 0;
    const un = camerasView({ footage: fakeFootage(false), live: () => { mounted++; return () => {}; }, ...timers() })(root, { arg: null });
    await settle();
    const text = root.textContent;
    ok(text.includes(EMPTY_TITLE) && text.includes(EMPTY_LINE), "the words: " + text);
    ok(root.querySelector("svg"), "the glyph");
    eq(mounted, 0, "the live view is not mounted: nothing to ask the cameras");
    eq(root.querySelectorAll(".cams, .cam-feed, .cam-img, video, img").length, 0, "no feed, no picture, nothing to be broken");
    ok(!SAYS_NOTHING_WRONG.test(text), "no reason, no REC, no Off: " + text);
    eq(typeof un, "function", "an unmount");
    un();
    root.remove();
  }],

  ["the screen with footage is the live view: mounted on the same root, with the same options", async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const calls = [];
    const opts = { arg: null };
    const un = camerasView({ footage: fakeFootage(true), live: (r, o) => { calls.push([r, o]); r.appendChild(document.createElement("i")); return () => calls.push("unmounted"); }, ...timers() })(root, opts);
    await settle();
    eq([calls[0][0] === root, calls[0][1] === opts], [true, true], "the root and the options go through");
    ok(!root.textContent.includes(EMPTY_LINE), "no empty state");
    un();
    eq(calls[1], "unmounted", "leaving the tab unmounts it");
    un();
    eq(calls.length, 2, "once");
    root.remove();
  }],

  ["no answer yet: nothing is drawn until the look answers, so footage never flashes the empty state", async () => {
    const f = fakeFootage(null);
    f.hold = true;
    const root = document.createElement("div");
    document.body.appendChild(root);
    let mounted = 0;
    const un = camerasView({ footage: f, live: () => { mounted++; return () => {}; }, ...timers() })(root, {});
    eq(root.children.length, 0, "blank while it asks");
    eq(f.checks, 1, "it asked as the tab mounted");
    f.value = true;
    f.held();
    await settle();
    eq(mounted, 1, "footage: the live view");
    ok(!root.textContent.includes(EMPTY_LINE), "and never the empty state");
    un();
    const g = fakeFootage(null);
    g.hold = true;
    const root2 = document.createElement("div");
    const un2 = camerasView({ footage: g, live: () => () => {}, ...timers() })(root2, {});
    g.value = null;                                  // the look failed: nothing is known
    g.held();
    await settle();
    ok(root2.textContent.includes(EMPTY_LINE), "a look that tells nothing: the empty state, never a blank screen");
    un2();
    root.remove();
  }],

  ["a known answer is drawn at once, and the look that follows corrects it", async () => {
    const f = fakeFootage(false);
    f.hold = true;
    const root = document.createElement("div");
    let mounted = 0;
    const un = camerasView({ footage: f, live: (r) => { mounted++; r.appendChild(document.createElement("i")); return () => {}; }, ...timers() })(root, {});
    ok(root.textContent.includes(EMPTY_LINE), "what it knew, at once");
    f.value = true;
    f.held();
    await settle();
    eq(mounted, 1, "and then the live view");
    ok(!root.textContent.includes(EMPTY_LINE), "in its place");
    un();
  }],

  ["empty, it looks again every RECHECK_MS and becomes the live view by itself when a camera is recording", async () => {
    const f = fakeFootage(false);
    const t = timers();
    const root = document.createElement("div");
    document.body.appendChild(root);
    let mounted = 0, unmounted = 0;
    const un = camerasView({ footage: f, live: (r) => { mounted++; r.appendChild(Object.assign(document.createElement("div"), { className: "cams" })); return () => { unmounted++; }; }, ...t })(root, {});
    await settle();
    eq(t.running().map((x) => x.ms), [RECHECK_MS], "a look every 5 s");
    t.ticks[0].fn();
    await settle();
    eq(mounted, 0, "still none");
    ok(root.textContent.includes(EMPTY_LINE), "still the empty state");
    f.value = true;
    t.ticks[0].fn();
    await settle();
    eq(mounted, 1, "the clips came");
    eq(root.children.length, 1, "the empty state replaced, not added to");
    ok(root.querySelector(".cams"), "by the live view");
    eq(t.running().length, 0, "and the looking stops");
    un();
    eq(unmounted, 1, "leaving unmounts the live view");
    root.remove();
  }],

  ["leaving the tab stops the looking, and a late answer draws nothing", async () => {
    const f = fakeFootage(false);
    f.hold = true;
    const t = timers();
    const root = document.createElement("div");
    let mounted = 0;
    const un = camerasView({ footage: f, live: () => { mounted++; return () => {}; }, ...t })(root, {});
    f.value = true;
    un();
    eq(t.running().length, 0, "the repeating look is cancelled");
    f.held();
    await settle();
    eq([mounted, root.children.length], [0, 1], "nothing drawn after leaving (the empty state it had stays, unseen)");
  }],

  ["the real live view, handed over to: three feeds, the timeline and the playback bar", async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const asked = [];
    const t = timers();
    const get = (path) => { asked.push(path.split("?")[0]); return Promise.resolve(path.startsWith("/api/cams/clips") ? { clips: [], events: [] } : UP); };
    const un = camerasView({ footage: fakeFootage(true), live: liveCamerasView, ...timers() })(root, {
      get, every: t.every, later: t.later, stopEvery: t.cancel });
    await settle();
    await settle();
    eq(root.querySelectorAll(".cam-feed").length, 3, "front, rear and cabin");
    ok(root.querySelector(".cam-timeline") && root.querySelector(".cam-playbar"), "the timeline and the playback bar");
    ok(asked.includes("/api/cams") && asked.includes("/api/cams/clips"), "its own polls: " + asked);
    ok(!root.textContent.includes(EMPTY_LINE), "no empty state");
    un();
    root.remove();
  }],

  ["register: the door's `cameras` view, and nothing else", () => {
    const D = { cards: {}, views: { work: () => 1 } };
    registerView(D, { footage: fakeFootage(false), ...timers() });
    eq(Object.keys(D.views).sort(), ["cameras", "work"], "one more view");
    eq(typeof D.views.cameras, "function", "a plain mount: main.js's `mount(root, { arg })`");
    eq(Object.keys(D.cards), [], "no card");
    const root = document.createElement("div");
    const un = D.views.cameras(root, { arg: null });
    ok(root.textContent.includes(EMPTY_LINE), "it draws");
    un();
  }],
];
