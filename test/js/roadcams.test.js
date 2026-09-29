// Road cameras: the rules, then the screen mounted for real against a stubbed
// API. Nothing here reaches the internet: the list, the stills and the pins
// are answered by the stubs below, and the construction cameras' frames point
// at about:blank rather than at TrueLook.
import { eq, ok } from "./assert.js";
import roadcams from "../js/views/roadcams.js";
import { store, api } from "../js/core.js";
import { WHILE_MOVING, WHY_PARKED, layoutFor, ageText, isStale, nextRefreshMs,
         arrange, firstPinnedStill, netState, feedLine } from "../js/roadcams.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Date.now() / 1000;

function cam(id, route, name, extra) {
  return Object.assign({ id, kind: "still", name, route, direction: "North", place: "Marina",
                         lat: 36.6, lon: -121.8, image_url: `https://cwwp2.dot.ca.gov/x/${id}.jpg`,
                         stream_url: null, updated_minutes: 2, source: "Caltrans District 5" }, extra);
}
function embed(n) {
  return { id: `imjin-${n}`, kind: "embed", name: `Imjin Parkway — camera ${n}`,
           route: "Imjin Parkway", url: `about:blank#imjin-${n}`,
           source: "Imjin Parkway Widening project (TrueLook)",
           note: "A construction camera. The project was due to finish in June 2026." };
}

// The server's answer, in its own shape (lib/roadcams.py listing()).
function listing(over) {
  const cams = [
    cam("us101airportblvd", "US-101", "US-101 : Airport Blvd", { place: "Salinas" }),
    cam("sr1lightfighterdrive", "SR-1", "SR-1 : Lightfighter Drive", { place: "Seaside" }),
    cam("sr1imjinparkway", "SR-1", "SR-1 : Imjin Parkway"),
    cam("sr68reservationroadriverroad", "SR-68", "SR-68 : Reservation Road / River Road",
        { place: "Salinas", direction: "West" }),
    cam("sr156westus101", "SR-156", "SR-156 West : US-101", { place: "Salinas" }),
    cam("sr183salinasst", "SR-183", "SR-183 : Salinas St", { place: "Salinas" }),
    embed(1), embed(2), embed(3),
  ];
  return Object.assign({
    cameras: cams,
    groups: [
      { id: "US-101", label: "US-101", ids: ["us101airportblvd"] },
      { id: "SR-1", label: "SR-1", ids: ["sr1lightfighterdrive", "sr1imjinparkway"] },
      { id: "SR-68", label: "SR-68", ids: ["sr68reservationroadriverroad"] },
      { id: "SR-156", label: "SR-156", ids: ["sr156westus101"] },
      { id: "SR-183", label: "SR-183", ids: ["sr183salinasst"] },
      { id: "imjin", label: "Imjin Parkway", ids: ["imjin-1", "imjin-2", "imjin-3"] },
    ],
    pins: ["sr1imjinparkway", "sr1lightfighterdrive", "sr68reservationroadriverroad",
           "imjin-1", "imjin-2", "imjin-3"],
    pins_default: true, pins_missing: [],
    default_pins: ["sr1imjinparkway", "sr1lightfighterdrive", "sr68reservationroadriverroad",
                   "imjin-1", "imjin-2", "imjin-3"],
    feed: { source: "Caltrans District 5", fetched_at: now() - 7200, age: 7200,
            from_cache: true, error: null, offline: false, count: 6 },
    net: { ok_at: now() - 5, fail_at: null, error: null },
    image_ttl: 60, max_pins: 12,
  }, over);
}

const svg = (label) => new Blob([`<svg xmlns="http://www.w3.org/2000/svg" width="320" height="260">`
  + `<rect width="320" height="260" fill="#345"/><text x="10" y="130" fill="#fff">${label}</text></svg>`],
  { type: "image/svg+xml" });

