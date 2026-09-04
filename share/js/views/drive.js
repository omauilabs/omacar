// Drive mode: the screen that is on while the car is moving.
//
// Everything else in this app is a workshop tool — dense, read at a standstill,
// with a pointer. This is read at seventy miles an hour in a glance of under a
// second, so it obeys different rules: few numbers, type large enough to read
// at arm's length in daylight, touch targets the width of the screen, and an
// alert that takes the whole display because if the watchdog has something to
// say at speed it is the only thing worth looking at.
//
// What is on it is yours to choose. What is NOT negotiable is when you choose
// it: the editor is only offered when the car is stopped. A screen you can
// rearrange while driving is a screen you rearrange while driving.
//
// The layout lives on the server, not in this browser, so the arrangement you
// make at the kitchen table is the one the tablet in the car shows.

import { h, clear, store, api, U, temp, econ, dist, vol, grouped, mins,
         since, toast } from "../core.js";
import { KINDS, makeGauge, kindsFor, normaliseKind } from "../gauges.js";

const ACK_KEY = "omacar.ackAlert";

// ---------------------------------------------------------------- the catalogue
//
// Everything drive mode can show. `get` returns { v, n, tone } — the number,
// the note under it, and whether it wants colour. Adding a readout is adding
// one entry here; nothing else in the file knows what any of them are.
const TILES = {
  speed: {
    label: "Speed", hero: true,
    get: (v) => ({ v: num(v.SPEED, (x) => Math.round(x * U.units.km)), n: U.units.speed }),
    read: (v) => (raw(v.SPEED) === null ? null : raw(v.SPEED) * U.units.km),
    scale: () => ({ min: 0, max: U.imperial ? 140 : 220, step: U.imperial ? 20 : 40 }),
  },
  rpm: {
    label: "Engine speed", hero: true,
    get: (v) => ({ v: num(v.RPM, (x) => grouped(x)), n: "rpm",
                   tone: v.RPM > 6200 ? "bad" : v.RPM > 5500 ? "warn" : "" }),
    read: (v) => raw(v.RPM),
    // Labelled in thousands, as every tachometer is.
    scale: () => ({ min: 0, max: 7000, step: 1000,
                    tick: (x) => String(Math.round(x / 1000)),
                    bands: [{ from: 5500, to: 6200, tone: "warn" },
                            { from: 6200, tone: "bad" }] }),
  },
  econ_now: {
    label: "Economy",
    get: (v, s, car) => {
      const moving = (v.SPEED || 0) > 3;
      if (moving && s.economy_lphk) return { v: econ(s.economy_lphk, false), n: U.units.econ };
      const day = car && car.perf && car.perf.day;
      return { v: day ? econ(day.lphk, false) : "—", n: U.units.econ + " today" };
    },
  },
  econ_trip: {
    label: "Economy, trip",
    get: (v, s, car) => {
      const day = car && car.perf && car.perf.day;
      return { v: day ? econ(day.lphk, false) : "—", n: U.units.econ + " today" };
    },
  },
  coolant: {
    label: "Coolant",
    get: (v) => ({ v: num(v.COOLANT_TEMP, (x) => temp(x, false)), n: U.units.temp,
                   tone: v.COOLANT_TEMP > 105 ? "bad" : v.COOLANT_TEMP > 100 ? "warn" : "" }),
    read: (v) => asTemp(v.COOLANT_TEMP),
    scale: () => ({ min: asTemp(40), max: asTemp(120), step: U.imperial ? 40 : 20,
                    bands: [{ from: asTemp(100), to: asTemp(105), tone: "warn" },
                            { from: asTemp(105), tone: "bad" }] }),
  },
  intake: {
    label: "Intake air",
    get: (v) => ({ v: num(v.INTAKE_TEMP, (x) => temp(x, false)), n: U.units.temp }),
    read: (v) => asTemp(v.INTAKE_TEMP),
    scale: () => ({ min: asTemp(-10), max: asTemp(70), step: U.imperial ? 40 : 20 }),
  },
  ambient: {
    label: "Outside",
    get: (v) => ({ v: num(v.AMBIANT_AIR_TEMP, (x) => temp(x, false)), n: U.units.temp }),
    read: (v) => asTemp(v.AMBIANT_AIR_TEMP),
    scale: () => ({ min: asTemp(-20), max: asTemp(50), step: U.imperial ? 30 : 20 }),
  },
  volts: {
    label: "Battery",
    get: (v) => {
      const running = (v.RPM || 0) > 200;
      return { v: num(v.CONTROL_MODULE_VOLTAGE, (x) => x.toFixed(1)), n: "V",
               tone: running && v.CONTROL_MODULE_VOLTAGE < 12.6 ? "bad"
                   : running && v.CONTROL_MODULE_VOLTAGE < 13.2 ? "warn" : "" };
    },
    read: (v) => raw(v.CONTROL_MODULE_VOLTAGE),
    // The bands are the figures that are low WHETHER OR NOT the engine is
    // running. The tone above is the stricter, running-aware judgement; a
    // painted band cannot know, and a face that shouts at a healthy parked
    // battery is a face you learn to ignore.
    // Step 2, not 1: seven labels round a face this small ran "12 13 14"
    // together into one smear.
    scale: () => ({ min: 10, max: 16, step: 2, tick: (x) => String(Math.round(x)),
                    bands: [{ to: 11.8, tone: "bad" },
                            { from: 11.8, to: 12.4, tone: "warn" }] }),
  },
  fuel: {
    label: "Fuel",
    get: (v, s, car) => {
      const tank = car && car.vehicle && car.vehicle.tank_l;
      return { v: num(v.FUEL_LEVEL, (x) => Math.round(x)), n:
        tank && v.FUEL_LEVEL ? `%  ·  ${vol(tank * v.FUEL_LEVEL / 100)}` : "%",
        tone: v.FUEL_LEVEL < 10 ? "bad" : v.FUEL_LEVEL < 18 ? "warn" : "" };
    },
    read: (v) => raw(v.FUEL_LEVEL),
    scale: () => ({ ...pct(), bands: [{ to: 10, tone: "bad" },
                                      { from: 10, to: 18, tone: "warn" }] }),
  },
  range: {
    label: "Range",
    get: (v, s, car) => {
      // Distance to empty, from what is in the tank and how the car has
      // actually been driven — not a number the ECU reports.
      const tank = car && car.vehicle && car.vehicle.tank_l;
      const day = car && car.perf && car.perf.year;
      if (!tank || !v.FUEL_LEVEL || !day || !day.lphk) return { v: "—", n: U.units.dist };
      const litres = tank * v.FUEL_LEVEL / 100;
      return { v: dist(litres / day.lphk * 100, false), n: U.units.dist + " left",
               tone: litres / day.lphk * 100 < 60 ? "warn" : "" };
    },
  },
  load: {
    label: "Engine load",
    get: (v) => ({ v: num(v.ENGINE_LOAD, (x) => Math.round(x)), n: "%" }),
    read: (v) => raw(v.ENGINE_LOAD),
    scale: () => ({ ...pct(), bands: [{ from: 85, tone: "warn" }] }),
  },
  throttle: {
    label: "Throttle",
    get: (v) => ({ v: num(v.THROTTLE_POS, (x) => Math.round(x)), n: "%" }),
    read: (v) => raw(v.THROTTLE_POS),
    scale: () => pct(),
  },
  timing: {
    label: "Timing",
    get: (v) => ({ v: num(v.TIMING_ADVANCE, (x) => x.toFixed(0)), n: "°" }),
    read: (v) => raw(v.TIMING_ADVANCE),
    scale: () => ({ min: -10, max: 50, step: 10 }),
  },
  stft: {
    label: "Short trim",
    get: (v) => ({ v: num(v.SHORT_FUEL_TRIM_1, (x) => (x > 0 ? "+" : "") + x.toFixed(1)), n: "%",
                   tone: Math.abs(v.SHORT_FUEL_TRIM_1) > 15 ? "warn" : "" }),
    read: (v) => raw(v.SHORT_FUEL_TRIM_1),
    scale: () => ({ min: -25, max: 25, step: 10,
                    tick: (x) => (x > 0 ? "+" : "") + Math.round(x),
                    bands: [{ to: -15, tone: "bad" }, { from: -15, to: -10, tone: "warn" },
                            { from: 10, to: 15, tone: "warn" }, { from: 15, tone: "bad" }] }),
  },
  ltft: {
    label: "Long trim",
    get: (v) => ({ v: num(v.LONG_FUEL_TRIM_1, (x) => (x > 0 ? "+" : "") + x.toFixed(1)), n: "%",
                   tone: Math.abs(v.LONG_FUEL_TRIM_1) > 15 ? "warn" : "" }),
    read: (v) => raw(v.LONG_FUEL_TRIM_1),
    scale: () => ({ min: -25, max: 25, step: 10,
                    tick: (x) => (x > 0 ? "+" : "") + Math.round(x),
                    bands: [{ to: -15, tone: "bad" }, { from: -15, to: -10, tone: "warn" },
                            { from: 10, to: 15, tone: "warn" }, { from: 15, tone: "bad" }] }),
  },
  maf: {
    label: "Air flow",
    get: (v) => ({ v: num(v.MAF, (x) => x.toFixed(1)), n: "g/s" }),
    read: (v) => raw(v.MAF),
    scale: () => ({ min: 0, max: 120, step: 30 }),
  },
  today: {
    label: "Today",
    get: (v, s, car) => ({ v: car && car.perf && car.perf.day
      ? dist(car.perf.day.km, false) : "—", n: U.units.dist }),
  },
  odometer: {
    label: "Odometer",
    get: (v, s, car) => ({ v: car && car.odometer ? grouped(car.odometer * U.units.km) : "—",
                           n: U.units.dist }),
  },
  codes: {
    label: "Faults",
    get: (v, s, car) => {
      const n = car && car.active_faults ? car.active_faults.length : 0;
      return { v: String(n), n: n === 1 ? "stored" : "stored", tone: n ? "warn" : "" };
    },
  },
  service: {
    label: "Next service",
    get: (v, s, car) => {
      const nx = car && car.service && car.service.next;
      if (!nx) return { v: "—", n: "" };
      return { v: Math.max(0, nx.life) + "%", n: nx.short || nx.item,
               tone: nx.life <= 0 ? "bad" : nx.life <= 15 ? "warn" : "" };
    },
    read: (v, s, car) => {
      const nx = car && car.service && car.service.next;
      return nx ? Math.max(0, nx.life) : null;
    },
    scale: () => ({ ...pct(), bands: [{ to: 15, tone: "warn" }] }),
  },
};

