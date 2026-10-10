// Gauges — the readouts of drive mode, drawn as instruments rather than text.
//
// WHY SVG AND NOT CANVAS.
//
// ring.js is WebGL and effects.js is a 2D canvas, and both earn it: they are
// animating a whole field of pixels every frame. A gauge is not that. It is a
// static face with one thing on it that moves, and the thing that moves is a
// rotation.
//
// Drawn in SVG the face is painted once by the browser and the needle is a
// transform the compositor can animate on its own — no requestAnimationFrame,
// no redraw loop, nothing on the main thread between samples. Eight gauges on
// screen cost about what eight <div>s cost. On a 2-core Yoga that is already
// polling a serial port, that is the difference between decoration and a
// problem.
//
// It also means the faces follow the theme for free: every stroke here is a
// CSS custom property, so a gauge is green under Matrix and red-only under
// Night red without this file knowing either look exists.
//
// WHAT MAKES A DIAL LOOK REAL.
//
// Four things, and none of them is the needle:
//
//   - A sweep that is not a full circle. Real instruments open at the bottom,
//     around 270 degrees, because the pointer has to be visibly AT one end
//     rather than ambiguously near the top.
//   - Minor ticks. A face with only major ticks reads as a diagram of a gauge.
//   - Numbers on the face, not just at the ends.
//   - A coloured band where the number stops being fine, drawn on the SCALE
//     rather than applied to the needle. A red needle tells you something is
//     wrong; a red band tells you how close you are before it happens.
//
// The needle itself gets a counterweight past the hub, which costs four
// coordinates and is most of why it reads as a machined part.

const NS = "http://www.w3.org/2000/svg";

// The viewBox is cropped to what is actually drawn, not to a tidy 0 0 100 100.
//
// With the full square and preserveAspectRatio the face fits to whichever side
// runs out first, and in a wide tile that is the height -- so the dial shrank
// to a coin with a third of the tile empty on either side. Cropping to the ink
// and letting width drive (see .g-svg in app.css) makes the gauge as large as
// the slot allows, which for an instrument read at a glance is the whole game.
// Tall enough for the digital inset below the sweep. See the note on the
// value's placement in dial().
const VIEW = "8 12 84 88";

function e(tag, attrs) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    el.setAttribute(k, v);
  }
  return el;
}

// The unit, trimmed to what fits on a face.
//
// A tile's note can be a whole clause -- fuel reports "%  ·  6.6 gal", range
// reports "mi left". Centred inside a dial that ran straight through the
// reading above it. The qualifier after the separator is detail for a number
// tile; on an instrument the unit is the unit.
function shortUnit(n) {
  return String(n || "").split("·")[0].trim();
}

function reducedMotion() {
  try { return matchMedia("(prefers-reduced-motion: reduce)").matches; }
  catch { return false; }
}

// ---------------------------------------------------------------- geometry
//
// The sweep opens at the bottom: 135 degrees round to 405, clockwise, with 0
// pointing right and y growing downward as SVG does. t is 0..1 along it.
const CX = 50, CY = 53, R = 38;
const START = 135, SWEEP = 270;

const rad = (deg) => (deg * Math.PI) / 180;
const angleAt = (t) => START + t * SWEEP;

function pointAt(t, r) {
  const a = rad(angleAt(t));
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
}

