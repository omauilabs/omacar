// The back-seat screen's page (share/rear.html). Reads /api/rear/state, draws
// five cards, and can do exactly two things: ask the driver (a fixed list) and,
// when the owner has allowed it, skip a song. Words and rules are in
// rearlogic.js, which is what the tests hold.
import { howLong, musicView, cabinView, askView, askAnswer, isStale, breath, PALETTES } from "./rearlogic.js";

const $ = (id) => document.getElementById(id);
const K = (() => {
  try { return new URLSearchParams(location.search).get("k") || sessionStorage.getItem("omacar.rear.k") || ""; }
  catch { return ""; }
})();
try { if (K) sessionStorage.setItem("omacar.rear.k", K); } catch { /* private mode */ }

async function call(path, body) {
  const headers = K ? { Authorization: "Bearer " + K } : {};
  const opts = { cache: "no-store", headers };
  if (body) { opts.method = "POST"; opts.headers = { ...headers, "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  let j = null;
  try { j = await r.json(); } catch { /* not JSON */ }
  return { status: r.status, body: j };
}

let state = null, lastOk = null, askUntil = 0;

function set(id, text) { const e = $(id); if (e.textContent !== text) e.textContent = text; }

function paint() {
  if (!state) return;
  const hl = howLong(state.how_long);
  set("how-big", hl.big); set("how-unit", hl.unit); set("how-line", hl.line);
  $("how-sim").hidden = !hl.simulated;

  const m = musicView(state.music, state.control);
  set("song-title", m.title); set("song-artist", m.artist);
  $("skips").hidden = !m.skip;
  $("music-sim").hidden = !m.simulated;

  const c = cabinView(state.cabin);
  $("cabin").hidden = !c;
  $("rear").dataset.cabin = c ? "1" : "0";
  if (c) {
    set("cabin-big", c.big); set("cabin-line", c.line);
    $("cabin").dataset.tone = c.tone; $("cabin-sim").hidden = !c.simulated;
  }

  const a = askView(state.ask, askUntil, Date.now());
  $("ask-open").disabled = !a.enabled;
  set("ask-note", a.note);
  $("rear").dataset.lost = isStale(lastOk, Date.now()) ? "1" : "0";
  $("lost").hidden = !isStale(lastOk, Date.now());
}

async function poll() {
  try {
    const r = await call("/api/rear/state");
    if (r.status === 200 && r.body) { state = r.body; lastOk = Date.now(); }
  } catch { /* the "lost" line says so after a few seconds */ }
  paint();
}

// ---- ask the driver
const choices = $("choices");
function openSheet() {
  choices.textContent = "";
  for (const c of (state && state.ask && state.ask.choices) || []) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "key"; b.textContent = c.label.replace(/^./, (s) => s.toUpperCase());
    b.addEventListener("click", () => send(c.id));
    choices.appendChild(b);
  }
  $("sheet").hidden = false;
}
async function send(what) {
  $("sheet").hidden = true;
  let r;
  try { r = await call("/api/rear/ask", { what }); } catch { r = { status: 0, body: null }; }
  const a = askAnswer(r.status, r.body);
  if (a.wait) askUntil = Date.now() + a.wait * 1000;
  set("ask-note", a.text);
  if (a.ok) $("ask-open").disabled = true;
  setTimeout(poll, 1500);
}
$("ask-open").addEventListener("click", openSheet);
$("sheet-close").addEventListener("click", () => { $("sheet").hidden = true; });

// ---- the music keys (visible only when the owner allowed them)
for (const [id, cmd] of [["prev", "previous"], ["next", "next"]]) {
  $(id).addEventListener("click", async () => { try { await call("/api/rear/music", { cmd }); } catch { /* ignore */ } setTimeout(poll, 800); });
}

// ---- calm time: slow bubbles and a breathing circle. Nothing flashes.
const stage = $("calm"), cv = $("calm-canvas"), g = cv.getContext("2d");
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
let pal = 0, raf = 0, bubbles = [], t0 = 0, lock = null;

function size() {
  const d = Math.min(devicePixelRatio || 1, 2);
  cv.width = Math.round(innerWidth * d); cv.height = Math.round(innerHeight * d);
  g.setTransform(d, 0, 0, d, 0, 0);
}
function spawn(x, y) {
  const P = PALETTES[pal];
  bubbles.push({ x: x ?? Math.random() * innerWidth, y: y ?? innerHeight + 40, r: 18 + Math.random() * 46,
    vy: 14 + Math.random() * 22, sway: Math.random() * 6.28, col: P.orbs[(Math.random() * P.orbs.length) | 0], born: performance.now() });
  if (bubbles.length > 28) bubbles.shift();
}
function frame(now) {
  const t = (now - t0) / 1000, P = PALETTES[pal];
  const grad = g.createLinearGradient(0, 0, 0, innerHeight);
  grad.addColorStop(0, P.bg[0]); grad.addColorStop(1, P.bg[1]);
  g.fillStyle = grad; g.fillRect(0, 0, innerWidth, innerHeight);
  if (!still && bubbles.length < 14 && Math.random() < 0.03) spawn();
  for (const b of bubbles) {
    if (!still) { b.y -= b.vy * 0.016; b.x += Math.sin(t * 0.6 + b.sway) * 0.25; }
    const rg = g.createRadialGradient(b.x - b.r * .3, b.y - b.r * .3, b.r * .1, b.x, b.y, b.r);
    rg.addColorStop(0, "#ffffff66"); rg.addColorStop(0.55, b.col + "44"); rg.addColorStop(1, b.col + "10");
    g.fillStyle = rg; g.beginPath(); g.arc(b.x, b.y, b.r, 0, 6.2832); g.fill();
  }
  bubbles = bubbles.filter((b) => b.y > -b.r * 2);
  const br = breath(t), R = Math.min(innerWidth, innerHeight) * (0.12 + 0.1 * br.scale);
  const cg = g.createRadialGradient(innerWidth / 2, innerHeight / 2, R * .2, innerWidth / 2, innerHeight / 2, R * 1.5);
  cg.addColorStop(0, P.orbs[0] + "55"); cg.addColorStop(1, P.orbs[0] + "00");
  g.fillStyle = cg; g.beginPath(); g.arc(innerWidth / 2, innerHeight / 2, R * 1.5, 0, 6.2832); g.fill();
  $("breath").textContent = still ? "" : br.word;
  raf = requestAnimationFrame(frame);
}
async function openCalm() {
  size(); bubbles = []; for (let i = 0; i < 9; i++) spawn(undefined, Math.random() * innerHeight);
  stage.hidden = false; t0 = performance.now(); raf = requestAnimationFrame(frame);
  try { lock = await navigator.wakeLock.request("screen"); } catch { /* not supported */ }
}
function closeCalm() {
  stage.hidden = true; cancelAnimationFrame(raf);
  try { lock && lock.release(); } catch { /* gone */ } lock = null;
}
$("calm-open").addEventListener("click", openCalm);
$("calm-close").addEventListener("click", closeCalm);
$("calm-colors").addEventListener("click", () => { pal = (pal + 1) % PALETTES.length; });
cv.addEventListener("pointerdown", (e) => { for (let i = 0; i < 3; i++) spawn(e.clientX + (i - 1) * 30, e.clientY); });
addEventListener("resize", () => { if (!stage.hidden) size(); });

poll();
setInterval(poll, 3000);
setInterval(paint, 1000);
