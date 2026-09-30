// CarPlay and Android Auto, the meetup demo's two phone-projection takeovers
// (Task 5 of doc/design/2026-09-30-meetup-demo-plan.md).
//
// Everything they depend on is injected, so these run with no map, no player
// and no page to go back to: a fake radio that records what it was asked, a
// fake map that records where it was mounted and whether it was let go, and a
// counter for the way back. Nothing here makes a sound.
import { eq, ok } from "./assert.js";
import { store } from "../js/core.js";
import { carplayView, register as registerCarPlay } from "../demo/js/views/carplay.js";
import { androidAutoView, register as registerAA } from "../demo/js/views/androidauto.js";
import { TAKEOVER, bannerOf, mss, openScreen } from "../demo/js/projection.js";

// 7:12 PM local, whatever zone the runner is in: the meetup is 6 to 8.
const NOW = new Date(2026, 8, 30, 19, 12, 30).getTime();

function fakeRadio() {
  const subs = new Set();
  const calls = [];
  const r = {
    tracks: [
      { title: "First Song", artist: "Ryan R. Hughes", file: "1.mp3" },
      { title: "Second Song", artist: "Ryan R. Hughes", file: "2.mp3" },
      { title: "Third Song", artist: "Ryan R. Hughes", file: "3.mp3" },
    ],
    index: 0, playing: false,
    play(i) { calls.push(["play", i]); if (i !== undefined) r.index = i; r.playing = true; },
    pause() { calls.push(["pause"]); r.playing = false; },
    toggle() { calls.push(["toggle"]); r.playing = !r.playing; },
    next() { calls.push(["next"]); },
    prev() { calls.push(["prev"]); },
    seek(s) { calls.push(["seek", s]); },
    position: () => 0,
    duration: () => 180,
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    push(state) { for (const fn of [...subs]) fn(state); },
    subs, calls,
  };
  return r;
}

function fakeMap() {
  const m = { calls: [], destroyed: 0 };
  m.mountMap = (el, opts) => {
    m.calls.push({ el, style: opts && opts.style });
    return { destroy() { m.destroyed++; } };
  };
  return m;
}

function mount(view, { arg = null, radio = fakeRadio() } = {}) {
  const map = fakeMap();
  const t = { radio, map, backs: 0 };
  const deps = { radio, mountMap: map.mountMap, back: () => { t.backs++; }, now: () => NOW };
  t.root = document.createElement("div");
  document.body.appendChild(t.root);
  const un = view(deps)(t.root, { arg });
  t.$ = (sel) => t.root.querySelector(sel);
  t.$$ = (sel) => [...t.root.querySelectorAll(sel)];
  t.screen = () => t.$(".proj").dataset.screen;
  t.done = () => { if (un) un(); t.root.remove(); };
  return t;
}

const tap = (el, what) => { ok(el, `${what} is on the screen`); el.click(); };
const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");

function live(next, extra) {
  store.live = {
    connected: true, simulated: true, values: { SPEED: 88 },
    demo: Object.assign({
      t: 60, loop_secs: 900, lat: 36.7, lon: -121.8, heading: 12, street: "CA-1 N",
      route_m: 5234, remaining_m: 182000, eta: NOW / 1000 + 7500, next,
      parked: false, scene: "drive", event: null,
    }, extra),
  };
  store.emit("live");
}
function unlive() { store.live = null; store.emit("live"); }

const TURN = { type: "turn", modifier: "right", street: "Reservation Rd",
               instruction: "Turn right onto Reservation Rd", in_m: 1300 };

// Every text node under `root` that names either platform.
function namings(root) {
  const out = [];
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const n = walk.currentNode;
    if (/CarPlay|Android Auto/.test(n.nodeValue)) out.push(n);
  }
  return out;
}

const VIEWS = [["CarPlay", carplayView], ["Android Auto", androidAutoView]];

