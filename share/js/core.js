// The three things every view needs: a way to build DOM, a way to format a
// number, and the one copy of the car's state.
//
// No framework. The whole app is small enough that a render function returning
// an element is simpler than anything that would replace it, and a diagnostic
// tool should not stop working the day a dependency does.

// ---------------------------------------------------------------- DOM
// h("div.card", {onclick}, child, child) — the terse constructor the views use.
export function h(spec, props, ...kids) {
  const [tag, ...cls] = String(spec).split(".");
  const el = document.createElement(tag || "div");
  if (cls.length) el.className = cls.join(" ");
  if (props && (props.nodeType || typeof props === "string" || Array.isArray(props))) {
    kids.unshift(props);
    props = null;
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className += (el.className ? " " : "") + v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "data" && typeof v === "object")
      for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = dv;
    else el.setAttribute(k, v === true ? "" : v);
  }
  add(el, kids);
  return el;
}

function add(el, kids) {
  for (const k of kids.flat(4)) {
    if (k === null || k === undefined || k === false || k === "") continue;
    el.appendChild(k.nodeType ? k : document.createTextNode(String(k)));
  }
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

// An inline SVG icon from a path list. Icons are drawn rather than fetched so
// the tool has no assets to lose.
export function icon(paths, size = 20) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size); svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const d of [].concat(paths)) {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
  }
  return svg;
}

// ---------------------------------------------------------------- numbers
const MPG_K = 235.214583;

export const U = {
  units: { system: "imperial", dist: "mi", speed: "mph", econ: "mpg",
           vol: "gal", temp: "°F", km: 0.621371, litre: 0.264172,
           econ_better: "high" },
  set(u) { if (u) this.units = u; },
  get imperial() { return this.units.system === "imperial"; },
};

export function num(v) { return v === null || v === undefined || Number.isNaN(v) ? null : Number(v); }

export function grouped(v) {
  const n = num(v);
  if (n === null) return "—";
  return Math.round(n).toLocaleString("en-US");
}

export function dist(km, withUnit = true) {
  const n = num(km);
  if (n === null) return "—";
  const d = n * U.units.km;
  const t = Math.abs(d) >= 1000 ? grouped(d) : Math.abs(d) >= 100 ? String(Math.round(d)) : d.toFixed(1);
  return withUnit ? `${t} ${U.units.dist}` : t;
}

export function speed(kph, withUnit = true) {
  const n = num(kph);
  if (n === null) return "—";
  const s = Math.round(n * U.units.km);
  return withUnit ? `${s} ${U.units.speed}` : String(s);
}

export function vol(l, withUnit = true) {
  const n = num(l);
  if (n === null) return "—";
  const v = n * U.units.litre;
  return (v >= 100 ? Math.round(v).toLocaleString("en-US") : v.toFixed(1)) + (withUnit ? ` ${U.units.vol}` : "");
}

export function temp(c, withUnit = true) {
  const n = num(c);
  if (n === null) return "—";
  const t = U.imperial ? n * 9 / 5 + 32 : n;
  return Math.round(t) + (withUnit ? ` ${U.units.temp}` : "");
}

// Consumption is a reciprocal, not a scale: a car burning nothing has infinite
// miles per gallon. Missing stays missing rather than becoming a huge number.
export function econVal(lphk) {
  const n = num(lphk);
  if (n === null || n <= 0) return null;
  return U.imperial ? MPG_K / n : n;
}

export function econ(lphk, withUnit = true) {
  const v = econVal(lphk);
  if (v === null) return "—";
  return v.toFixed(1) + (withUnit ? ` ${U.units.econ}` : "");
}

// Which way is good. Litres per hundred wants to be low, miles per gallon
// wants to be high, so the same improvement changes sign with the unit and
// nothing may assume a direction.
export function econDelta(now, before) {
  const a = econVal(now), b = econVal(before);
  if (a === null || b === null) return null;
  const d = a - b;
  if (Math.abs(d) < 0.15) return { arrow: "=", text: "", tone: "" };
  const up = d > 0;
  const good = U.units.econ_better === "high" ? up : !up;
  return { arrow: up ? "↑" : "↓", text: Math.abs(d).toFixed(1), tone: good ? "ok" : "warn" };
}

export function money(v) {
  const n = num(v);
  return n === null ? "" : "$" + (n >= 1000 ? grouped(n) : n.toFixed(2));
}

export function pct(v) {
  const n = num(v);
  return n === null ? "—" : Math.round(n) + "%";
}

