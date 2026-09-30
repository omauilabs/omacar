// The meetup demo's map (demo/js/map.js), its turn banner and ETA (demo/js/nav.js),
// and the two screens that use them (demo/js/views/navigation.js,
// demo/js/cards/navcard.js).
//
// The projection has to agree with tools/demo_map.py's to the half-metre, or
// the car is drawn beside the road it is on. The banner's words and distances
// are what a room full of people reads off a projector. The renderer is drawn
// on an OffscreenCanvas behind a stand-in for a <canvas>, so it runs with no
// layout; the screens are mounted with the map data missing, which they must
// survive, and with store.live set by hand.
import { eq, ok } from "./assert.js";
import { project, unproject, createMap, STYLES, loadMapData } from "../demo/js/map.js";
import { bannerOf, etaOf, iconOf, iconSvg, distanceText, streetLine } from "../demo/js/nav.js";
import navigationView, { mountMap, register as registerNav } from "../demo/js/views/navigation.js";
import { navCard, register as registerCard } from "../demo/js/cards/navcard.js";
import { store } from "../js/core.js";

const ORIGIN = [36.69611, -121.806059];
const settle = () => new Promise((r) => setTimeout(r, 0));

// A <canvas> as createMap sees one: its CSS size, and a 2D context from an
// OffscreenCanvas whose pixels the test can read back.
function stubCanvas(w, h) {
  const off = new OffscreenCanvas(w, h);
  return {
    off, clientWidth: w, clientHeight: h, style: {},
    get width() { return off.width; }, set width(v) { off.width = v; },
    get height() { return off.height; }, set height(v) { off.height = v; },
    getContext: (kind) => off.getContext(kind),
  };
}

// A few hundred metres of everything map.json holds, around the origin.
const DATA = {
  version: 1, origin: ORIGIN, bbox: [-2000, -2000, 2000, 2000],
  layers: {
    ocean: [[[-2000, -2000], [-300, -2000], [-300, 2000], [-2000, 2000], [-2000, -2000]]],
    water: [[[200, 200], [260, 200], [260, 260], [200, 260], [200, 200]]],
    rivers: [[[-250, -1000], [-200, 1000]]],
    roads: {
      motorway: [[[0, -2000], [0, 2000]]], trunk: [[[-300, 500], [2000, 500]]],
      primary: [[[-300, -500], [2000, -500]]], secondary: [[[100, -2000], [100, 2000]]],
      tertiary: [[[-300, 900], [2000, 900]]], minor: [[[-300, 50], [400, 50]], [[50, -200], [50, 300]]],
    },
  },
  labels: [{ x: 300, y: 500, text: "Salinas Road", class: "trunk" },
           { x: 0, y: 300, text: "1", class: "shield" }],
  route: [[0, -1500], [0, 0], [0, 1500]],
};

const px = (c, x, y) => Array.from(c.off.getContext("2d").getImageData(x, y, 1, 1).data);
const same = (a, b, tol = 6) => a.every((v, i) => Math.abs(v - b[i]) <= tol);

const DEMO = {
  t: 60, loop_secs: 900, lat: 36.70, lon: -121.80, heading: 12.5, street: "CA-1 N",
  route_m: 1200, remaining_m: 4184, eta: 0, parked: false, scene: "drive", event: null,
  next: { type: "turn", modifier: "right", street: "Reservation Rd",
          instruction: "Turn right onto Reservation Rd", in_m: 1300 },
  ima: { state: "idle", kw: 0 },
};
function withLive(demo, fn) {
  const before = store.live;
  store.live = demo ? { connected: true, simulated: true, t: Date.now() / 1000,
                        values: { SPEED: 48 }, demo } : null;
  return Promise.resolve(fn()).finally(() => { store.live = before; });
}