export default [
  // ---------------------------------------------------------------- the takeover
  ...VIEWS.map(([name, view]) => [`${name} takes the whole screen while mounted and gives the bars back`, () => {
    ok(!document.body.classList.contains(TAKEOVER), "no takeover before");
    const t = mount(view);
    try {
      ok(document.body.classList.contains(TAKEOVER), "the body carries the takeover class");
    } finally { t.done(); }
    ok(!document.body.classList.contains(TAKEOVER), "and loses it on unmount");
  }]),

  // ---------------------------------------------------------------- home
  ["CarPlay's home grid is the seven apps and an OmaCar tile that goes back", () => {
    const t = mount(carplayView);
    try {
      eq(t.screen(), "home");
      eq(t.$$(".proj-grid [data-app]").map((e) => e.dataset.app),
         ["maps", "nowplaying", "phone", "messages", "podcasts", "calendar", "settings", "omacar"]);
      eq(t.$$(".cp-dock .cp-recent").length, 3, "three recent apps in the dock");
      tap(t.$('.proj-grid [data-app="omacar"]'), "the OmaCar tile");
      eq(t.backs, 1, "back() called once");
    } finally { t.done(); }
  }],
  ["Android Auto opens on its dashboard: the map, the media and a suggestion", () => {
    const t = mount(androidAutoView);
    try {
      eq(t.screen(), "dashboard");
      ok(t.$(".aa-dash .proj-map"), "a map pane");
      ok(t.$(".aa-dash .aa-media"), "a media card");
      ok(t.$(".aa-dash .aa-suggest"), "a suggestion card");
    } finally { t.done(); }
  }],
  ["Android Auto's launcher has the apps and an OmaCar exit that goes back", () => {
    const t = mount(androidAutoView);
    try {
      tap(t.$(".aa-rail .aa-launch"), "the launcher button");
      eq(t.screen(), "launcher");
      const apps = t.$$(".proj-grid [data-app]").map((e) => e.dataset.app);
      ok(["maps", "nowplaying", "phone", "messages", "omacar"].every((a) => apps.includes(a)),
         `the launcher lists the apps: ${apps}`);
      eq(apps[apps.length - 1], "omacar", "the exit is last");
      tap(t.$('.proj-grid [data-app="omacar"]'), "the OmaCar exit");
      eq(t.backs, 1, "back() called once");
      eq(t.$$(".aa-rail .aa-recent").length, 3, "three recent apps on the rail");
    } finally { t.done(); }
  }],

  // ---------------------------------------------------------------- maps
  ["CarPlay's Maps mounts the map once, in the CarPlay style, and leaving it lets it go", () => {
    const t = mount(carplayView);
    try {
      eq(t.map.calls.length, 0, "no map on the home grid");
      tap(t.$('.proj-grid [data-app="maps"]'), "the Maps tile");
      eq(t.screen(), "maps");
      eq(t.map.calls.map((c) => c.style), ["carplay"]);
      ok(t.root.contains(t.map.calls[0].el) && t.map.calls[0].el.isConnected,
         "mounted into an element on the page");
      tap(t.$(".cp-dock .cp-home"), "the home button");
      eq([t.map.calls.length, t.map.destroyed], [1, 1]);
    } finally { t.done(); }
  }],
  ["and unmounting from CarPlay's Maps lets it go too", () => {
    const t = mount(carplayView, { arg: "maps" });
    eq(t.map.calls.map((c) => c.style), ["carplay"], "the hash arg opens Maps");
    t.done();
    eq(t.map.destroyed, 1);
  }],
  ["Android Auto's dashboard and its Maps each hold one map in the AA style, and let it go", () => {
    const t = mount(androidAutoView);
    try {
      eq(t.map.calls.map((c) => c.style), ["aa"], "the dashboard's map");
      tap(t.$(".aa-rail .aa-launch"), "the launcher button");
      eq(t.map.destroyed, 1, "the launcher lets the dashboard's map go");
      tap(t.$('.proj-grid [data-app="maps"]'), "the Maps icon");
      eq(t.screen(), "maps");
      eq(t.map.calls.map((c) => c.style), ["aa", "aa"]);
      tap(t.$(".aa-rail .aa-launch"), "the launcher button");
      eq(t.map.destroyed, 2, "leaving Maps lets its map go");
    } finally { t.done(); }
    eq(t.map.destroyed, t.map.calls.length, "every map mounted was destroyed");
  }],
  ...VIEWS.map(([name, view]) => [`${name}'s turn card is store.live.demo.next`, () => {
    live(TURN);
    const t = mount(view, { arg: "maps" });
    try {
      const card = t.$(".proj-turn");
      ok(card, "a turn card on Maps");
      eq(text(t.$(".proj-turn .proj-turn-dist")), "0.8 mi");
      ok(text(card).includes("Reservation Rd"), `the street: ${text(card)}`);
      live(Object.assign({}, TURN, { in_m: 150 }));
      eq(text(t.$(".proj-turn .proj-turn-dist")), "500 ft", "follows the live sample");
      ok(text(t.$(".proj-eta")).includes("113"), `the miles left: ${text(t.$(".proj-eta"))}`);
      unlive();
      ok(text(t.$(".proj-turn")).includes("Waiting for the demo drive"), "and says so with no drive");
      live(Object.assign({}, TURN, { in_m: 150 }));
      eq(text(t.$(".proj-turn .proj-turn-dist")), "500 ft", "and comes back when the drive does");
    } finally { t.done(); unlive(); }
  }]),
  ["with no next turn, the card says to carry on along the street", () => {
    live(null);
    const t = mount(carplayView, { arg: "maps" });
    try {
      ok(text(t.$(".proj-turn")).includes("CA-1 N"), text(t.$(".proj-turn")));
    } finally { t.done(); unlive(); }
  }],

  // ---------------------------------------------------------------- now playing
  ...VIEWS.map(([name, view]) => [`${name}'s Now Playing shows what the radio pushes, and drives it`, () => {
    const t = mount(view, { arg: "nowplaying" });
    try {
      eq(t.screen(), "nowplaying");
      eq(text(t.$(".pj-title")), "First Song", "painted from the radio before any push");
      t.radio.push({ index: 1, title: "Second Song", artist: "Ryan R. Hughes",
                     playing: true, position: 61.4, duration: 200 });
      eq(text(t.$(".pj-title")), "Second Song");
      eq(text(t.$(".pj-artist")), "Ryan R. Hughes");
      eq(text(t.$(".pj-elapsed")), "1:01");
      // CarPlay counts down what is left; Android Auto shows the length.
      if (view === carplayView) eq(text(t.$(".pj-remain")), "-2:18");
      else eq(text(t.$(".pj-dur")), "3:20");
      const w = parseFloat(t.$(".pj-fill").style.width);
      ok(w > 30 && w < 31.5, `the bar is 30.7% along, not ${w}`);
      eq(t.$(".pj-play").getAttribute("aria-label"), "Pause");
      tap(t.$(".pj-prev"), "previous");
      tap(t.$(".pj-play"), "play/pause");
      tap(t.$(".pj-next"), "next");
      eq(t.radio.calls, [["prev"], ["toggle"], ["next"]]);
      t.radio.push({ index: 1, title: "Second Song", artist: "Ryan R. Hughes",
                     playing: false, position: 62, duration: 200 });
      eq(t.$(".pj-play").getAttribute("aria-label"), "Play");
    } finally { t.done(); }
    eq(t.radio.subs.size, 0, "unmounting lets go of the radio");
  }]),
  ["Android Auto's dashboard media card follows the radio too", () => {
    const t = mount(androidAutoView);
    try {
      t.radio.push({ index: 2, title: "Third Song", artist: "Ryan R. Hughes",
                     playing: true, position: 30, duration: 120 });
      eq(text(t.$(".aa-media .pj-title")), "Third Song");
      eq(text(t.$(".aa-media .pj-artist")), "Ryan R. Hughes");
      eq(t.$(".aa-media .pj-play").getAttribute("aria-label"), "Pause");
    } finally { t.done(); }
    eq(t.radio.subs.size, 0);
  }],
  ["one radio: the song carries across both screens, and mounting never starts or stops it", () => {
    const radio = fakeRadio();
    radio.index = 2;
    radio.playing = true;
    const cp = mount(carplayView, { arg: "nowplaying", radio });
    eq(text(cp.$(".pj-title")), "Third Song");
    cp.done();
    const aa = mount(androidAutoView, { radio });
    eq(text(aa.$(".aa-media .pj-title")), "Third Song");
    eq(aa.$(".aa-media .pj-play").getAttribute("aria-label"), "Pause");
    aa.done();
    eq(radio.calls, [], "neither view touched the player on its own");
  }],

  // ---------------------------------------------------------------- phone, messages
  ...VIEWS.map(([name, view]) => [`${name}'s Phone and Messages are made up, and believable`, () => {
    const t = mount(view, { arg: "phone" });
    try {
      const faves = text(t.$(".proj-scr"));
      ok(["Mom", "Home", "Office"].every((n) => faves.includes(n)), `favourites: ${faves}`);
      openScreen("messages");
      eq(t.screen(), "messages");
      ok(text(t.$(".proj-scr")).includes("Leaving Los Banos now, see you at the meetup 🎉"),
         `the thread: ${text(t.$(".proj-scr"))}`);
    } finally { t.done(); }
  }]),

  // ---------------------------------------------------------------- the names
  ...VIEWS.map(([name, view]) => [`the name "${name}" appears only in the status area, and no maker's name at all`, () => {
    const t = mount(view);
    try {
      for (const scr of ["home", "launcher", "dashboard", "maps", "nowplaying", "phone",
                         "messages", "calendar", "podcasts", "settings"]) {
        openScreen(scr);
        const found = namings(t.root);
        ok(found.length >= 1, `${scr}: the status area names the platform`);
        for (const n of found) {
          ok(n.parentElement.closest(".proj-status"), `${scr}: "${n.nodeValue}" outside the status area`);
        }
        ok(!/\bApple\b|\bGoogle\b|\bSiri\b/.test(t.root.textContent), `${scr}: a maker's name on screen`);
      }
    } finally { t.done(); }
  }]),

  // ---------------------------------------------------------------- the clock, the router
  ...VIEWS.map(([name, view]) => [`${name}'s clock is the time now, h:mm`, () => {
    const t = mount(view);
    try { ok(text(t.$(".proj-status")).includes("7:12"), text(t.$(".proj-status"))); } finally { t.done(); }
  }]),
  ["openScreen moves the mounted view, and does nothing once it is gone", () => {
    const t = mount(carplayView);
    eq(openScreen("nowplaying"), true);
    eq(t.screen(), "nowplaying");
    t.done();
    eq(openScreen("maps"), false);
  }],

  // ---------------------------------------------------------------- the pieces
  ["the turn banner: imperial distances and the icon for the maneuver", () => {
    eq(bannerOf({ type: "turn", modifier: "right", street: "A St", in_m: 1300 }).distance, "0.8 mi");
    eq(bannerOf({ type: "turn", modifier: "right", street: "A St", in_m: 150 }).distance, "500 ft");
    eq(bannerOf({ type: "turn", modifier: "right", street: "A St", in_m: 20000 }).distance, "12 mi");
    eq(bannerOf({ type: "turn", modifier: "right", in_m: 10 }).icon, "turn-right");
    eq(bannerOf({ type: "turn", modifier: "slight left", in_m: 10 }).icon, "slight-left");
    eq(bannerOf({ type: "arrive", in_m: 10 }).icon, "arrive");
    eq(bannerOf({ type: "continue", modifier: "uturn", in_m: 10 }).icon, "uturn");
  }],
  ["m:ss", () => {
    eq([mss(0), mss(61.4), mss(3600), mss(NaN)], ["0:00", "1:01", "60:00", "0:00"]);
  }],
  ["register adds both screens to the demo's routable views", () => {
    const D = { views: {}, extraViews: [], tabRoots: {}, cards: {} };
    const deps = { radio: fakeRadio(), mountMap: fakeMap().mountMap, back() {} };
    registerCarPlay(D, deps);
    registerAA(D, deps);
    eq(D.extraViews.map((v) => [v.id, v.label, v.title, v.fast, typeof v.mount]),
       [["carplay", "CarPlay", "Apple CarPlay", true, "function"],
        ["androidauto", "Android Auto", "Android Auto", true, "function"]]);
  }],
  ["the sources carry no one else's artwork: no maker URLs, no SVG files, no images", async () => {
    for (const f of ["../demo/js/projection.js", "../demo/js/views/carplay.js",
                     "../demo/js/views/androidauto.js", "../demo/css/projection.css"]) {
      const r = await fetch(f);
      ok(r.ok, `${f} is there`);
      const src = await r.text();
      ok(!/apple\.com|google\.com|gstatic|googleapis/i.test(src), `${f}: a maker's URL`);
      ok(!/\.svg\b/i.test(src), `${f}: an SVG file`);
      ok(!/\.(png|jpe?g|webp|gif|ico|bmp|avif|heic)\b/i.test(src), `${f}: an image file`);
      ok(!/<img\b|new Image\b|url\(/i.test(src), `${f}: an image load`);
      // Omarchy Radio's own Now Playing screen (Task 4) owns .np-*; a class of
      // ours by that name would be styled by its sheet, and ours would leak
      // into its screen.
      ok(!/(^|[^\w-])\.?np-[a-z]/m.test(src), `${f}: a .np- class, which is the radio screen's`);
    }
  }],
];
