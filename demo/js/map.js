// The meetup demo's map renderer: map.json (tools/demo_map.py) on a Canvas 2D,
// heading-up, with no map library.
//
//   project(lat, lon, origin) -> [x, y]       metres east and north of origin,
//                                             exactly as demo_map.py projects
//   createMap(canvas, { data, style, dpr, anchor })
//     -> { setView({ x, y, heading, mpp, car: { x, y, heading, route_m } | null }),
//          toScreen(x, y), resize(),
//          draw(), destroy() }
//   STYLES = { omacar, carplay, aa }
//   loadMapData(url = "/demo-media/map.json") -> Promise<data>, one fetch per URL
//
// THE WORLD IS PREPARED ONCE AND ONLY TRANSFORMED AFTER THAT. Every road,
// river and lake is built into a Path2D in map metres when the data arrives,
// grouped by class into 2 km tiles, and each frame strokes only the tiles
// whose extent meets the view, under one canvas transform: translate to the
// car's place on screen, rotate so the heading points up, scale metres to
// pixels. Line widths are pixels times metres-per-pixel, so a road is the same
// width on screen however the canvas is turned. Labels and the car are drawn
// after, in screen space, so text is always upright.
//
// Credit is the view's job, in the DOM: "© OpenStreetMap contributors".

const RAD = Math.PI / 180;
export const K_LON = 111320;
export const K_LAT = 110540;

export function project(lat, lon, origin) {
  const [lat0, lon0] = origin;
  return [(lon - lon0) * Math.cos(lat0 * RAD) * K_LON, (lat - lat0) * K_LAT];
}

export function unproject(x, y, origin) {
  const [lat0, lon0] = origin;
  return [lat0 + y / K_LAT, lon0 + x / (Math.cos(lat0 * RAD) * K_LON)];
}

// ---- the looks ------------------------------------------------------------
//
// Road widths are CSS pixels at 3 m/px, [casing, fill]; a casing of 0 means
// none. `route` is the line ahead of the car, `past` what it has driven.
// `vignette` is how dark the edges fall off, for mountMap's overlay: drawn in
// CSS over the canvas, because a full-screen gradient per frame nearly
// tripled the draw time in software (4.4 -> 11.9 ms at 2x on the box).
const CLASSES = ["minor", "tertiary", "secondary", "primary", "trunk", "motorway"];

export const STYLES = {
  // Mockups 3 and 7: near-black slate land, a deep blue-slate sea, a slate
  // network that brightens with the road's class, the app's cyan route.
  omacar: {
    land: "#0E1820", ocean: "#0C2D49", water: "#0D3150", river: "#16425F", coast: "#1F5B7C",
    vignette: 0.32,
    roads: {
      minor:     { color: "#233643", w: [0, 1.4] },
      tertiary:  { color: "#2B4151", w: [0, 2.0] },
      secondary: { color: "#324B5C", w: [0, 2.6] },
      primary:   { color: "#3B5768", w: [0, 3.4] },
      trunk:     { color: "#466575", casing: "#122029", w: [5.8, 4.2] },
      motorway:  { color: "#527385", casing: "#122029", w: [6.8, 5.0] },
    },
    route: "#22CDEC", routeCasing: "#A5EEFB", glow: "34, 205, 236", past: "rgba(120, 170, 190, 0.35)",
    label: "#93A9B7", halo: "#0E1820", labelFont: "600 13px",
    shield: { fill: "#E8EDF1", ink: "#0B1419", edge: "#0E1820" },
    car: { kind: "disc", disc: "#FFFFFF", arrow: "#1D6FF2", shadow: "rgba(0, 0, 0, 0.55)" },
  },
  // A CarPlay-like night map: lighter charcoal grey, grey roads, a blue route.
  carplay: {
    land: "#2A2C30", ocean: "#1B2B3E", water: "#1D2E42", river: "#263B52", coast: "#33506B",
    vignette: 0.22,
    roads: {
      minor:     { color: "#3A3D43", w: [0, 1.5] },
      tertiary:  { color: "#44474E", w: [0, 2.1] },
      secondary: { color: "#4D5058", w: [0, 2.7] },
      primary:   { color: "#5A5E66", w: [0, 3.5] },
      trunk:     { color: "#6A6E76", casing: "#212226", w: [6.0, 4.4] },
      motorway:  { color: "#7B7F87", casing: "#212226", w: [7.0, 5.2] },
    },
    route: "#3D8BFF", routeCasing: "#9CC4FF", glow: "61, 139, 255", past: "rgba(160, 170, 185, 0.35)",
    label: "#B7BBC2", halo: "#2A2C30", labelFont: "600 13px",
    shield: { fill: "#F2F3F5", ink: "#1B1C1F", edge: "#2A2C30" },
    car: { kind: "disc", disc: "#FFFFFF", arrow: "#2F7BF6", shadow: "rgba(0, 0, 0, 0.5)" },
  },
  // An Android Auto-like night map: dark teal land, a blue route, a chevron.
  aa: {
    land: "#15282B", ocean: "#0D3246", water: "#0F364B", river: "#164056", coast: "#21546C",
    vignette: 0.26,
    roads: {
      minor:     { color: "#22393D", w: [0, 1.5] },
      tertiary:  { color: "#2A4347", w: [0, 2.1] },
      secondary: { color: "#314C51", w: [0, 2.7] },
      primary:   { color: "#3B585D", w: [0, 3.5] },
      trunk:     { color: "#48676C", casing: "#0F1E20", w: [6.0, 4.4] },
      motorway:  { color: "#56777C", casing: "#0F1E20", w: [7.0, 5.2] },
    },
    route: "#4C8DF6", routeCasing: "#A8C8FF", glow: "76, 141, 246", past: "rgba(140, 170, 175, 0.35)",
    label: "#9DB6B9", halo: "#15282B", labelFont: "500 13px",
    shield: { fill: "#E6EEEF", ink: "#0D1B1D", edge: "#15282B" },
    car: { kind: "chevron", fill: "#4C8DF6", edge: "#FFFFFF", shadow: "rgba(0, 0, 0, 0.5)" },
  },
};