export default [
  // ---- the projection --------------------------------------------------------
  ["project is tools/demo_map.py's: metres east and north of the origin", () => {
    const [x, y] = project(ORIGIN[0] + 0.1, ORIGIN[1] + 0.1, ORIGIN);
    ok(Math.abs(x - 0.1 * Math.cos(ORIGIN[0] * Math.PI / 180) * 111320) < 1e-6, `x ${x}`);
    ok(Math.abs(y - 11054) < 1e-6, `y ${y}`);
    eq(project(...ORIGIN, ORIGIN), [0, 0]);
  }],
  ["project round-trips within 0.5 m at 50 km", () => {
    for (const [dn, de] of [[50000, 0], [0, 50000], [-35355, 35355], [35355, -35355]]) {
      const lat = ORIGIN[0] + dn / 110540;
      const lon = ORIGIN[1] + de / (Math.cos(ORIGIN[0] * Math.PI / 180) * 111320);
      const [x, y] = project(lat, lon, ORIGIN);
      ok(Math.hypot(x - de, y - dn) < 0.5, `projected ${x},${y} for ${de},${dn}`);
      const [la, lo] = unproject(x, y, ORIGIN);
      const back = project(la, lo, ORIGIN);
      ok(Math.hypot(back[0] - x, back[1] - y) < 0.5, `round trip ${back} for ${x},${y}`);
    }
  }],

  // ---- the banner and the ETA -----------------------------------------------
  ["distances are imperial: feet to 0.1 mi, one decimal to 10 mi, whole miles after", () => {
    eq(bannerOf({ type: "turn", modifier: "right", in_m: 1300 }).distance, "0.8 mi");
    eq(bannerOf({ type: "turn", modifier: "right", in_m: 150 }).distance, "500 ft");
    eq(bannerOf({ type: "turn", modifier: "right", in_m: 20000 }).distance, "12 mi");
    eq(distanceText(160), "500 ft");
    eq(distanceText(170), "0.1 mi");
    eq(distanceText(16000), "9.9 mi");
    eq(distanceText(16090), "10 mi");
    eq(distanceText(20), "50 ft");
  }],
  ["the banner: icon, distance, words and street", () => {
    eq(bannerOf(DEMO.next), { icon: "turn-right", distance: "0.8 mi", text: "Turn right", street: "Reservation Rd" });
    const m = bannerOf({ type: "merge", modifier: "slight left", street: "CA 1", in_m: 400 });
    eq([m.icon, m.text, m.street], ["merge", "Merge", "CA 1"]);
    const a = bannerOf({ type: "arrive", modifier: null, street: "", in_m: 90 });
    eq([a.icon, a.text, a.street], ["arrive", "Arrive", ""]);
  }],
  ["the street reads as a clause: onto a road joined, toward a road kept", () => {
    eq(streetLine(bannerOf(DEMO.next)), "onto Reservation Rd");
    eq(streetLine(bannerOf({ type: "fork", modifier: "slight left", street: "CA 1", in_m: 900 })), "toward CA 1");
    eq(streetLine(bannerOf({ type: "off ramp", modifier: "slight right", street: "", in_m: 900 })), "");
    eq(streetLine(bannerOf({ type: "arrive", street: "Sandholdt Road", in_m: 50 })), "Sandholdt Road");
  }],
  ["OSRM's type and modifier pick the icon", () => {
    const table = [
      ["turn", "left", "turn-left"], ["turn", "sharp left", "turn-left"],
      ["turn", "right", "turn-right"], ["turn", "sharp right", "turn-right"],
      ["turn", "slight left", "slight-left"], ["turn", "slight right", "slight-right"],
      ["turn", "straight", "straight"], ["turn", "uturn", "uturn"],
      ["end of road", "left", "turn-left"], ["end of road", "right", "turn-right"],
      ["new name", "straight", "straight"], ["continue", "slight right", "slight-right"],
      ["continue", "uturn", "uturn"], ["depart", "right", "straight"],
      ["merge", "slight left", "merge"], ["merge", "slight right", "merge"],
      ["fork", "slight left", "fork-left"], ["fork", "left", "fork-left"],
      ["fork", "slight right", "fork-right"], ["fork", "right", "fork-right"],
      ["on ramp", "slight right", "ramp-right"], ["on ramp", "left", "ramp-left"],
      ["off ramp", "slight right", "ramp-right"], ["off ramp", "slight left", "ramp-left"],
      ["arrive", null, "arrive"], ["arrive", "right", "arrive"],
      ["roundabout", "right", "turn-right"], ["something new", undefined, "straight"],
    ];
    for (const [type, mod, want] of table) eq(iconOf(type, mod), want, `${type} / ${mod}`);
  }],
  ["every icon is drawn, inline", () => {
    for (const id of ["turn-left", "turn-right", "slight-left", "slight-right", "straight", "merge",
                      "fork-left", "fork-right", "ramp-right", "ramp-left", "arrive", "uturn"]) {
      const s = iconSvg(id);
      ok(s.startsWith("<svg") && s.includes("<path") && s.endsWith("</svg>"), `${id}: ${s.slice(0, 60)}`);
    }
  }],
  ["the ETA is h:mm AM/PM, with the time and distance left", () => {
    const at = new Date(2026, 8, 30, 22, 8, 0).getTime();
    eq(etaOf({ eta: at / 1000, remaining_m: 4184 }, at - 27 * 60000),
       { time: "10:08 PM", remaining: "27 min", distance: "2.6 mi" });
    eq(etaOf({ eta: at / 1000, remaining_m: 187600 }, at - (2 * 3600 + 44 * 60) * 1000).remaining, "2 h 44 min");
    eq(etaOf({ eta: new Date(2026, 8, 30, 0, 5).getTime() / 1000, remaining_m: 10 }, 0).time, "12:05 AM");
    eq(etaOf({ eta: new Date(2026, 8, 30, 12, 0).getTime() / 1000, remaining_m: 10 }, 0).time, "12:00 PM");
    eq(etaOf({ eta: at / 1000, remaining_m: 0 }, at - 20000).remaining, "1 min");
  }],

  // ---- the renderer ----------------------------------------------------------
  ["the three styles are there", () => {
    for (const k of ["omacar", "carplay", "aa"]) ok(STYLES[k] && STYLES[k].land && STYLES[k].route, k);
  }],
  ["createMap draws every layer on a canvas without throwing", () => {
    const c = stubCanvas(320, 240);
    const m = createMap(c, { data: DATA, style: "omacar", dpr: 1 });
    m.resize();
    m.setView({ x: 0, y: 0, heading: 0, mpp: 4 });
    m.draw();
    // The car sits at 50% across and 70% down, on the route.
    const [cx, cy] = m.toScreen(0, 0);
    ok(Math.abs(cx - 160) < 0.01 && Math.abs(cy - 168) < 0.01, `car at ${cx},${cy}`);
    const land = px(c, 300, 20);
    ok(same(land, [...hex(STYLES.omacar.land), 255], 30), `land ${land}`);
    ok(!same(px(c, 5, 120), land, 3), "the ocean is drawn over the land");
    ok(!same(px(c, 160, 40), land, 10), "the route is drawn ahead of the car");
    m.destroy();
  }],
  ["heading-up: with heading 90, a point north of the car lands left of centre", () => {
    const c = stubCanvas(400, 300);
    const m = createMap(c, { data: DATA, style: "carplay", dpr: 1 });
    m.resize();
    m.setView({ x: 0, y: 0, heading: 0, mpp: 2 });
    let [sx, sy] = m.toScreen(0, 100);
    ok(Math.abs(sx - 200) < 0.01 && sy < 210, `heading 0: north is up (${sx},${sy})`);
    m.setView({ x: 0, y: 0, heading: 90, mpp: 2 });
    [sx, sy] = m.toScreen(0, 100);
    ok(sx < 200 && Math.abs(sy - 210) < 0.01, `heading 90: north is left (${sx},${sy})`);
    [sx, sy] = m.toScreen(100, 0);
    ok(Math.abs(sx - 200) < 0.01 && sy < 210, `heading 90: east is up (${sx},${sy})`);
    m.draw();
    m.destroy();
  }],
  ["what the car has driven is dimmed, chosen by route_m where the route doubles back", () => {
    // Out 1 km south and back north 30 m to the east, as at 1st Avenue.
    const data = Object.assign({}, DATA, { route: [[0, 0], [0, -1000], [30, -1000], [30, 1000]] });
    const c = stubCanvas(200, 400);
    const m = createMap(c, { data, style: "omacar", dpr: 1 });
    m.resize();
    const route = STYLES.omacar.route;
    const bright = (x, y) => same(px(c, Math.round(x), Math.round(y)), [...hex(route), 255], 40);
    // Each time the car is placed nearer the leg it is NOT on, as easing and a
    // 5 m simplified route can put it: only route_m says which leg it is on.
    // Northbound, 1530 m along: all of the southbound leg is behind it.
    m.setView({ x: 12, y: -500, heading: 0, mpp: 2, car: { x: 12, y: -500, heading: 0, route_m: 1530 } });
    m.draw();
    let [sx, sy] = m.toScreen(30, -300);
    ok(bright(sx, sy), "the road ahead is bright, though its segment's ends are both off screen");
    [sx, sy] = m.toScreen(0, -700);
    ok(!bright(sx, sy), "the southbound leg beside the car, already driven, is not");
    // Southbound, 500 m along: the rest of this leg, and the way back, are to come.
    m.setView({ x: 18, y: -500, heading: 180, mpp: 2, car: { x: 18, y: -500, heading: 180, route_m: 500 } });
    m.draw();
    [sx, sy] = m.toScreen(0, -700);
    ok(bright(sx, sy), "the road ahead of a southbound car is bright");
    [sx, sy] = m.toScreen(30, -300);
    ok(bright(sx, sy), "the way back is still to come");
    [sx, sy] = m.toScreen(0, -300);
    ok(!bright(sx, sy), "the road just driven is dimmed");
    m.destroy();
  }],
  ["the canvas is sized in device pixels", () => {
    const c = stubCanvas(200, 100);
    const m = createMap(c, { data: DATA, style: "aa", dpr: 2 });
    m.resize();
    eq([c.width, c.height], [400, 200]);
    m.setView({ x: 0, y: 0, heading: 30, mpp: 3 });
    m.draw();
    m.destroy();
  }],
  ["loadMapData fetches once per URL", async () => {
    const url = "data:application/json," + encodeURIComponent(JSON.stringify(DATA));
    const a = loadMapData(url), b = loadMapData(url);
    ok(a === b, "the same promise");
    const d = await a;
    eq(d.origin, ORIGIN);
  }],

  // ---- the screens -------------------------------------------------------------
  ["register: the Navigation tab opens on the demo's map, polled fast, and Home's nav card is dressed", () => {
    const D = { views: {}, extraViews: [], tabRoots: {}, cards: {} };
    registerNav(D);
    registerCard(D);
    eq(D.views.navigation.fast, true);
    ok(D.views.navigation.mount === navigationView, "the view's mount");
    eq(D.tabRoots.navigation, "navigation");
    ok(D.cards.nav === navCard, "the card");
  }],
  ["with no demo block the screen says it is waiting", () => withLive(null, async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const off = navigationView(root, { arg: null });
    await settle();
    ok(root.textContent.includes("Waiting for the demo drive"), root.textContent.slice(0, 200));
    ok(root.textContent.includes("© OpenStreetMap contributors"), "the credit");
    off();
    root.remove();
  })],
  ["with a demo block it shows the turn, the ETA and the way to the road cameras", () => withLive(DEMO, async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const off = navigationView(root, { arg: null });
    store.emit("live");
    await settle();
    const t = root.textContent;
    ok(t.includes("0.8 mi") && t.includes("Turn right") && t.includes("Reservation Rd"), t.slice(0, 300));
    ok(t.includes("2.6 mi"), "the distance left");
    ok(!root.querySelector(".dnav-wait") || root.querySelector(".dnav-wait").hidden, "not waiting");
    const chip = [...root.querySelectorAll("a,button")].find((b) => b.textContent.includes("Road cameras"));
    ok(chip, "a Road cameras chip");
    off();
    root.remove();
  })],
  ["Home's nav card: the banner and the ETA row, and a map that goes when the card does", () => withLive(DEMO, async () => {
    const c = navCard();
    document.body.appendChild(c.node);
    c.paint();
    await settle();
    const t = c.node.textContent;
    ok(t.includes("0.8 mi") && t.includes("Turn right") && t.includes("ETA"), t.slice(0, 200));
    ok(c.node.querySelector("canvas"), "a mini map");
    c.destroy();
    c.node.remove();
  })],
  ["mountMap gives back destroy, and survives a missing map.json", async () => {
    const el = document.createElement("div");
    el.style.cssText = "width:200px;height:120px";
    document.body.appendChild(el);
    const m = mountMap(el, { style: "carplay", follow: true });
    ok(typeof m.destroy === "function", "destroy");
    await settle();
    m.destroy();
    ok(!el.querySelector("canvas"), "the canvas goes with it");
    el.remove();
  }],
];

function hex(s) {
  const n = parseInt(s.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
