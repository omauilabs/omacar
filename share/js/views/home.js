// Home: the screen the tablet shows when somebody gets in.
//
// Cards in a grid, in the order and at the sizes the layout says
// (share/data/home-cards.json defines the cards; the owner's arrangement comes
// from the server in the layout editor's task). Every number comes from the same
// readings catalogue drive mode uses and says where it came from: a tile that
// has no value draws words, never a zero.

import { h, clear, icon, store, api } from "../core.js";
import { ICONS } from "../icons.js";
import { READINGS, readingState } from "../readings.js";
import { makeGauge } from "../gauges.js";
import { makeSignalTile } from "../sigtile.js";
import { footerLine, sourceKey } from "../provenance.js";
import { asset } from "../assets.js";
import { tyreState } from "../systems.js";
import { loadCatalogue, orientation, spanOf, defaultLayout } from "../homecards.js";
import { savedLook, mountLookEffect } from "../looks.js";
import { startEditing } from "../homeedit.js";

const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };
// Only ever called from a tap: the routing rule in main.js.
const go = (id) => { location.hash = "#" + id; };

function tappable(node, id) {
  node.setAttribute("role", "button");
  node.tabIndex = 0;
  node.addEventListener("click", () => { if (!node.closest(".editing")) go(id); });
  node.addEventListener("keydown", (e) => { if (e.key === "Enter") go(id); });
  return node;
}

// ---- the cards ---------------------------------------------------------------

function dialCard() {
  const speed = READINGS.speed;
  const rpmReading = READINGS.rpm;
  const g = makeGauge("arc", { scale: speed.scale() });
  const rpm = h("div.dial-rpm");
  // A LABEL, NOT A READING: no identifier reports the drive mode, so this is
  // the one the driver last marked on the drive screen, and it says so.
  const mode = h("span.pill.dial-mode.mode-pill", { hidden: true,
    title: "The drive mode you last marked. OmaCar cannot read it from the car." });
  const begin = h("button.btn.dial-begin", { type: "button", hidden: true,
    onclick: (e) => { e.stopPropagation(); go("launcher"); } }, "Begin");
  const node = tappable(h("div.card.hc.hc-dial", g.el, rpm, mode, begin), "drive");
  const paintMode = (m) => {
    mode.hidden = !m;
    if (m) { text(mode, String(m).toUpperCase()); mode.dataset.mode = m; }
  };
  const onMode = (e) => paintMode((e && e.detail) || null);
  document.addEventListener("omacar:drivemode", onMode);
  let asked = 0;
  return {
    node,
    paint() {
      const s = store.sample, v = store.values, car = store.car;
      g.update(speed.get(v, s, car), speed.read(v, s, car));
      // THE DIAL NAMES ITS SOURCE LIKE ANY OTHER LIVE NUMBER. Speed and RPM
      // are readings like a sig tile's, just drawn on a needle and in a div
      // instead of in a box -- so each carries the same data-state/data-src
      // pair, set the same way sigtile.js sets it (readingState + sourceKey),
      // so this card cannot draw a live number without saying where it came
      // from. Never a value with no state, or a state with no source.
      const speedState = readingState(speed, s);
      g.el.dataset.state = speedState;
      if (speedState === "live") g.el.dataset.src = sourceKey(car, s);
      else delete g.el.dataset.src;
      const rpmState = readingState(rpmReading, s);
      rpm.dataset.state = rpmState;
      if (rpmState === "live") rpm.dataset.src = sourceKey(car, s);
      else delete rpm.dataset.src;
      // NAME THE SERVER, NEVER THE CAR. store.noServer means OmaCar cannot
      // reach its own server, at boot or at any point after it -- nothing
      // below this line knows anything about a vehicle, so a word about the
      // car would send somebody to the OBD cable when the fix is
      // `omacar server status`.
      const noServer = store.noServer;
      text(rpm, noServer ? "No server"
        : store.connected ? `${rpmReading.get(v, s, car).v} rpm` : "Car off");
      begin.hidden = store.connected || noServer;
      if (Date.now() - asked > 30000) {
        asked = Date.now();
        api.driveMode().then((d) => paintMode(d && d.mode)).catch(() => {});
      }
    },
    destroy() { document.removeEventListener("omacar:drivemode", onMode); },
  };
}