// Mount the screen with the API stubbed; `net` decides how stills answer.
async function withScreen(opts, fn) {
  const was = { roadcams: api.roadcams, roadcamImage: api.roadcamImage,
                saveRoadcamPins: api.saveRoadcamPins };
  const asked = [];
  const net = { mode: opts.mode || "ok", age: 120, lists: 0 };
  api.roadcams = async () => { net.lists++; return listing(opts.over); };
  api.roadcamImage = async (id, cached) => {
    asked.push(cached ? `${id}?cached` : id);
    if (net.mode === "offline") {
      const e = new Error("No connection: [Errno 101] Network is unreachable");
      e.offline = true;
      e.saved = true;
      throw e;
    }
    if (net.mode === "refused" && !cached) {
      const e = new Error("Caltrans answered 503 Service Unavailable");
      e.status = 502;
      e.saved = true;
      throw e;
    }
    if (net.mode === "refused") {
      return { blob: svg(id), age: 7200, modified: now() - 7200, source: "saved", at: now() };
    }
    if (net.mode === "noage") return { blob: svg(id), age: null, modified: null, source: "caltrans", at: now() };
    return { blob: svg(id), age: net.age, modified: now() - net.age, source: "caltrans", at: now() };
  };
  api.saveRoadcamPins = opts.save || (async (pins) => listing({ pins, pins_default: false }));
  for (const id of ["toasts", "modal-host"]) {
    if (!document.getElementById(id)) {
      const d = document.createElement("div");
      d.id = id;
      document.body.appendChild(d);
    }
  }
  const clock = { t: 1e12 };
  const wasClock = store.clock;
  store.clock = () => clock.t;
  store.motionLatched = false;
  store.stillSince = null;
  store.lastMoving = false;
  store.live = opts.live || { connected: true, values: { SPEED: 0, RPM: 0 } };
  store.emit("live");
  const root = document.createElement("div");
  root.style.width = "1300px";
  document.body.appendChild(root);
  const unmount = roadcams(root);
  try {
    for (let i = 0; i < 60 && !root.querySelector(".rc-sec[data-sec='SR-1'] .rc-tile"); i++) await wait(50);
    await fn(root, { asked, net, clock });
  } finally {
    unmount();
    root.remove();
    Object.assign(api, was);
    store.clock = wasClock;
    store.live = null;
    store.lastMoving = false;
    store.motionLatched = false;
    store.stillSince = null;
    store.emit("live");
  }
}

const ids = (el) => [...el.querySelectorAll(":scope > .rc-tile")].map((t) => t.dataset.id);
const pinnedGrid = (root) => root.querySelector(".rc-sec[data-sec='pinned'] .rc-grid");
const tileIn = (root, sec, id) => root.querySelector(`.rc-sec[data-sec='${sec}'] .rc-tile[data-id='${id}']`);
async function until(cond, ms = 3000) {
  for (let t = 0; t < ms && !cond(); t += 50) await wait(50);
  return cond();
}

