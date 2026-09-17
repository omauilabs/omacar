// The four vitals on the hub, each with the thing it measures moving behind it.
//
// Speed gets stars streaking past, RPM gets meshing gears, coolant gets green
// fluid with bubbles rising through it, and the trip gets a route drawn
// point to point. Every one is DRIVEN BY THE READING -- the stars stretch with
// road speed, the gears turn at engine speed, the bubbles quicken as the
// coolant warms, the route fills with the distance covered. A card whose
// background animates at a fixed rate is a screensaver; this is an instrument
// you can read from across the cabin without focusing on the number.
//
// THE RULES ARE effects.js's RULES, and they are not negotiable here either.
// That file explains each at length; the short version, because a second
// animation surface is exactly where they get quietly dropped:
//
//   - 2D canvas, never WebGL. A shader that fails to compile on an old Intel
//     driver takes the dashboard with it.
//   - 30fps. Nothing here benefits from sixty and the machine is also polling
//     a serial port.
//   - Dead when the tab is hidden, dead when the view unmounts.
//   - prefers-reduced-motion refuses to start it at all.
//   - Pixel ratio capped: a HiDPI panel must not quadruple the fill cost of a
//     background nobody looks at directly.
//   - Colours come from the palette tokens, so all four follow whatever theme
//     is worn -- including Night red, where a stray blue would undo the point
//     of the look.
//
// AND THE NUMBER WINS. These draw at low alpha under the text, on a card whose
// job is to be read at a glance. If a choice ever comes down to the effect
// looking better or the reading staying legible, it is not a choice.

const FRAME = 1000 / 30;

function reducedMotion() {
  try { return matchMedia("(prefers-reduced-motion: reduce)").matches; }
  catch { return false; }
}

function palette() {
  const cs = getComputedStyle(document.documentElement);
  const tok = (n, fb) => (cs.getPropertyValue(n) || "").trim() || fb;
  return {
    ink: tok("--bright", "#eaf2ee"),
    accent: tok("--accent", "#9af6ca"),
    ok: tok("--ok", "#7ee0a8"),
    warn: tok("--warn", "#e5b457"),
    bad: tok("--bad", "#ff8091"),
    info: tok("--info", "#99c6fa"),
    edge: tok("--edge-2", "#303333"),
  };
}

function rgb(hex, fb) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((hex || "").trim());
  if (!m) return fb;
  let x = m[1];
  if (x.length === 3) x = x.split("").map((c) => c + c).join("");
  return [parseInt(x.slice(0, 2), 16), parseInt(x.slice(2, 4), 16),
          parseInt(x.slice(4, 6), 16)];
}

const rgba = (hex, a, fb) => {
  const c = rgb(hex, fb || [140, 140, 140]);
  return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
};

// ---------------------------------------------------------------- the four
//
// Each renderer is (ctx, w, h, t, value, pal) and draws ONE frame. `value` is
// whatever read() returned, already normalised by the caller to something the
// renderer can use directly -- the renderers do no unit thinking, because
// the hub already knows whether it is showing mph or km/h and this must not
// have a second opinion about it.

function makeWarp() {
  let stars = [];
  return (ctx, w, h, t, v, pal) => {
    const speed = Math.max(0, Math.min(1, (v || 0) / 120));   // 0..1 of "fast"
    // Denser than the first pass. At 5200px per star the field was so sparse
    // that at 88 km/h the card read as empty rather than fast, which is the
    // one thing this effect exists to say.
    const want = Math.round((w * h) / 2600);
    while (stars.length < want) {
      stars.push({ a: Math.random() * Math.PI * 2, r: Math.random() * 0.5 + 0.02,
                   z: Math.random() });
    }
    stars.length = want;
    const cx = w / 2, cy = h / 2;
    const reach = Math.hypot(cx, cy);
    // Even stopped, a little drift -- a dead starfield reads as a broken card
    // rather than a still one.
    const rate = 0.004 + speed * 0.055;
    for (const s of stars) {
      s.z += rate;
      if (s.z > 1) { s.z = 0.02; s.a = Math.random() * Math.PI * 2; }
      const near = s.z * s.z;                 // perspective: late acceleration
      const d = near * reach * (0.5 + s.r);
      const x = cx + Math.cos(s.a) * d;
      const y = cy + Math.sin(s.a) * d;
      // The streak IS the speed. At rest these are points; at speed they are
      // lines, which is the whole reading rendered as motion.
      const tail = (2 + speed * 26) * near;
      const x2 = cx + Math.cos(s.a) * (d - tail);
      const y2 = cy + Math.sin(s.a) * (d - tail);
      ctx.strokeStyle = rgba(pal.ink, 0.10 + near * 0.46);
      ctx.lineWidth = 0.7 + near * 1.8;
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x, y);
      ctx.stroke();
    }
  };
}