// An arc path between two positions on the sweep, at a given radius.
function arcPath(t0, t1, r) {
  const [x0, y0] = pointAt(t0, r);
  const [x1, y1] = pointAt(t1, r);
  // The sweep is 270 degrees, so any span over half of it needs the large-arc
  // flag or the browser draws the short way round and the redline appears on
  // the opposite side of the face.
  const large = (t1 - t0) * SWEEP > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

// Where a value sits on the scale, clamped. A gauge that can point past its
// own maximum is a gauge that has lied about its maximum.
function fraction(scale, value) {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  const span = scale.max - scale.min;
  if (!span) return 0;
  return Math.max(0, Math.min(1, (value - scale.min) / span));
}

// Coloured regions of the scale, as spans rather than as a single "past here
// is bad" threshold.
//
// The first version had scale.warn and scale.bad and assumed trouble was
// always at the TOP. Half the readings on this car are the other way up: a
// battery at 11.9V, a tank at 8%, sixty miles of range. And fuel trim is in
// trouble at BOTH ends, which no single threshold can say at all.
function bands(scale) {
  return (scale.bands || []).map((b) => ({
    t0: fraction(scale, b.from === undefined ? scale.min : b.from),
    t1: fraction(scale, b.to === undefined ? scale.max : b.to),
    cls: b.tone === "bad" ? "g-band-bad" : "g-band-warn",
  })).filter((b) => b.t0 !== null && b.t1 !== null && b.t1 > b.t0);
}

// ---------------------------------------------------------------- the kinds
export const KINDS = {
  digital: {
    label: "Number",
    note: "The reading, large. Works for everything.",
    scaled: false,
  },
  dial: {
    label: "Dial",
    note: "A swept face with a needle, ticks and a redline.",
    scaled: true,
  },
  arc: {
    label: "Arc",
    note: "A filling sweep with the number in the middle.",
    scaled: true,
  },
  bar: {
    label: "Bar",
    note: "A linear meter. Reads well in a short, wide slot.",
    scaled: true,
  },
};

export const KIND_IDS = Object.keys(KINDS);

// Which kinds a given readout can actually wear. Anything without a numeric
// scale — the odometer, a fault count, a name — can only be a number, and
// offering it a dial would produce a needle with nowhere to point.
export function kindsFor(def) {
  return def && def.scale ? KIND_IDS : ["digital"];
}

export function normaliseKind(kind, def) {
  const allowed = kindsFor(def);
  return allowed.includes(kind) ? kind : "digital";
}

// ---------------------------------------------------------------- rendering
//
// Every kind returns the same shape: an element to put in the slot, and an
// update() that takes the tile's existing { v, n, tone } plus the raw number.
// Nothing above this file knows which kind it asked for after it has asked.

function digital() {
  const v = document.createElement("div");
  v.className = "drive-tile-v";
  v.textContent = "—";
  const n = document.createElement("div");
  n.className = "drive-tile-n";
  const el = document.createDocumentFragment();
  el.appendChild(v);
  el.appendChild(n);
  return {
    el,
    update(out) {
      v.textContent = out.v;
      v.className = "drive-tile-v" + (out.tone ? " " + out.tone : "");
      n.textContent = out.n || "";
    },
  };
}

function ticksAndBands(svg, scale) {
  const span = scale.max - scale.min;
  const step = scale.step || span / 5;
  const majors = Math.max(1, Math.round(span / step));

  // The bands first, so ticks and numbers sit on top of them.
  for (const b of bands(scale)) {
    svg.appendChild(e("path", {
      d: arcPath(b.t0, b.t1, R - 2), class: "g-band " + b.cls, fill: "none",
    }));
  }

  // The track the ticks live on.
  svg.appendChild(e("path", { d: arcPath(0, 1, R - 2), class: "g-track", fill: "none" }));

  for (let i = 0; i <= majors; i++) {
    const t = i / majors;
    const [x0, y0] = pointAt(t, R - 5);
    const [x1, y1] = pointAt(t, R - 11);
    svg.appendChild(e("line", { x1: x0.toFixed(2), y1: y0.toFixed(2),
                                x2: x1.toFixed(2), y2: y1.toFixed(2), class: "g-tick-major" }));

    // Four minors between each pair of majors. Not drawn past the last major.
    if (i < majors) {
      for (let j = 1; j < 5; j++) {
        const tm = (i + j / 5) / majors;
        const [a0, b0] = pointAt(tm, R - 5);
        const [a1, b1] = pointAt(tm, R - 8.5);
        svg.appendChild(e("line", { x1: a0.toFixed(2), y1: b0.toFixed(2),
                                    x2: a1.toFixed(2), y2: b1.toFixed(2), class: "g-tick-minor" }));
      }
    }

    const value = scale.min + t * span;
    // R-18 is the whole usable range: further out and the two END numbers sit
    // in the bottom opening where the digital inset goes; further in and the
    // ring gets so short that adjacent labels near the top run together --
    // "60 80" on a speedo became one word at R-24. The clearance at the bottom
    // comes from the inset being LOW (see dial()), not from moving these.
    const [lx, ly] = pointAt(t, R - 18);
    const label = e("text", { x: lx.toFixed(2), y: (ly + 2.4).toFixed(2), class: "g-num" });
    label.textContent = scale.tick ? scale.tick(value) : String(Math.round(value));
    svg.appendChild(label);
  }
}

function dial(def) {
  const scale = def.scale;
  const svg = e("svg", { viewBox: VIEW, class: "g-svg g-dial",
                         preserveAspectRatio: "xMidYMid meet", "aria-hidden": "true" });

  ticksAndBands(svg, scale);

  // The needle, drawn pointing RIGHT and rotated into place. Drawing it at the
  // scale's start instead would mean every rotation carried a 135 degree
  // offset, which is the kind of constant that gets "simplified" away later.
  const needle = e("polygon", {
    class: "g-needle",
    points: `${CX + R - 7},${CY} ${CX + 3},${CY - 3.1} ${CX - 9},${CY} ${CX + 3},${CY + 3.1}`,
    transform: `rotate(${START} ${CX} ${CY})`,
  });
  if (!reducedMotion()) needle.classList.add("g-eased");
  svg.appendChild(needle);

  svg.appendChild(e("circle", { cx: CX, cy: CY, r: 4.4, class: "g-hub" }));
  svg.appendChild(e("circle", { cx: CX, cy: CY, r: 1.6, class: "g-hub-pin" }));

  // The digital inset, below the opening of the sweep rather than inside the
  // ring. A real gauge puts it here for the same reason: it is the one part of
  // the face the needle can never cross.
  const value = e("text", { x: CX, y: CY + 31, class: "g-value" });
  const unit = e("text", { x: CX, y: CY + 40, class: "g-unit" });
  svg.appendChild(value);
  svg.appendChild(unit);

  return {
    el: svg,
    update(out, raw) {
      const t = fraction(scale, raw);
      needle.setAttribute("transform",
        `rotate(${(angleAt(t === null ? 0 : t)).toFixed(2)} ${CX} ${CY})`);
      needle.classList.toggle("g-dead", t === null);
      value.textContent = out.v;
      unit.textContent = shortUnit(out.n);
      svg.dataset.tone = out.tone || "";
    },
  };
}

function arc(def) {
  const scale = def.scale;
  const svg = e("svg", { viewBox: VIEW, class: "g-svg g-arc",
                         preserveAspectRatio: "xMidYMid meet", "aria-hidden": "true" });

  const r = R - 4;
  svg.appendChild(e("path", { d: arcPath(0, 1, r), class: "g-arc-track", fill: "none" }));

  // The bands sit inside the track so the reading arc can cover it without
  // hiding where the trouble starts.
  for (const b of bands(scale)) {
    svg.appendChild(e("path", { d: arcPath(b.t0, b.t1, r - 9),
                                class: "g-band g-band-thin " + b.cls, fill: "none" }));
  }

  // One long path with a dash that hides all but the read portion. Cheaper and
  // smoother than rebuilding the path geometry on every sample, and the dash
  // offset animates the same way the needle's rotation does.
  const fill = e("path", { d: arcPath(0, 1, r), class: "g-arc-fill", fill: "none" });
  const len = (SWEEP / 360) * 2 * Math.PI * r;
  fill.setAttribute("stroke-dasharray", `${len.toFixed(2)} ${len.toFixed(2)}`);
  fill.setAttribute("stroke-dashoffset", len.toFixed(2));
  if (!reducedMotion()) fill.classList.add("g-eased");
  svg.appendChild(fill);

  const value = e("text", { x: CX, y: CY + 6, class: "g-value g-value-big" });
  const unit = e("text", { x: CX, y: CY + 17, class: "g-unit" });
  svg.appendChild(value);
  svg.appendChild(unit);

  return {
    el: svg,
    update(out, raw) {
      const t = fraction(scale, raw);
      fill.setAttribute("stroke-dashoffset", (len * (1 - (t === null ? 0 : t))).toFixed(2));
      value.textContent = out.v;
      unit.textContent = shortUnit(out.n);
      svg.dataset.tone = out.tone || "";
    },
  };
}

function bar(def) {
  const scale = def.scale;
  const wrap = document.createElement("div");
  wrap.className = "g-bar";

  const head = document.createElement("div");
  head.className = "g-bar-head";
  const value = document.createElement("span");
  value.className = "g-bar-v";
  value.textContent = "—";
  const unit = document.createElement("span");
  unit.className = "g-bar-n";
  // EST / SIM: where a worked-out or simulated reading says so in type as
  // large as the number, not in a footnote (share/js/hybrid.js).
  const tag = document.createElement("span");
  tag.className = "g-bar-tag";
  head.appendChild(value);
  head.appendChild(unit);
  head.appendChild(tag);

  const track = document.createElement("div");
  track.className = "g-bar-track";
  const fill = document.createElement("i");
  fill.className = "g-bar-fill" + (reducedMotion() ? "" : " g-eased");
  track.appendChild(fill);

  // A SIGNED SCALE FILLS FROM ZERO, NOT FROM THE LEFT EDGE.
  //
  // Fuel trim runs -25 to +25 and the interesting thing about it is which side
  // of zero it is on and by how much. Filled from the left, +7.8% drew a bar
  // straight through the whole negative half INCLUDING its trouble bands, so a
  // healthy reading looked like it had swallowed the warning. Anchoring at the
  // zero mark makes the bar a deviation, which is what the number is.
  const zeroT = scale.min < 0 && scale.max > 0 ? fraction(scale, 0) : 0;
  if (zeroT > 0) {
    const mark = document.createElement("i");
    mark.className = "g-bar-zero";
    mark.style.left = (zeroT * 100).toFixed(1) + "%";
    track.appendChild(mark);
  }

  // The bands are absolutely positioned slivers of the track, so the meter
  // says where the trouble starts even when the reading is nowhere near it.
  for (const b of bands(scale)) {
    const band = document.createElement("i");
    band.className = "g-bar-band " + b.cls;
    band.style.left = (b.t0 * 100).toFixed(1) + "%";
    band.style.width = ((b.t1 - b.t0) * 100).toFixed(1) + "%";
    track.appendChild(band);
  }

  const ends = document.createElement("div");
  ends.className = "g-bar-ends";
  const lo = document.createElement("span");
  const hi = document.createElement("span");
  lo.textContent = scale.tick ? scale.tick(scale.min) : String(scale.min);
  hi.textContent = scale.tick ? scale.tick(scale.max) : String(scale.max);
  ends.appendChild(lo);
  ends.appendChild(hi);

  wrap.appendChild(head);
  wrap.appendChild(track);
  wrap.appendChild(ends);

  return {
    el: wrap,
    update(out, raw) {
      const t = fraction(scale, raw);
      const at = t === null ? zeroT : t;
      fill.style.left = (Math.min(zeroT, at) * 100).toFixed(1) + "%";
      fill.style.width = (Math.abs(at - zeroT) * 100).toFixed(1) + "%";
      value.textContent = out.v;
      unit.textContent = out.source ? "" : out.n || "";
      tag.textContent = TAGS[out.source] || "";
      wrap.dataset.tone = out.tone || "";
      wrap.dataset.source = out.source || "";
    },
  };
}

const TAGS = { estimated: "EST", simulated: "SIM" };

const BUILDERS = { digital, dial, arc, bar };

export function makeGauge(kind, def) {
  const k = normaliseKind(kind, def);
  return BUILDERS[k](def);
}