export default [
  // ---- the rules -------------------------------------------------------------
  ["the driving rule is one value, and it is the owner's default: one still", () => {
    eq(WHILE_MOVING, "one");
    eq([layoutFor(false), layoutFor(true), layoutFor(true, "none"), layoutFor(true, "all")],
       ["all", "one", "none", "all"]);
  }],
  ["an age is said from the timestamp, and never as live", () => {
    eq([ageText(20), ageText(120), ageText(59 * 60), ageText(3 * 3600), ageText(3 * 86400), ageText(null)],
       ["under a minute ago", "2 min ago", "59 min ago", "3 h ago", "3 days ago", "age unknown"]);
    ok(![0, 5, 60, 7200].some((s) => /live/i.test(ageText(s))), "never 'live'");
  }],
  ["a picture is stale after three of its own updates, and never under ten minutes", () => {
    const c = { updated_minutes: 2 };
    eq([isStale(300, c), isStale(700, c), isStale(null, c)], [false, true, false]);
    eq(isStale(800, { updated_minutes: 5 }), false, "a five-minute camera has fifteen");
  }],
  ["a still is asked for on the camera's own clock, and never more than once a minute", () => {
    const c = { updated_minutes: 2 };
    eq(nextRefreshMs(c, 0), 135000, "just uploaded: the next is due in two minutes and a margin");
    eq(nextRefreshMs(c, 30), 105000, "timed for the next upload");
    eq(nextRefreshMs(c, 110), 60000, "never sooner than a minute");
    eq(nextRefreshMs(c, 3600), 60000, "a late camera: once a minute");
    eq(nextRefreshMs(c, null), 120000, "age unknown: one period");
    eq(nextRefreshMs({ updated_minutes: 0.2 }, 0), 75000, "a camera claiming seconds still waits a minute");
  }],
  ["pinned first, in the owner's order, then the groups as the server ordered them", () => {
    const d = listing();
    const a = arrange(d);
    eq(a.pinned.map((p) => p.id), d.pins);
    eq(a.groups.map((g) => g.label), ["US-101", "SR-1", "SR-68", "SR-156", "SR-183", "Imjin Parkway"]);
    eq(firstPinnedStill(d).id, "sr1imjinparkway");
    eq(firstPinnedStill(d, ["imjin-1", "sr68reservationroadriverroad"]).id,
       "sr68reservationroadriverroad", "a construction camera is skipped: it is a frame");
    eq(firstPinnedStill(d, ["imjin-2"]), null);
  }],
  ["a pin missing from the list is kept and named", () => {
    const d = listing({ pins: ["sr68yorkroad", "sr1imjinparkway"],
                        pins_missing: [{ id: "sr68yorkroad", name: "SR-68 : York Road" }] });
    const a = arrange(d);
    eq(a.pinned.map((p) => [p.id, p.cam ? "listed" : p.name]),
       [["sr68yorkroad", "SR-68 : York Road"], ["sr1imjinparkway", "listed"]]);
  }],
  ["the list's age and any trouble are said under the title", () => {
    eq(feedLine(listing()), "Caltrans District 5 · 6 cameras in Monterey County · list from 2 h ago");
    const off = listing({ feed: { source: "Caltrans District 5", fetched_at: now() - 30000, age: 30000,
                                  from_cache: true, error: "No connection (x)", offline: true, count: 6 } });
    eq(feedLine(off), "Caltrans District 5 · 6 cameras in Monterey County · No connection · list from 8 h ago");
    ok(/not been downloaded/.test(feedLine(listing({ feed: { fetched_at: null, error: "No connection" } }))),
       "no list at all says so");
    eq([netState(listing()), netState(listing({ net: { ok_at: 1, fail_at: 2 } })), netState({})],
       ["online", "offline", "unknown"]);
  }],

  // ---- the screen -------------------------------------------------------------
  ["parked: the pinned cameras first, then every road in order, then Imjin Parkway", () =>
    withScreen({}, async (root) => {
      eq(ids(pinnedGrid(root)), listing().pins, "the pinned section, in pin order");
      eq([...root.querySelectorAll(".rc-groups .rc-sec-t")].map((e) => e.textContent),
         ["US-101", "SR-1", "SR-68", "SR-156", "SR-183", "Imjin Parkway"]);
      eq([...root.querySelectorAll(".rc-jump .chip")].map((e) => e.textContent),
         ["Pinned", "US-101", "SR-1", "SR-68", "SR-156", "SR-183", "Imjin Parkway"]);
      eq(ids(root.querySelector(".rc-sec[data-sec='SR-1'] .rc-grid")),
         ["sr1lightfighterdrive", "sr1imjinparkway"]);
      ok(/6 cameras in Monterey County/.test(root.querySelector(".rc-sub").textContent), "the list line");
      eq(root.querySelector(".rc-driving").hidden, true, "no driving tile while parked");
    })],

  ["each tile names its camera, its direction and its picture's age, never 'live'", () =>
    withScreen({}, async (root, { asked }) => {
      const t = tileIn(root, "pinned", "sr1imjinparkway");
      ok(await until(() => t.dataset.has === "1"), "the picture arrived");
      eq(t.querySelector(".rc-route").textContent, "SR-1");
      eq(t.querySelector(".rc-nm").textContent, "Imjin Parkway");
      eq(t.querySelector(".rc-where").textContent, "Marina · North");
      eq(t.querySelector(".rc-age").textContent, "2 min ago");
      // The stills are pictures with a time on them. Only a construction
      // camera -- a player -- is ever called live, and it says whose clock.
      ok(![...root.querySelectorAll(".rc-tile[data-kind='still']")].some((el) => /live/i.test(el.textContent)),
         "no Caltrans still says live");
      const twice = tileIn(root, "SR-1", "sr1imjinparkway");
      ok(await until(() => twice.dataset.has === "1"), "the same camera in its group has the picture too");
      eq(asked.filter((a) => a === "sr1imjinparkway").length, 1, "and it cost one request, not two");
    })],

  ["with no connection the last picture stays up, with its time and 'No connection'", () =>
    withScreen({}, async (root, { net }) => {
      const t = tileIn(root, "pinned", "sr1lightfighterdrive");
      ok(await until(() => t.dataset.has === "1"), "the picture arrived");
      const src = t.querySelector(".rc-img").src;
      net.mode = "offline";
      // Due now rather than in a minute, then off screen and back, which is
      // the path that asks again.
      t.__tile.still.nextAt = 0;
      t.style.display = "none";
      document.dispatchEvent(new Event("scroll"));
      await wait(200);
      t.style.display = "";
      document.dispatchEvent(new Event("scroll"));
      ok(await until(() => t.dataset.state === "offline"), "the next ask found no connection");
      eq(t.dataset.state, "offline");
      eq(t.querySelector(".rc-flag").textContent, "No connection");
      eq(t.querySelector(".rc-flag").hidden, false);
      eq(t.querySelector(".rc-img").src, src, "the last picture is still the one shown");
      eq(t.querySelector(".rc-age").textContent, "2 min ago", "with its own time");
    })],

  ["the construction cameras wait for a tap, then are framed, sandboxed", () =>
    withScreen({}, async (root) => {
      const t = tileIn(root, "pinned", "imjin-1");
      const poster = t.querySelector(".rc-poster");
      await wait(300);
      eq(t.querySelector("iframe"), null, "no player until asked: it uses mobile data");
      eq([poster.disabled, poster.textContent.includes("Tap to load the live view")], [false, true]);
      eq(t.querySelector(".rc-age").textContent, "live embed — its own clock");
      poster.click();
      ok(await until(() => !!t.querySelector("iframe")), "a frame once tapped, parked and online");
      const f = t.querySelector("iframe");
      eq(f.getAttribute("sandbox"), "allow-scripts allow-same-origin");
      eq(f.getAttribute("referrerpolicy"), "no-referrer");
      ok(/construction camera/i.test(t.textContent) && /June 2026/.test(t.textContent),
         "and it says it is the project's construction camera, which may go");
    })],

  // The stills fail too: a still that arrives is proof of a connection, and
  // would rightly bring the frames back.
  ["no frame at all when the server last found no internet", () =>
    withScreen({ mode: "offline",
                 over: { net: { ok_at: now() - 60, fail_at: now() - 5, error: "no route" } } },
      async (root) => {
        const t = tileIn(root, "pinned", "imjin-2");
        await wait(300);
        t.querySelector(".rc-poster").click();
        await wait(100);
        eq(t.querySelector("iframe"), null, "not even when tapped");
        eq(t.querySelector(".rc-poster").disabled, true);
        ok(/No connection/.test(t.querySelector(".rc-estate").textContent), "and it says why");
      })],

  ["moving: one small fixed tile, the first pinned Caltrans camera, and the rest greyed with the reason", () =>
    withScreen({}, async (root) => {
      await wait(200);
      for (const p of root.querySelectorAll(".rc-sec[data-sec='pinned'] .rc-poster")) p.click();
      ok(await until(() => !!root.querySelector(".rc-sec[data-sec='pinned'] iframe")), "players loaded by tap");
      store.live = { connected: true, values: { SPEED: 55, RPM: 2400 } };
      store.emit("live");
      eq(root.querySelector(".rc-parked").hidden, true, "the grid is gone");
      eq(root.querySelector(".rc-driving").hidden, false);
      const tiles = root.querySelectorAll(".rc-driving .rc-tile");
      eq(tiles.length, 1, "exactly one tile");
      eq(tiles[0].dataset.id, "sr1imjinparkway", "the first pinned Caltrans camera");
      ok(await until(() => tiles[0].dataset.has === "1"), "with its picture");
      eq(tiles[0].querySelector(".rc-age").textContent, "2 min ago", "and its age");
      eq(root.querySelectorAll("iframe").length, 0, "no construction camera frames while moving");
      const edit = root.querySelector(".rc-editbtn");
      eq([edit.hidden, edit.disabled, edit.title], [false, true, WHY_PARKED], "Edit pins greys, never hides");
      const locked = [...root.querySelectorAll(".rc-locked .btn")];
      eq(locked.map((b) => [b.textContent, b.disabled, b.title]),
         [["All cameras", true, WHY_PARKED], ["Edit pins", true, WHY_PARKED],
          ["Imjin Parkway cameras", true, WHY_PARKED]]);
      ok(root.querySelector(".rc-driving").textContent.includes(WHY_PARKED), "the reason is on screen");
    })],

  ["a 40 s red light keeps the one tile; a minute stopped brings the grid back", () =>
    withScreen({}, async (root, { clock }) => {
      const at = (secs, speed) => {
        clock.t = 1e12 + secs * 1000;
        store.live = { connected: true, values: { SPEED: speed, RPM: speed ? 2200 : 800 } };
        store.emit("live");
      };
      at(0, 50);
      const tile = root.querySelector(".rc-driving .rc-tile");
      at(1, 0);
      at(41, 0);
      eq(root.querySelector(".rc-parked").hidden, true, "40 s at the light: still the driving layout");
      ok(root.querySelector(".rc-driving .rc-tile") === tile, "the same tile, not rebuilt at the stop");
      eq(root.querySelectorAll("iframe").length, 0, "and no players");
      eq(root.querySelector(".rc-editbtn").disabled, true, "Edit pins still greyed");
      at(61, 0);
      eq(root.querySelector(".rc-parked").hidden, false, "a minute stopped: the grid is back");
      eq(root.querySelector(".rc-editbtn").disabled, false);
    })],

  ["a hand-off while stopped counts as moving", () =>
    withScreen({}, async (root, { clock }) => {
      eq(root.querySelector(".rc-parked").hidden, false, "parked, never seen moving");
      clock.t += 5000;
      store.live = { connected: false, values: {}, handover: true, status: "yielded" };
      store.emit("live");
      eq(root.querySelector(".rc-parked").hidden, true, "the adapter is lent out: motion unknown");
      eq(root.querySelectorAll(".rc-driving .rc-tile").length, 1);
      clock.t += 15000;
      store.live = { connected: true, values: { SPEED: 0, RPM: 800 } };
      store.emit("live");
      eq(root.querySelector(".rc-parked").hidden, true, "and for a minute after it ends");
    })],

  ["an adapter that drops out mid-drive keeps the driving layout", () =>
    withScreen({ live: { connected: true, values: { SPEED: 60, RPM: 2500 } } }, async (root) => {
      store.live = { connected: false, values: {} };
      store.emit("live");
      eq(root.querySelector(".rc-parked").hidden, true);
      eq(root.querySelectorAll(".rc-driving .rc-tile").length, 1);
    })],

  ["a list reload leaves a loaded player, the tiles and the driving tile alone", () =>
    withScreen({}, async (root, { net }) => {
      await wait(200);
      const t = tileIn(root, "pinned", "imjin-1");
      t.querySelector(".rc-poster").click();
      ok(await until(() => !!t.querySelector("iframe")), "loaded");
      const frame = t.querySelector("iframe");
      const still = tileIn(root, "pinned", "sr1imjinparkway");
      const drive = root.querySelector(".rc-driving .rc-tile");
      const lists = net.lists;
      window.dispatchEvent(new Event("online"));
      ok(await until(() => net.lists > lists), "the list was read again");
      await wait(100);
      ok(tileIn(root, "pinned", "imjin-1") === t && t.querySelector("iframe") === frame,
         "the same player, not taken down and put up again");
      ok(tileIn(root, "pinned", "sr1imjinparkway") === still, "the same still tile");
      ok(root.querySelector(".rc-driving .rc-tile") === drive, "the same driving tile");
    })],

  ["a picture with no timestamp says 'age unknown', in amber", () =>
    withScreen({ mode: "noage" }, async (root) => {
      const t = tileIn(root, "pinned", "sr1imjinparkway");
      ok(await until(() => t.dataset.has === "1"), "the picture arrived");
      eq([t.querySelector(".rc-age").textContent, t.dataset.state], ["age unknown", "unknown"]);
      // The runner page does not load the stylesheet, so the rule is read.
      const css = await (await fetch("css/roadcams.css")).text();
      ok(/\[data-state="unknown"\] \.rc-age[^{]*\{[^}]*--warn/.test(css), "and the stylesheet draws it amber");
    })],

  ["after a refusal the kept picture is shown, with its real age, as the last good one", () =>
    withScreen({ mode: "refused" }, async (root, { asked }) => {
      const t = tileIn(root, "pinned", "sr1lightfighterdrive");
      ok(await until(() => t.dataset.has === "1"), "the kept picture is up");
      ok(asked.includes("sr1lightfighterdrive?cached"), "asked for the copy on disk");
      eq(t.querySelector(".rc-flag").textContent, "Last good image");
      eq(t.querySelector(".rc-age").textContent, "2 h ago", "its own age, not the time it was shown");
      eq(t.dataset.state, "error");
    })],

  ["a list the disk would not keep says it is in memory only", () => {
    const d = listing();
    d.feed.warning = "The camera list could not be kept on disk (No space left on device)";
    ok(/kept in memory only/.test(feedLine(d)), feedLine(d));
  }],

  ["a pin that left Caltrans' list is shown as no longer in it, and saved with the rest", () => {
    let sent = null;
    return withScreen({ over: { pins: ["sr68yorkroad", "sr1imjinparkway"], pins_default: false,
                                pins_missing: [{ id: "sr68yorkroad", name: "SR-68 : York Road" }] },
                        save: async (pins) => { sent = pins; return listing({ pins, pins_default: false }); } },
      async (root) => {
        const gone = tileIn(root, "pinned", "sr68yorkroad");
        ok(/No longer in Caltrans' list/.test(gone.textContent), "said plainly");
        ok(/SR-68 : York Road/.test(gone.textContent), "under the name it had");
        root.querySelector(".rc-editbtn").click();
        const pin = tileIn(root, "US-101", "us101airportblvd").querySelector(".rc-pinbtn");
        pin.click();
        [...root.querySelectorAll(".rc-actions .btn")].find((b) => b.textContent === "Done").click();
        await until(() => sent !== null);
        eq(sent, ["sr68yorkroad", "sr1imjinparkway", "us101airportblvd"], "the missing pin goes with the rest");
      });
  }],

  ["pins are edited while parked, and saved only on Done", () => {
    let sent = null;
    return withScreen({ save: async (pins) => { sent = pins; return listing({ pins, pins_default: false }); } },
      async (root) => {
        root.querySelector(".rc-editbtn").click();
        eq(root.querySelector(".rc").dataset.editing, "1");
        const pin = tileIn(root, "US-101", "us101airportblvd").querySelector(".rc-pinbtn");
        eq(pin.textContent, "Pin");
        pin.click();
        eq(pin.getAttribute("aria-pressed"), "true");
        eq(sent, null, "nothing is saved yet");
        const unpin = [...tileIn(root, "pinned", "sr1imjinparkway").querySelectorAll(".rc-edit .btn")]
          .find((b) => b.textContent === "Unpin");
        unpin.click();
        const later = [...tileIn(root, "pinned", "sr1lightfighterdrive").querySelectorAll(".rc-edit .btn")]
          .find((b) => b.textContent === "↓ Later");
        later.click();
        [...root.querySelectorAll(".rc-actions .btn")].find((b) => b.textContent === "Done").click();
        await until(() => sent !== null);
        eq(sent, ["sr68reservationroadriverroad", "sr1lightfighterdrive", "imjin-1", "imjin-2",
                  "imjin-3", "us101airportblvd"]);
        await until(() => root.querySelector(".rc").dataset.editing === "0");
        eq(ids(pinnedGrid(root))[0], "sr68reservationroadriverroad", "the new order is on screen");
      });
  }],

  ["'Back to the commute' puts the default pins in the draft, and saves nothing until Done", () => {
    let sent = null;
    return withScreen({ over: { pins: ["us101airportblvd"], pins_default: false },
                        save: async (pins) => { sent = pins; return listing({ pins, pins_default: false }); } },
      async (root) => {
        eq(ids(pinnedGrid(root)), ["us101airportblvd"]);
        root.querySelector(".rc-editbtn").click();
        [...root.querySelectorAll(".rc-actions .btn")].find((b) => b.textContent === "Back to the commute").click();
        eq(ids(pinnedGrid(root)), listing().default_pins, "the commute is back on screen");
        eq(sent, null, "and nothing was sent");
        [...root.querySelectorAll(".rc-actions .btn")].find((b) => b.textContent === "Cancel").click();
        eq(ids(pinnedGrid(root)), ["us101airportblvd"], "Cancel leaves the saved pins as they were");
        eq(sent, null);
      });
  }],
];