function gearPath(ctx, cx, cy, r, teeth, phase) {
  const inner = r * 0.74;
  ctx.beginPath();
  const step = (Math.PI * 2) / (teeth * 2);
  for (let i = 0; i < teeth * 2; i++) {
    const a = phase + i * step;
    const rr = i % 2 === 0 ? r : inner;
    const x = cx + Math.cos(a) * rr;
    const y = cy + Math.sin(a) * rr;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

function makeGears() {
  let phase = 0;
  return (ctx, w, h, t, v, pal) => {
    const rpm = Math.max(0, v || 0);
    // Real revolutions would be a blur and a strobe. This is geared DOWN hard
    // and deliberately: what the eye should read is "turning, and faster than
    // a moment ago", not an attempt at a tachometer nobody can count.
    phase += 0.004 + (rpm / 7000) * 0.09;
    const r = Math.min(w, h) * 0.42;
    const big = { x: w * 0.30, y: h * 0.62, r, teeth: 12 };
    const small = { x: w * 0.30 + r * 1.52, y: h * 0.30, r: r * 0.66, teeth: 8 };
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = rgba(pal.ink, 0.16);
    gearPath(ctx, big.x, big.y, big.r, big.teeth, phase);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(big.x, big.y, big.r * 0.24, 0, Math.PI * 2);
    ctx.stroke();
    // Counter-rotating, and geared by the tooth ratio, because two gears
    // turning the same way is the one thing everybody notices.
    const ratio = -big.teeth / small.teeth;
    ctx.strokeStyle = rgba(pal.accent, 0.20);
    gearPath(ctx, small.x, small.y, small.r, small.teeth,
             -phase * ratio * -1 + Math.PI / small.teeth);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(small.x, small.y, small.r * 0.26, 0, Math.PI * 2);
    ctx.stroke();
  };
}

function makeCoolant() {
  let bubbles = [];
  return (ctx, w, h, t, v, pal) => {
    const c = v == null ? null : v;                 // degrees C, always
    const warm = c == null ? 0 : Math.max(0, Math.min(1, (c - 40) / 65));
    // Antifreeze green, which is the point -- but it takes the theme's own
    // green so the card still belongs to the palette it is sitting in.
    const fluid = pal.ok;
    const level = h * (0.18 + warm * 0.5);
    const g = ctx.createLinearGradient(0, h - level, 0, h);
    g.addColorStop(0, rgba(fluid, 0.05));
    g.addColorStop(1, rgba(fluid, 0.22));
    ctx.fillStyle = g;
    ctx.fillRect(0, h - level, w, level);
    // A surface line, so it reads as fluid rather than a gradient.
    ctx.strokeStyle = rgba(fluid, 0.30);
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= w; x += 6) {
      const y = h - level + Math.sin((x / w) * 6.5 + t / 900) * 1.8;
      if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    // Bubbles: more of them, faster, as it warms. A cold engine barely
    // fizzes; one near boiling is busy, which is a reading you can take
    // without reading the number.
    const want = Math.round(2 + warm * 14);
    while (bubbles.length < want) {
      bubbles.push({ x: Math.random() * w, y: h + Math.random() * 20,
                     r: 0.8 + Math.random() * 2.2, s: 0.2 + Math.random() * 0.8 });
    }
    bubbles.length = want;
    for (const b of bubbles) {
      b.y -= b.s * (0.35 + warm * 1.9);
      b.x += Math.sin((b.y + t / 260) / 18) * 0.35;
      if (b.y < h - level - 4) { b.y = h + Math.random() * 12; b.x = Math.random() * w; }
      ctx.strokeStyle = rgba(fluid, 0.34);
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2);
      ctx.stroke();
    }
  };
}

function makeRoute() {
  return (ctx, w, h, t, v, pal) => {
    // A route across the card with a vehicle running it. `v` is the fraction
    // of a notional leg covered, which the caller derives from the trip; with
    // no trip at all it simply sits at the start, because a card that animates
    // a journey nobody has taken is telling a small lie.
    const frac = Math.max(0, Math.min(1, v == null ? 0 : v));
    const pad = Math.min(w, h) * 0.22;
    const pts = [[pad, h - pad], [w * 0.38, h * 0.36], [w * 0.62, h * 0.66],
                 [w - pad, pad]];
    const at = (u) => {
      const seg = Math.min(pts.length - 2, Math.floor(u * (pts.length - 1)));
      const local = u * (pts.length - 1) - seg;
      const a = pts[seg], b = pts[seg + 1];
      return [a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local];
    };
    ctx.lineWidth = 1.1;
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = rgba(pal.ink, 0.20);
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (const p of pts.slice(1)) ctx.lineTo(p[0], p[1]);
    ctx.stroke();
    ctx.setLineDash([]);
    // The covered part, solid.
    ctx.strokeStyle = rgba(pal.accent, 0.50);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    const steps = 40;
    for (let i = 1; i <= steps; i++) {
      const p = at((i / steps) * frac);
      ctx.lineTo(p[0], p[1]);
    }
    ctx.stroke();
    for (const [p, col] of [[pts[0], pal.ink], [pts[pts.length - 1], pal.ink]]) {
      ctx.strokeStyle = rgba(col, 0.38);
      ctx.beginPath();
      ctx.arc(p[0], p[1], 2.6, 0, Math.PI * 2);
      ctx.stroke();
    }
    // The traveller pulses gently so the card has life while parked, without
    // pretending the car is moving.
    const head = at(frac);
    const pulse = 2.2 + Math.sin(t / 420) * 0.7;
    ctx.fillStyle = rgba(pal.accent, 0.55);
    ctx.beginPath();
    ctx.arc(head[0], head[1], pulse, 0, Math.PI * 2);
    ctx.fill();
  };
}

const MAKERS = { warp: makeWarp, gears: makeGears, coolant: makeCoolant,
                 route: makeRoute };

export const CARD_EFFECTS = Object.keys(MAKERS);

/** Mount one card background. `read()` returns the value the renderer wants. */
export function mountCardEffect(host, kind, read) {
  const make = MAKERS[kind];
  if (!make || reducedMotion()) return () => {};

  const canvas = document.createElement("canvas");
  canvas.className = "card-fx";
  canvas.setAttribute("aria-hidden", "true");
  host.appendChild(canvas);
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) { canvas.remove(); return () => {}; }

  const draw = make();
  let pal = palette();
  let w = 0, h = 0, dpr = 1, raf = 0, last = 0, stopped = false;

  function resize() {
    const r = host.getBoundingClientRect();
    if (!r.width || !r.height) return;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    w = r.width; h = r.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // A theme can change under a mounted card, and the tokens are read once.
    pal = palette();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  function loop(now) {
    if (stopped) return;
    raf = requestAnimationFrame(loop);
    if (now - last < FRAME) return;
    last = now;
    if (!w || !h) return;
    ctx.clearRect(0, 0, w, h);
    let v = null;
    try { v = read(); } catch { v = null; }
    draw(ctx, w, h, now, v, pal);
  }

  function onVisibility() {
    if (document.hidden) { cancelAnimationFrame(raf); raf = 0; }
    else if (!raf && !stopped) { last = 0; raf = requestAnimationFrame(loop); }
  }
  document.addEventListener("visibilitychange", onVisibility);
  raf = requestAnimationFrame(loop);

  return () => {
    stopped = true;
    cancelAnimationFrame(raf);
    document.removeEventListener("visibilitychange", onVisibility);
    ro.disconnect();
    canvas.remove();
  };
}