function carCard() {
  const img = h("img.car-img", { alt: "", hidden: true, draggable: "false" });
  const slot = h("div.car-slot", h("span", "Your car's picture goes here"));
  const tyre = h("div.callout", { data: { tone: "" } },
    h("span.co-dot"), h("span.co-k", "Tyres"), h("span.co-v"));
  const node = h("div.card.hc.hc-car", h("div.car-stage", img, slot, tyre));
  asset("crz-home").then((a) => {
    if (a && a.url) { img.src = a.url; img.hidden = false; slot.hidden = true; }
    const at = (a && a.anchors && a.anchors.tyres) || [0.5, 0.9];
    tyre.style.left = (at[0] * 100) + "%";
    tyre.style.top = (at[1] * 100) + "%";
  });
  return {
    node,
    paint() {
      const st = tyreState(store.car);
      tyre.dataset.tone = st.tone;
      text(tyre.querySelector(".co-v"), st.text);
    },
  };
}

function soonCard(ico, title, line, id) {
  const node = tappable(h("div.card.hc",
    h("div.hc-ph", icon(ico, 28), h("div.hc-ph-t", title), h("div.hc-ph-s", line))), id);
  return { node, paint() {} };
}

function phoneCard() {
  const chip = (label, id) => h("button.hc-chip", { type: "button",
    onclick: (e) => { e.stopPropagation(); if (!e.currentTarget.closest(".editing")) go(id); } }, label);
  const nursery = chip("Nursery", "nursery");
  const node = h("div.card.hc.hc-phone",
    h("div.hc-title", icon(ICONS.phone, 18), "Phone integration"),
    h("button.hc-row", { type: "button",
      onclick: (e) => { e.stopPropagation(); if (!e.currentTarget.closest(".editing")) go("omaplay"); } },
      icon(ICONS.phone, 22),
      h("span", "Apple CarPlay", h("span.sub", "Your phone's screen, through the adapter")),
      h("span.chev", icon(ICONS.chevron, 18))),
    h("div.hc-chips", chip("Music", "music"), nursery));
  return { node, paint() { nursery.hidden = !store.nurseryOn; } };
}

function agentCard() {
  const q = h("div.ag-q", { hidden: true });
  const a = h("div.ag-a", { hidden: true });
  const empty = h("div.hc-ph-s", "Ask about your car: what a code means, what is due, how a drive went.");
  const node = tappable(h("div.card.hc.hc-agent",
    h("div.hc-title", icon(ICONS.agent, 18), "Oma Agent", h("span.chev", icon(ICONS.chevron, 18))),
    q, a, empty), "advisor");
  let asked = 0;
  return {
    node,
    paint() {
      if (Date.now() - asked < 60000) return;
      asked = Date.now();
      api.aiHistory().then((d) => {
        const r = ((d && d.records) || [])[0];
        const p = r && r.payload;
        const said = p && p.data && (p.data.headline || p.data.answer || p.data.summary);
        q.hidden = a.hidden = !said;
        empty.hidden = !!said;
        if (said) {
          text(q, p.question || p.code || (r.label || "").replace(/^[a-z]+:\s*/, ""));
          text(a, said);
        }
      }).catch(() => { q.hidden = a.hidden = true; empty.hidden = false; });
    },
  };
}

const MAKERS = {
  dial: dialCard,
  car: carCard,
  nav: () => soonCard(ICONS.nav, "Navigation", "Offline maps and turn-by-turn arrive with the navigation step.", "navigation"),
  dashcam: () => soonCard(ICONS.camera, "Dashcams", "Front, rear and cabin recording arrive with the cameras step.", "cameras"),
  phone: phoneCard,
  agent: agentCard,
};

function makeCard(id, cat) {
  const c = cat.cards[id];
  if (!c) return null;
  if (c.reading) {
    const t = makeSignalTile(c.reading, { label: c.label });
    t.node.classList.add("card", "hc");
    return { node: t.node, paint: () => t.paint(store.car, store.sample) };
  }
  return MAKERS[id] ? MAKERS[id]() : null;
}

// ---- the view ---------------------------------------------------------------