export function mins(secs) {
  const m = Math.round((num(secs) || 0) / 60);
  if (m < 60) return m + " min";
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function shortDate(secs) {
  if (!secs) return "";
  const d = new Date(secs * 1000);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

export function fullDate(secs) {
  if (!secs) return "";
  const d = new Date(secs * 1000);
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export function clockOf(secs) {
  const d = new Date(secs * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function isoDate(iso) {
  if (!iso) return "";
  const p = String(iso).split("-");
  if (p.length < 3) return String(iso);
  return `${parseInt(p[2], 10)} ${MONTHS[parseInt(p[1], 10) - 1]} ${p[0]}`;
}

export function since(secs) {
  if (secs === null || secs === undefined) return "";
  const s = Math.max(0, Math.floor(secs));
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 172800) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const MONTH_NAMES = MONTHS;

// Remaining service life, on Honda's own countdown: 15% is book it, 5% is now,
// nought is past due. Inverted against every other percentage in the tool,
// because here a low number is the bad one.
export function lifeTone(life) {
  if (life === null || life === undefined) return "";
  if (life <= 0) return "bad";
  if (life <= 15) return "warn";
  return "ok";
}

export function sevTone(sev) {
  return sev === "critical" ? "bad" : sev === "warning" ? "warn" : "";
}

// ---------------------------------------------------------------- transport
// In cockpit mode the page is opened with ?k=<token> and every request needs
// it. Kept out of the visible URL after the first load so the token does not
// sit in a screenshot of the dashboard.
const TOKEN = (() => {
  const p = new URLSearchParams(location.search);
  const k = p.get("k");
  if (k) {
    try { sessionStorage.setItem("omacar.k", k); } catch { /* private mode */ }
    return k;
  }
  try { return sessionStorage.getItem("omacar.k") || ""; } catch { return ""; }
})();

export const readOnly = TOKEN !== "";

function withToken(path) {
  if (!TOKEN) return path;
  return path + (path.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(TOKEN);
}

async function req(path, opts) {
  const r = await fetch(withToken(path), Object.assign({ cache: "no-store" }, opts || {}));
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).error || msg; } catch { /* body was not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  snapshot: () => req("/api/snapshot"),
  live: () => req("/api/live"),
  // The two routes the Connect button rests on. Everything that knows their
  // shape is in the "connecting" section below, so a change on the server side
  // is one edit here rather than three across the views.
  adapter: () => req("/api/adapter"),
  daemon: (action) => req("/api/daemon", { method: "POST", body: JSON.stringify({ action }) }),
  history: (q) => req("/api/history?" + new URLSearchParams(q)),
  trips: (n) => req("/api/trips?n=" + (n || 20)),
  records: (q) => req("/api/records?" + new URLSearchParams(q || {})),
  alerts: (n = 40) => req("/api/records?kind=alert&n=" + n),
  scan: () => req("/api/scan", { method: "POST" }),
  clear: (module, headers) => req("/api/clear", { method: "POST", body: JSON.stringify({ module, headers }) }),
  resets: () => req("/api/resets"),
  procedures: () => req("/api/procedures"),
  documents: (kind) => req("/api/documents" + (kind ? "?kind=" + kind : "")),
  serviceHistory: () => req("/api/service-history"),
  plugins: () => req("/api/plugins"),
  // No timeout wrapper: a parse OCRs a page and then asks the advisor.
  document: (body) => req("/api/document", { method: "POST", body: JSON.stringify(body) }),
  runReset: (id, header) => req("/api/reset", { method: "POST", body: JSON.stringify({ id, header }) }),
  saveRecording: (label, from, to) =>
    req("/api/record", { method: "POST", body: JSON.stringify({ label, from, to }) }),
  setUnits: (system) => req("/api/units", { method: "POST", body: JSON.stringify({ system }) }),
  mode: () => req("/api/mode"),
  setMode: (tier) => req("/api/mode", { method: "POST", body: JSON.stringify({ tier }) }),
  actuate: (body) => req("/api/actuate", { method: "POST", body: JSON.stringify(body) }),
  concerns: () => req("/api/concerns"),
  photos: (q) => req("/api/photos?" + new URLSearchParams(q || {})),
  photo: (body) => req("/api/photo", { method: "POST", body: JSON.stringify(body) }),
  snapshots: (n = 40) => req("/api/snapshots?n=" + n),
  capture: (body) => req("/api/snapshot", { method: "POST", body: JSON.stringify(body || {}) }),
  vehicles: () => req("/api/vehicles"),
  learned: () => req("/api/learned"),
  // No timeout wrapper here on purpose: a learning pass probes ten module
  // addresses and legitimately takes the best part of a minute.
  learn: (deep) => req("/api/learn", { method: "POST", body: JSON.stringify({ deep: !!deep }) }),
  writeMode: (arm, minutes) => req("/api/write-mode", { method: "POST", body: JSON.stringify({ arm, minutes }) }),
  writeDid: (body) => req("/api/write-did", { method: "POST", body: JSON.stringify(body) }),
  vehicle: (body) => req("/api/vehicle", { method: "POST", body: JSON.stringify(body) }),
  theme: () => req("/api/theme"),
  themes: () => req("/api/themes"),
  themesDo: (body) => req("/api/themes", { method: "POST", body: JSON.stringify(body) }),
  setOdometer: (km) => req("/api/odometer", { method: "POST", body: JSON.stringify({ km }) }),
  service: (body) => req("/api/service", { method: "POST", body: JSON.stringify(body) }),
  driveLayout: () => req("/api/drive"),
  saveDriveLayout: (body) => req("/api/drive", { method: "POST", body: JSON.stringify(body) }),
  aiAvailable: () => req("/api/ai/available"),
  aiStart: (body) => req("/api/ai", { method: "POST", body: JSON.stringify(body) }),
  aiPoll: (id) => req("/api/ai?job=" + encodeURIComponent(id)),
  aiHistory: () => req("/api/ai/history"),
};

// ---------------------------------------------------------------- the store
// One object, one event. Views subscribe and re-render; nothing reaches into
// anything else's DOM.
class Store extends EventTarget {
  constructor() {
    super();
    this.car = null;          // the full snapshot, refreshed slowly
    this.live = null;         // the current sample, refreshed fast
    this.knowledge = null;    // dtc.json
    this.aiOn = false;
    this.error = null;
  }
  emit(what) { this.dispatchEvent(new CustomEvent(what)); }
  on(what, fn) { this.addEventListener(what, fn); return () => this.removeEventListener(what, fn); }

  async boot() {
    const [car, kb, ai] = await Promise.allSettled([
      api.snapshot(),
      fetch("data/dtc.json", { cache: "no-store" }).then((r) => r.json()),
      api.aiAvailable(),
    ]);
    if (car.status === "fulfilled") { this.car = car.value; U.set(car.value.units); }
    else this.error = String(car.reason);
    this.knowledge = kb.status === "fulfilled" ? kb.value : {};
    this.aiOn = ai.status === "fulfilled" && ai.value.available;
    this.emit("car");
  }

  async refreshCar() {
    try {
      this.car = await api.snapshot();
      U.set(this.car.units);
      this.error = null;
    } catch (e) { this.error = String(e.message || e); }
    this.emit("car");
  }

  async refreshLive() {
    try { this.live = await api.live(); } catch { this.live = null; }
    this.emit("live");
  }

  // The current sample if the fast poller has one, the snapshot's copy if not,
  // so a view is right the moment it mounts rather than after the first tick.
  get sample() { return this.live || (this.car && this.car.live) || {}; }
  get values() { return this.sample.values || {}; }
  get connected() { return !!this.sample.connected; }
  get state() {
    if (!this.connected) return "offline";
    const v = this.values;
    if ((v.SPEED || 0) > 3) return "driving";
    if ((v.RPM || 0) > 200) return "idling";
    return "parked";
  }
}

export const store = new Store();

// ---------------------------------------------------------------- connecting
// One button, and the whole contract behind it in one place.
//
// The empty states used to read "no daemon — run: omacar daemon start", which
// tells somebody standing at a car to leave the app, find a terminal and type
// a command the app could perfectly well have run for them. The views now ask
// for a connection instead, and every assumption about how the server offers
// one lives here rather than being spread across the screens that need it.
//
// The contract, as the server exposes it:
//   GET  /api/adapter -> { port, kind, name, warning, running }
//        port     the device connect.resolve() found, or null when there is none
//        kind     "wired" | "bench" | "override", as resolve() reports it
//        name     an optional human description ("FTDI adapter"); absent is fine
//        warning  connect.serial_group_warning(port), or null
//        running  whether a daemon is already up
//   POST /api/daemon { action: "start" | "stop" } -> 2xx once the daemon is
//        alive, otherwise the usual { error: "..." } with a 4xx or 5xx, which
//        req() has already turned into the message on the thrown Error.
// Anything missing from the GET degrades to a plain "Connect" button, so a
// server that does not carry the route yet is a smaller label rather than a
// broken screen.

// Words for the kinds resolve() can report, for when the server does not send
// a friendlier name of its own.
const ADAPTER_KINDS = { bench: "bench emulator", override: "port override" };

export async function adapterState() {
  try {
    const a = await api.adapter();
    return {
      known: true,
      port: a.port || null,
      kind: a.kind || null,
      name: a.name || ADAPTER_KINDS[a.kind] || null,
      warning: a.warning || null,
      running: !!a.running,
    };
  } catch {
    // An older server, or a route that is not there. Not knowing which port we
    // would use is no reason to hide the button — connecting still works, the
    // label just cannot name the hardware.
    return { known: false, port: null, kind: null, name: null, warning: null, running: false };
  }
}

// Naming the port on the button is the difference between a promise and
// evidence: the tool has already looked, and it is telling you what it found.
export function adapterLabel(a) {
  if (!a || !a.port) return "Connect";
  return (a.name ? `${a.name} on ${a.port}` : a.port) + " — Connect";
}

// `omacar daemon start` waits about ten seconds for a pidfile, and a cold
// adapter can spend several more detecting its baud rate before the first
// sample lands. Twenty-two seconds is long enough for the slow honest case and
// short enough that a dead cable does not leave a spinner up indefinitely.
const CONNECT_TIMEOUT_MS = 22000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// One calm sentence for each way this goes wrong. The server's own refusals are
// already written in this voice — the serial group hint in particular is the
// exact sentence that fixes the most common failure on Arch — so anything not
// recognised here is passed through unchanged rather than flattened into a
// generic failure the user can do nothing with.
function connectFailure(msg) {
  // The CLI prefixes its own refusals with "omacar:", which is right in a
  // terminal and reads as debug output on a dashboard.
  const raw = String(msg || "").replace(/^omacar:\s*/i, "").trim();
  const m = raw.toLowerCase();
  if (m.includes("no adapter") || m.includes("no port"))
    return "No adapter found. Plug the cable into the car's port and try again.";
  if (m.includes("simulat"))
    return "The simulator is running. Stop it first, then connect.";
  if (m.includes("group") || m.includes("readable") || m.includes("permission"))
    return raw;
  if (m.includes("read-only") || m.includes("cockpit") || m === "403")
    return "This screen is read-only. Connect from the machine OmaCar is running on.";
  if (m === "404")
    return "This copy of the server cannot start the daemon yet.";
  if (m.includes("did not start") || m.includes("failed to start") || m.includes("could not start"))
    return "The daemon did not start.";
  return raw ? `The daemon did not start — ${raw}` : "The daemon did not start.";
}

// Ask for a connection and wait until the car is actually answering.
//
// Success is judged on live data, not on the exit status of the start command:
// the daemon is alive well before the car has said anything, and a button that
// goes green on "the process exists" is the kind of dishonesty this tool is
// meant not to have. Resolves to { ok, message, adapter } and never rejects —
// every failure is a sentence the caller can put on screen.
export async function connectCar(onStep) {
  const step = typeof onStep === "function" ? onStep : () => {};
  const adapter = await adapterState();
  // Two refusals worth making without shelling anything, because both are
  // certain and both would otherwise cost the user the full timeout first.
  if (adapter.known && !adapter.port)
    return { ok: false, adapter,
             message: "No adapter found. Plug the cable into the car's OBD-II port and try again." };
  if (adapter.warning)
    return { ok: false, adapter, message: adapter.warning };

  step("Connecting…");
  try {
    await api.daemon("start");
  } catch (e) {
    const msg = String((e && e.message) || e);
    // A daemon that is already up is not a failure, it is the state we were
    // asking for. Fall through and let the car decide whether we are connected.
    if (!/already/i.test(msg)) return { ok: false, adapter, message: connectFailure(msg) };
  }

  const until = Date.now() + CONNECT_TIMEOUT_MS;
  while (Date.now() < until) {
    await sleep(700);
    await store.refreshLive();
    if (store.connected) return { ok: true, adapter, message: "" };
  }

  // Started, still nothing. The daemon publishes what it is waiting for, so
  // repeat that rather than inventing a diagnosis of our own.
  const status = String((store.sample && store.sample.status) || "");
  if (/wait|ignition|asleep/i.test(status))
    return { ok: false, adapter,
             message: "The adapter is connected but the car is not answering. Turn the ignition on." };
  return { ok: false, adapter,
           message: status ? `Not connected yet — ${status}`
                           : "Connected to the adapter, but the car has not answered. Check the plug is seated in the car's port." };
}

// ---------------------------------------------------------------- toasts
export function toast(msg, tone) {
  const host = document.getElementById("toasts");
  const el = h("div.toast" + (tone ? "." + tone : ""), msg);
  host.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 4200);
  setTimeout(() => el.remove(), 4600);
}

export function confirmDialog({ title, body, confirm = "Confirm", tone = "danger" }) {
  return new Promise((resolve) => {
    const host = document.getElementById("modal-host");
    const close = (v) => { host.hidden = true; clear(host); resolve(v); };
    const box = h("div.modal", { role: "dialog", "aria-modal": "true" },
      h("div.title", title),
      typeof body === "string" ? h("p.lede", body) : body,
      h("div.row", { style: { justifyContent: "flex-end" } },
        h("button.btn", { onclick: () => close(false) }, "Cancel"),
        h("button.btn." + tone, { onclick: () => close(true) }, confirm)));
    clear(host); host.appendChild(box); host.hidden = false;
    host.onclick = (e) => { if (e.target === host) close(false); };
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { document.removeEventListener("keydown", esc); close(false); }
    });
    box.querySelector(".btn." + tone).focus();
  });
}