const FONT = "\"OmaCar Inter\", Inter, \"Adwaita Sans\", \"Noto Sans\", sans-serif";

// ---- the data ---------------------------------------------------------------

const cache = new Map();

export function loadMapData(url = "/demo-media/map.json") {
  if (!cache.has(url)) {
    const p = fetch(url, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error(`map data: ${r.status} ${url}`);
      return r.json();
    });
    // A failure is not kept, so the next screen to ask tries again.
    p.catch(() => { if (cache.get(url) === p) cache.delete(url); });
    cache.set(url, p);
  }
  return cache.get(url);
}

const TILE = 2000;     // metres
const CHUNK = 48;      // points per piece of a long line

const prepared = new WeakMap();

function extent(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) {
    if (x < x0) x0 = x; if (x > x1) x1 = x;
    if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}

// Lines and polygons into Path2Ds, by tile. A long line is cut into pieces of
// CHUNK points first, so a freeway that crosses the whole map is only drawn
// where the view is.
function tiles(features, { closed = false } = {}) {
  const groups = new Map();
  for (const f of features || []) {
    if (!f || f.length < 2) continue;
    const pieces = [];
    if (closed || f.length <= CHUNK) pieces.push(f);
    else for (let i = 0; i < f.length - 1; i += CHUNK - 1) pieces.push(f.slice(i, i + CHUNK));
    for (const p of pieces) {
      const e = extent(p);
      const key = Math.floor((e[0] + e[2]) / 2 / TILE) + ":" + Math.floor((e[1] + e[3]) / 2 / TILE);
      let g = groups.get(key);
      if (!g) { g = { path: new Path2D(), box: [Infinity, Infinity, -Infinity, -Infinity] }; groups.set(key, g); }
      g.path.moveTo(p[0][0], p[0][1]);
      for (let i = 1; i < p.length; i++) g.path.lineTo(p[i][0], p[i][1]);
      if (closed) g.path.closePath();
      g.box = [Math.min(g.box[0], e[0]), Math.min(g.box[1], e[1]), Math.max(g.box[2], e[2]), Math.max(g.box[3], e[3])];
    }
  }
  return [...groups.values()];
}