const FOOTERS = {
  trip: (car) => car && car.perf && car.perf.day
    ? `${dist(car.perf.day.km)} today${car.odometer ? "   ·   " + dist(car.odometer) : ""}` : "",
  odometer: (car) => car && car.odometer ? dist(car.odometer) : "",
  none: () => "",
};

function num(x, fmt) {
  return x === null || x === undefined || Number.isNaN(x) ? "—" : String(fmt(x));
}

// ---------------------------------------------------------------- the scales
//
// `get` formats a reading for reading; `read` returns the same reading as a
// NUMBER, and `scale` says what face to draw it on. A dial whose ticks say °F
// has to be handed Fahrenheit, so read() converts exactly as get() does.
//
// scale is a function rather than an object because the units toggle at
// runtime: a face built once at import would keep its mph ticks after you
// switched to km/h. Readouts with no scale — the odometer, a fault count —
// simply have none, and the editor then offers them only as numbers.
const raw = (x) => (x === null || x === undefined || Number.isNaN(x) ? null : Number(x));
const asTemp = (c) => (raw(c) === null ? null : U.imperial ? raw(c) * 9 / 5 + 32 : raw(c));
const pct = () => ({ min: 0, max: 100, step: 25 });

// ---------------------------------------------------------------- the view
export default function drive(root, { arg } = {}) {
  let alive = true;
  let layout = { hero: "speed", tiles: ["econ_now", "coolant", "volts"], columns: 3,
                 footer: "trip", kinds: {}, heroKind: "digital" };
  let editing = false;

  root.parentElement.classList.add("drive-stage");
  // Drive mode has exactly one way out and it is the width of the screen. The
  // rail this replaced was eleven small targets beside a driver's hand; the tab
  // bar that replaced the rail is five big ones, which is better but is still
  // five decisions offered to somebody who has asked for one.
  document.getElementById("app").dataset.drive = "1";

  const wrap = h("div.drive");
  root.appendChild(wrap);

  const takeover = h("div.drive-alert", { hidden: true });

  // The alert is fixed above the navigation, but painting over the tab bar is
  // not the same as taking it out of reach: a thumb landing where a tab used
  // to be would still press it, and during a critical alert the one thing on
  // screen that should be pressable is "Got it". inert makes the bar
  // unclickable and unfocusable for as long as the alert is up.
  function showAlert(on) {
    if (takeover.hidden !== !on) takeover.hidden = !on;
    const bar = document.getElementById("navbar");
    if (bar) bar.inert = !!on;
  }
  wrap.appendChild(takeover);

  const heroV = h("div.drive-speed", "—");
  const heroU = h("div.drive-unit", "");
  const stateEl = h("div.drive-state", "");
  // A slot rather than two fixed children: the big number is one rendering of
  // the hero readout, and a dial is another.
  const heroSlot = h("div.drive-hero-slot");
  wrap.appendChild(h("div.drive-hero", heroSlot, stateEl));

  const row = h("div.drive-row");
  wrap.appendChild(row);

  const tripEl = h("div.drive-trip", "");
  wrap.appendChild(tripEl);

  const controls = h("div.drive-controls");
  wrap.appendChild(controls);

  const editor = h("div.drive-editor", { hidden: true });
  wrap.appendChild(editor);

  let cells = [];
  let heroGauge = null;

  // A gauge wants a scale object; a readout carries a scale FUNCTION so it can
  // follow the units toggle. Resolve it at build time, which is also when a
  // unit change rebuilds.
  function resolved(def) {
    return def && def.scale ? { ...def, scale: def.scale() } : def;
  }

  function kindOf(id, def) {
    return normaliseKind((layout.kinds || {})[id], def);
  }

  function build() {
    clear(row);
    cells = [];
    row.style.gridTemplateColumns = `repeat(${layout.columns}, 1fr)`;
    for (const id of layout.tiles) {
      const def = TILES[id];
      if (!def) continue;
      const rdef = resolved(def);
      const kind = kindOf(id, rdef);
      const g = makeGauge(kind, rdef);
      const tile = h("div.drive-tile", { data: { kind } },
                     h("div.drive-tile-k", def.label));
      tile.appendChild(g.el);
      cells.push({ id, def, g });
      row.appendChild(tile);
    }
    buildHero();
    paint();
  }

  function buildHero() {
    clear(heroSlot);
    const def = TILES[layout.hero] || TILES.speed;
    const rdef = resolved(def);
    const kind = normaliseKind(layout.heroKind, rdef);
    heroSlot.dataset.kind = kind;
    if (kind === "digital") {
      heroGauge = null;
      heroSlot.appendChild(heroV);
      heroSlot.appendChild(heroU);
    } else {
      heroGauge = makeGauge(kind, rdef);
      heroSlot.appendChild(heroGauge.el);
    }
  }

  function paintControls() {
    clear(controls);
    const moving = (store.values.SPEED || 0) > 3;
    controls.appendChild(h("button.drive-exit", {
      onclick: () => { location.hash = "#dash"; },
    }, "Workshop"));
    // Only when stopped. This is the one rule the editor does not bend.
    if (!moving) {
      controls.appendChild(h("button.drive-exit", {
        onclick: () => { editing = !editing; paintEditor(); },
      }, editing ? "Done" : "Customise"));
    }
  }

  function paint() {
    const v = store.values, s = store.sample, car = store.car;
    const moving = (v.SPEED || 0) > 3;
    const running = (v.RPM || 0) > 200;

    const hero = TILES[layout.hero] || TILES.speed;
    const hv = hero.get(v, s, car);
    if (heroGauge) {
      heroGauge.update(hv, hero.read ? hero.read(v, s, car) : null);
    } else {
      heroV.textContent = hv.v;
      heroV.className = "drive-speed" + (hv.tone ? " " + hv.tone : "");
      heroU.textContent = hv.n;
    }
    stateEl.textContent = store.connected
      ? (moving ? "" : running ? "idling" : "parked") : "no link";
    wrap.dataset.state = store.connected ? (moving ? "driving" : "still") : "offline";

    for (const c of cells) {
      const out = c.def.get(v, s, car);
      c.g.update(out, c.def.read ? c.def.read(v, s, car) : null);
    }

    tripEl.textContent = (FOOTERS[layout.footer] || FOOTERS.trip)(car);
    paintControls();
    if (editing && moving) { editing = false; paintEditor(); }
  }

  // ---- the editor -------------------------------------------------------
  //
  // One row of gauge kinds, for the hero or for a chosen readout. Only the
  // kinds the readout can actually wear: a fault count has no scale, so it is
  // offered as a number and nothing else rather than as a needle with nowhere
  // to point.
  function kindRow(def, current, onPick) {
    const allowed = kindsFor(def);
    if (allowed.length < 2) return null;
    const rowEl = h("div.drive-kinds");
    for (const k of allowed) {
      rowEl.appendChild(h("button", {
        "aria-pressed": current === k ? "true" : "false",
        title: KINDS[k].note,
        onclick: () => onPick(k),
      }, KINDS[k].label));
    }
    return rowEl;
  }

  function paintEditor() {
    editor.hidden = !editing;
    if (!editing) return;
    clear(editor);

    editor.appendChild(h("div.drive-editor-k", "Big number"));
    const heroRow = h("div.drive-pick");
    for (const [id, def] of Object.entries(TILES)) {
      if (!def.hero) continue;
      heroRow.appendChild(h("button", {
        "aria-pressed": layout.hero === id ? "true" : "false",
        onclick: () => { layout.hero = id; save(); },
      }, def.label));
    }
    editor.appendChild(heroRow);

    const heroDef = resolved(TILES[layout.hero] || TILES.speed);
    const heroKinds = kindRow(heroDef, normaliseKind(layout.heroKind, heroDef), (k) => {
      layout.heroKind = k;
      save();
    });
    if (heroKinds) {
      editor.appendChild(h("div.drive-editor-k", "Big number style"));
      editor.appendChild(heroKinds);
    }

    editor.appendChild(h("div.drive-editor-k",
      `Readouts  ·  ${layout.tiles.length} of 8`));
    const chosen = h("div.drive-chosen");
    layout.tiles.forEach((id, i) => {
      const def = TILES[id];
      if (!def) return;
      const rdef = resolved(def);
      const chip = h("div.drive-chip",
        h("div.drive-chip-top",
          h("span", def.label),
          h("button", { title: "left", disabled: i === 0,
            onclick: () => { swap(i, i - 1); } }, "‹"),
          h("button", { title: "right", disabled: i === layout.tiles.length - 1,
            onclick: () => { swap(i, i + 1); } }, "›"),
          h("button", { title: "remove",
            onclick: () => { layout.tiles.splice(i, 1); save(); } }, "×")));
      const ks = kindRow(rdef, kindOf(id, rdef), (k) => {
        layout.kinds = { ...(layout.kinds || {}), [id]: k };
        save();
      });
      if (ks) chip.appendChild(ks);
      chosen.appendChild(chip);
    });
    editor.appendChild(chosen);

    editor.appendChild(h("div.drive-editor-k", "Add"));
    const avail = h("div.drive-pick");
    for (const [id, def] of Object.entries(TILES)) {
      if (layout.tiles.includes(id)) continue;
      avail.appendChild(h("button", {
        disabled: layout.tiles.length >= 8,
        onclick: () => { layout.tiles.push(id); save(); },
      }, "+ " + def.label));
    }
    editor.appendChild(avail);

    editor.appendChild(h("div.drive-editor-k", "Across"));
    const cols = h("div.drive-pick");
    for (const n of [1, 2, 3, 4]) {
      cols.appendChild(h("button", {
        "aria-pressed": layout.columns === n ? "true" : "false",
        onclick: () => { layout.columns = n; save(); },
      }, String(n)));
    }
    editor.appendChild(cols);

    editor.appendChild(h("div.drive-editor-k", "Bottom line"));
    const foot = h("div.drive-pick");
    for (const [id, label] of [["trip", "Today and odometer"],
                               ["odometer", "Odometer"], ["none", "Nothing"]]) {
      foot.appendChild(h("button", {
        "aria-pressed": layout.footer === id ? "true" : "false",
        onclick: () => { layout.footer = id; save(); },
      }, label));
    }
    editor.appendChild(foot);

    editor.appendChild(h("div.drive-editor-k", "Switch to this screen"));
    const autoRow = h("div.drive-pick");
    for (const [id, label] of [["connect", "When the adapter connects"],
                               ["moving", "When the car starts moving"],
                               ["off", "Never — I'll choose"]]) {
      autoRow.appendChild(h("button", {
        "aria-pressed": (layout.auto || "connect") === id ? "true" : "false",
        onclick: () => { layout.auto = id; save(); },
      }, label));
    }
    editor.appendChild(autoRow);

    const backRow = h("div.drive-pick");
    backRow.appendChild(h("button", {
      "aria-pressed": layout.auto_return !== false ? "true" : "false",
      onclick: () => { layout.auto_return = layout.auto_return === false; save(); },
    }, layout.auto_return !== false
      ? "Back to the workshop when unplugged"
      : "Stay here when unplugged"));
    editor.appendChild(backRow);

    editor.appendChild(h("div.drive-editor-n",
      "Saved on the machine running OmaCar, so a tablet showing this over the "
      + "network gets the same arrangement. Only offered while the car is "
      + "stopped. Leaving drive mode by hand keeps it away until the adapter "
      + "reconnects — it will not drag you back."));
  }

  function swap(a, b) {
    const t = layout.tiles[a];
    layout.tiles[a] = layout.tiles[b];
    layout.tiles[b] = t;
    save();
  }

  async function save() {
    build();
    paintEditor();
    try {
      layout = await api.saveDriveLayout(layout);
    } catch (e) {
      // A cockpit display is read-only by design; say so once rather than
      // failing silently on every tap.
      toast("This display cannot change the layout — set it on the machine "
            + "running OmaCar.", "bad");
      editing = false;
      paintEditor();
    }
  }

  // ---- alerts -----------------------------------------------------------
  let lastAlertAt = 0;
  try { lastAlertAt = Number(localStorage.getItem(ACK_KEY)) || 0; } catch { /* private mode */ }

  async function pollAlerts() {
    if (!alive) return;
    try {
      const { records } = await api.alerts(5);
      const top = (records || [])[0];
      if (!top) return;
      const p = top.payload || {};
      const fresh = top.at > lastAlertAt && Date.now() / 1000 - top.at < 900;
      if (!fresh || p.urgency === "low") { showAlert(false); return; }
      if (!takeover.hidden) return;
      clear(takeover);
      takeover.dataset.urgency = p.urgency || "normal";
      takeover.appendChild(h("div.drive-alert-k",
        p.urgency === "critical" ? "Stop when safe" : "Heads up"));
      takeover.appendChild(h("div.drive-alert-t", p.title || top.label));
      takeover.appendChild(h("div.drive-alert-b", p.body || ""));
      takeover.appendChild(h("button.drive-exit", { onclick: () => {
        lastAlertAt = top.at;
        try { localStorage.setItem(ACK_KEY, String(top.at)); } catch { /* fine */ }
        showAlert(false);
      } }, "Got it"));
      showAlert(true);
    } catch { /* the watchdog may not be running */ }
  }

  const off = store.on("live", paint);
  api.driveLayout().then((l) => {
    if (!alive) return;
    layout = l;
    build();
    // #drive/edit opens straight into the editor — handy from a settings link
    // and from a keyboard, and still refused the moment the car moves.
    if (arg === "edit" && (store.values.SPEED || 0) <= 3) {
      editing = true;
      paintEditor();
    }
  }).catch(() => build());
  build();
  pollAlerts();
  const t = setInterval(pollAlerts, 5000);

  // Keep the screen awake. A dashboard that blanks halfway through a drive is
  // a dashboard nobody trusts. Best-effort — not every browser grants it.
  let lock = null;
  if (navigator.wakeLock) {
    navigator.wakeLock.request("screen").then((l) => { lock = l; }).catch(() => {});
  }

  return () => {
    alive = false;
    off();
    clearInterval(t);
    if (lock) { try { lock.release(); } catch { /* already gone */ } }
    root.parentElement.classList.remove("drive-stage");
    delete document.getElementById("app").dataset.drive;
    // Leaving with an alert up must not leave the navigation inert behind us,
    // or the user is on another screen with a dead tab bar and no way to say
    // so. Unmount is the only place that is guaranteed to run.
    const bar = document.getElementById("navbar");
    if (bar) bar.inert = false;
  };
}