export default function home(root) {
  let alive = true;
  let cat = null;
  let layout = null;
  const made = new Map();
  const fx = h("div.home-fx");
  const grid = h("div.home-grid");
  const editBar = h("div.home-editbar", { hidden: true });
  const prov = h("span.home-prov");
  const custom = h("button.home-custom", { type: "button", hidden: true },
    icon(ICONS.layout, 18), "Customize layout");
  root.appendChild(h("div.home", fx, editBar, grid, h("div.home-foot", prov, custom)));

  // ---- the look's background, behind the grid, while parked -----------------
  //
  // The design says nothing moves while the car moves except values, so the
  // animated look stops the instant the car starts rolling and resumes the
  // instant it is not. Decided on a TRANSITION only -- never torn down and
  // rebuilt on every live sample, which is exactly the mistake the hub's own
  // comments warn against for everything else on this screen.
  let fxStop = null;
  // null until the first check decides it, so that first check always runs
  // even if the car happens to already be moving when Home mounts.
  let driving = null;

  function fxOn() { if (!fxStop) fxStop = mountLookEffect(fx, savedLook()); }
  function fxOff() { if (fxStop) { fxStop(); fxStop = null; } }

  function syncFx() {
    const now = store.state === "driving";
    if (now === driving) return;
    driving = now;
    if (driving) fxOff(); else fxOn();
  }

  // THE LOOK ITSELF CHANGED (Settings -> Look), which is a different question
  // from whether the car is moving. Remount with whatever is current now,
  // but only if something should be showing at all.
  const onLook = () => { fxOff(); if (!driving) fxOn(); };
  document.addEventListener("omacar:look", onLook);
  syncFx();

  // PLACED BY MOVING NODES, NEVER BY REBUILDING THEM. appendChild on a node
  // that is already here moves it, so reordering keeps every card's state
  // (its gauge, its image, its listeners) and nothing is rebuilt under a thumb.
  function place(lay = layout) {
    const o = orientation();
    const want = [];
    for (const [id, size] of lay[o].cards) {
      let c = made.get(id);
      if (!c) {
        c = makeCard(id, cat);
        if (!c) continue;
        made.set(id, c);
      }
      const [cols, rows] = spanOf(cat, id, size, o);
      c.node.style.gridColumn = `span ${cols}`;
      c.node.style.gridRow = `span ${rows}`;
      c.node.dataset.card = id;
      c.node.dataset.size = size;
      want.push(c.node);
    }
    for (const n of [...grid.children]) if (!want.includes(n)) n.remove();
    for (const n of want) grid.appendChild(n);
    paint();
  }

  function paint() {
    syncFx();
    if (!alive || !layout) return;
    for (const c of made.values()) if (c.node.isConnected) c.paint();
    // NAME THE SERVER, NEVER THE CAR (see dialCard()). footerLine() describes
    // a vehicle's data; with no server there is no vehicle to describe.
    const noServer = store.noServer;
    text(prov, noServer ? "OmaCar cannot reach its own server — omacar server status"
                        : footerLine(store.car, store.live));
  }

  const offLive = store.on("live", paint);
  const offCar = store.on("car", paint);
  const mq = matchMedia("(orientation: portrait)");
  const onTurn = () => { if (layout) place(); };
  mq.addEventListener("change", onTurn);

  // ---- customising: the button, or a long press on any card -------------
  let editor = null;
  function customise() {
    if (editor || !layout || !cat) return;
    editor = startEditing({
      grid, bar: editBar, cat, layout, orient: orientation, place,
      save: async (w) => { layout = await api.saveHome(w); return layout; },
      onEnd: () => { editor = null; },
    });
  }
  custom.hidden = false;
  custom.addEventListener("click", customise);
  let press = null;
  const unpress = () => { if (press) { clearTimeout(press.t); press = null; } };
  grid.addEventListener("pointerdown", (e) => {
    if (editor) return;
    press = { x: e.clientX, y: e.clientY, t: setTimeout(() => { press = null; customise(); }, 600) };
  });
  grid.addEventListener("pointermove", (e) => {
    if (press && Math.hypot(e.clientX - press.x, e.clientY - press.y) > 10) unpress();
  });
  grid.addEventListener("pointerup", unpress);
  grid.addEventListener("pointercancel", unpress);

  (async () => {
    cat = await loadCatalogue();
    // The owner's arrangement from the server; the catalogue's default if the
    // server is older than this screen or unreachable.
    layout = await api.home().catch(() => null) || defaultLayout(cat);
    if (alive) place();
  })().catch((e) => {
    clear(grid);
    grid.appendChild(h("div.card.tint-bad", h("div.title", "Home could not load its cards"),
      h("p.lede", String((e && e.message) || e))));
  });

  return () => {
    if (editor) editor.finish(false);
    alive = false;
    offLive();
    offCar();
    mq.removeEventListener("change", onTurn);
    document.removeEventListener("omacar:look", onLook);
    fxOff();
    for (const c of made.values()) if (c.destroy) c.destroy();
  };
}