function prepare(data) {
  let p = prepared.get(data);
  if (p) return p;
  const L = data.layers || {};
  const R = L.roads || {};
  const route = data.route || [];
  const along = [0];
  for (let i = 1; i < route.length; i++)
    along.push(along[i - 1] + Math.hypot(route[i][0] - route[i - 1][0], route[i][1] - route[i - 1][1]));
  p = {
    ocean: tiles(L.ocean, { closed: true }),
    water: tiles(L.water, { closed: true }),
    coast: tiles(L.coast),
    rivers: tiles(L.rivers),
    roads: Object.fromEntries(CLASSES.map((c) => [c, tiles(R[c])])),
    labels: (data.labels || []).filter((l) => l && l.text),
    route, along,
  };
  prepared.set(data, p);
  return p;
}

const meets = (b, v) => !(b[2] < v[0] || b[0] > v[2] || b[3] < v[1] || b[1] > v[3]);

// ---- the renderer -----------------------------------------------------------

export function createMap(canvas, { data, style = "omacar", dpr = null, anchor = [0.5, 0.7] } = {}) {
  const ctx = canvas.getContext("2d");
  const S = STYLES[style] || STYLES.omacar;
  const W = prepare(data || {});
  let w = 0, h = 0, ratio = 1;
  const view = { x: 0, y: 0, heading: 0, mpp: 3, car: null };
  let routeAt = 0;     // the route vertex the car was last nearest, for the split

  function resize() {
    ratio = dpr || globalThis.devicePixelRatio || 1;
    w = canvas.clientWidth || (canvas.width / ratio) || 0;
    h = canvas.clientHeight || (canvas.height / ratio) || 0;
    const pw = Math.max(1, Math.round(w * ratio)), ph = Math.max(1, Math.round(h * ratio));
    if (canvas.width !== pw) canvas.width = pw;
    if (canvas.height !== ph) canvas.height = ph;
  }

  function setView({ x = view.x, y = view.y, heading = view.heading, mpp = view.mpp, car } = {}) {
    Object.assign(view, { x, y, heading, mpp: Math.max(0.2, mpp) });
    view.car = car === undefined ? { x, y, heading } : car;
  }

  // World metres to CSS pixels on the canvas.
  function toScreen(x, y) {
    const a = view.heading * RAD, c = Math.cos(a), s = Math.sin(a);
    const dx = x - view.x, dy = y - view.y;
    const fwd = dx * s + dy * c, right = dx * c - dy * s;
    return [anchor[0] * w + right / view.mpp, anchor[1] * h - fwd / view.mpp];
  }

  function toWorld(sx, sy) {
    const a = view.heading * RAD, c = Math.cos(a), s = Math.sin(a);
    const right = (sx - anchor[0] * w) * view.mpp, fwd = (anchor[1] * h - sy) * view.mpp;
    return [view.x + right * c + fwd * s, view.y - right * s + fwd * c];
  }

  // The view's extent in metres, with room for the widest line.
  function viewBox(pad) {
    const cs = [toWorld(0, 0), toWorld(w, 0), toWorld(0, h), toWorld(w, h)];
    const xs = cs.map((p) => p[0]), ys = cs.map((p) => p[1]);
    const m = pad * view.mpp;
    return [Math.min(...xs) - m, Math.min(...ys) - m, Math.max(...xs) + m, Math.max(...ys) + m];
  }

  // Where the car is along the route, so what is behind it can be drawn as
  // driven. By route_m first when the car has one: at the turn-round near 1st
  // Avenue the route runs both ways along CA-1, 30 m apart, and the nearest
  // line alone could be the carriageway the car is not on. Then near the
  // last answer; then everywhere (the loop starting again, the first frame).
  function splitAt(x, y, routeM) {
    const r = W.route, A = W.along;
    if (r.length < 2) return null;
    const near = (lo, hi) => {
      let best = { d: Infinity, i: 0, t: 0 };
      for (let i = Math.max(0, lo); i < Math.min(r.length - 1, hi); i++) {
        const [ax, ay] = r[i], [bx, by] = r[i + 1];
        const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
        const t = L2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / L2)) : 0;
        const d = Math.hypot(ax + dx * t - x, ay + dy * t - y);
        if (d < best.d) best = { d, i, t };
      }
      return best;
    };
    let b = { d: Infinity };
    if (Number.isFinite(routeM)) {
      let lo = 0, hi = A.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (A[mid] < routeM - 250) lo = mid + 1; else hi = mid; }
      let end = lo;
      while (end < A.length - 1 && A[end] < routeM + 250) end++;
      b = near(lo - 1, end + 1);
    }
    if (b.d > 60) b = near(routeAt - 20, routeAt + 200);
    if (b.d > 60) b = near(0, r.length);
    if (b.d > 150) return null;       // off the route: draw it all ahead
    routeAt = b.i;
    const [ax, ay] = r[b.i], [bx, by] = r[b.i + 1];
    return { i: b.i, p: [ax + (bx - ax) * b.t, ay + (by - ay) * b.t] };
  }

  function strokeTiles(groups, vb, color, px) {
    if (!groups.length || px <= 0) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = px * view.mpp;
    for (const g of groups) if (meets(g.box, vb)) ctx.stroke(g.path);
  }

  function fillTiles(groups, vb, color) {
    ctx.fillStyle = color;
    for (const g of groups) if (meets(g.box, vb)) ctx.fill(g.path);
  }

  function linePath(pts, from, to, head, tail) {
    const p = new Path2D();
    let started = false;
    if (head) { p.moveTo(head[0], head[1]); started = true; }
    for (let i = from; i <= to; i++) {
      if (!started) { p.moveTo(pts[i][0], pts[i][1]); started = true; } else p.lineTo(pts[i][0], pts[i][1]);
    }
    if (tail) p.lineTo(tail[0], tail[1]);
    return p;
  }

  function drawRoute(vb, zoom) {
    const r = W.route;
    if (r.length < 2) return;
    const car = view.car;
    const cut = car ? splitAt(car.x, car.y, car.route_m) : null;
    // Only the stretch of route in view is built: from the first segment that
    // meets the view to the last. By segment, not by point -- a straight run of
    // freeway can be one segment kilometres long with both ends off screen.
    let lo = -1, hi = -1;
    for (let i = 0; i < r.length - 1; i++) {
      const [ax, ay] = r[i], [bx, by] = r[i + 1];
      if (Math.max(ax, bx) < vb[0] || Math.min(ax, bx) > vb[2] ||
          Math.max(ay, by) < vb[1] || Math.min(ay, by) > vb[3]) continue;
      if (lo < 0) lo = i;
      hi = i + 1;
    }
    if (lo < 0) return;
    if (cut) { lo = Math.min(lo, cut.i); hi = Math.max(hi, cut.i + 1); }
    const m = view.mpp;
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    if (cut && cut.i >= lo) {
      ctx.strokeStyle = S.past;
      ctx.lineWidth = 5 * zoom * m;
      ctx.stroke(linePath(r, lo, Math.min(cut.i, hi), null, cut.p));
    }
    const ahead = cut ? linePath(r, Math.max(cut.i + 1, lo), hi, cut.p, null) : linePath(r, lo, hi, null, null);
    // The glow: two wide, faint passes under a lighter casing and the line.
    ctx.strokeStyle = `rgba(${S.glow}, 0.10)`; ctx.lineWidth = 22 * zoom * m; ctx.stroke(ahead);
    ctx.strokeStyle = `rgba(${S.glow}, 0.18)`; ctx.lineWidth = 14 * zoom * m; ctx.stroke(ahead);
    ctx.strokeStyle = S.routeCasing; ctx.lineWidth = 9 * zoom * m; ctx.stroke(ahead);
    ctx.strokeStyle = S.route; ctx.lineWidth = 6.5 * zoom * m; ctx.stroke(ahead);
  }

  function drawLabels(vb) {
    const placed = [];
    const hit = (b) => placed.some((o) => !(b[2] < o[0] || b[0] > o[2] || b[3] < o[1] || b[1] > o[3]));
    const car = view.car ? toScreen(view.car.x, view.car.y) : null;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    for (const pass of ["name", "shield"]) {
      for (const l of W.labels) {
        if ((l.class === "shield") !== (pass === "shield")) continue;
        if (l.x < vb[0] || l.x > vb[2] || l.y < vb[1] || l.y > vb[3]) continue;
        const [sx, sy] = toScreen(l.x, l.y);
        if (sx < -40 || sx > w + 40 || sy < -20 || sy > h + 20) continue;
        if (pass === "shield") {
          ctx.font = `700 11px ${FONT}`;
          const tw = Math.max(18, ctx.measureText(l.text).width + 10), th = 17;
          const b = [sx - tw / 2 - 3, sy - th / 2 - 3, sx + tw / 2 + 3, sy + th / 2 + 3];
          if (hit(b) || (car && Math.hypot(sx - car[0], sy - car[1]) < 36)) continue;
          placed.push(b);
          ctx.beginPath();
          ctx.roundRect(sx - tw / 2, sy - th / 2, tw, th, 5);
          ctx.fillStyle = S.shield.fill; ctx.fill();
          ctx.lineWidth = 1.5; ctx.strokeStyle = S.shield.edge; ctx.stroke();
          ctx.fillStyle = S.shield.ink; ctx.fillText(l.text, sx, sy + 0.5);
        } else {
          ctx.font = `${S.labelFont} ${FONT}`;
          const tw = ctx.measureText(l.text).width;
          const b = [sx - tw / 2 - 4, sy - 10, sx + tw / 2 + 4, sy + 10];
          if (hit(b) || (car && Math.hypot(sx - car[0], sy - car[1]) < 40)) continue;
          placed.push(b);
          ctx.lineWidth = 3.5; ctx.strokeStyle = S.halo; ctx.strokeText(l.text, sx, sy);
          ctx.fillStyle = S.label; ctx.fillText(l.text, sx, sy);
        }
      }
    }
  }

  // Mockup 3's car: a white disc with the blue navigation arrow, pointing
  // along the car's heading on the turned map. Android Auto's is a chevron.
  function drawCar() {
    const car = view.car;
    if (!car) return;
    const [sx, sy] = toScreen(car.x, car.y);
    const rot = ((car.heading || 0) - view.heading) * RAD;
    const C = S.car;
    ctx.save();
    ctx.translate(sx, sy);
    ctx.shadowColor = C.shadow; ctx.shadowBlur = 10; ctx.shadowOffsetY = 2;
    if (C.kind === "disc") {
      ctx.beginPath(); ctx.arc(0, 0, 17, 0, Math.PI * 2);
      ctx.fillStyle = C.disc; ctx.fill();
      ctx.shadowColor = "transparent";
      ctx.rotate(rot);
      ctx.beginPath();
      ctx.moveTo(0, -10.5); ctx.lineTo(8, 8.5); ctx.lineTo(0, 4.5); ctx.lineTo(-8, 8.5); ctx.closePath();
      ctx.fillStyle = C.arrow; ctx.fill();
    } else {
      ctx.rotate(rot);
      ctx.beginPath();
      ctx.moveTo(0, -16); ctx.lineTo(12, 12); ctx.lineTo(0, 5); ctx.lineTo(-12, 12); ctx.closePath();
      ctx.lineJoin = "round"; ctx.lineWidth = 3.5; ctx.strokeStyle = C.edge; ctx.stroke();
      ctx.shadowColor = "transparent";
      ctx.fillStyle = C.fill; ctx.fill();
    }
    ctx.restore();
  }

  function draw() {
    if (!w || !h) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.fillStyle = S.land;
    ctx.fillRect(0, 0, w, h);
    // Roads get wider as the map zooms in, and thinner out, but not in step
    // with the metres: a motorway at 6 m/px is still a line you can see.
    const zoom = Math.sqrt(3 / view.mpp);
    const vb = viewBox(30 * zoom);

    ctx.save();
    ctx.translate(anchor[0] * w, anchor[1] * h);
    ctx.rotate(-view.heading * RAD);
    ctx.scale(1 / view.mpp, -1 / view.mpp);
    ctx.translate(-view.x, -view.y);
    ctx.lineCap = "round"; ctx.lineJoin = "round";

    fillTiles(W.ocean, vb, S.ocean);
    strokeTiles(W.coast, vb, S.coast, 1.5);
    fillTiles(W.water, vb, S.water);
    strokeTiles(W.rivers, vb, S.river, 1.6 * zoom);
    for (const c of CLASSES) {
      const R = S.roads[c];
      if (R.casing) strokeTiles(W.roads[c], vb, R.casing, R.w[0] * zoom);
    }
    for (const c of CLASSES) strokeTiles(W.roads[c], vb, S.roads[c].color, S.roads[c].w[1] * zoom);
    drawRoute(vb, zoom);
    ctx.restore();

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    drawLabels(vb);
    drawCar();
  }

  return {
    setView, toScreen, toWorld, resize, draw,
    get view() { return Object.assign({}, view); },
    get size() { return [w, h]; },
    destroy() { w = h = 0; },
  };
}
