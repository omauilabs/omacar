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

import { h, clear, store, api, U, dist, mins,
         since, toast } from "../core.js";
import { KINDS, makeGauge, kindsFor, normaliseKind } from "../gauges.js";
import { READINGS as TILES, learnedFor, learnedKey,
         readingState, pausedNote, drawnAs } from "../readings.js";

const ACK_KEY = "omacar.ackAlert";

const FOOTERS = {
  trip: (car) => car && car.perf && car.perf.day
    ? `${dist(car.perf.day.km)} today${car.odometer ? "   ·   " + dist(car.odometer) : ""}` : "",
  odometer: (car) => car && car.odometer ? dist(car.odometer) : "",
  none: () => "",
};

// ---------------------------------------------------------------- the view
export default function drive(root, { arg } = {}) {
  let alive = true;
  let layout = { hero: "speed", tiles: ["econ_now", "coolant", "volts"], columns: 3,
                 footer: "trip", kinds: {}, heroKind: "digital" };
  let editing = false;

  root.parentElement.classList.add("drive-stage");

  // The catalogue this screen draws from: the built-ins above, plus whatever
  // the CURRENT car has been taught. Built here and rebuilt below when the car
  // changes, never accumulated — see learnedFor().
  //
  // One object, read by both the renderer and the editor, so the picker cannot
  // offer a tile the screen would refuse to draw and the screen cannot draw
  // one the picker never offered. An id in the saved layout that this car has
  // no tile for is SKIPPED rather than deleted from the layout: the layout is
  // one arrangement shared by the whole install, and the other car's readout
  // should come back when that car does, not be quietly thrown away because a
  // different vehicle was plugged in for an afternoon.
  let catalogue = { ...TILES, ...learnedFor(store.car) };
  let taught = learnedKey(store.car);

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
    // A PROPERTY, NOT THE COMPUTED VALUE. Writing grid-template-columns here
    // put the column count in an inline style, which no stylesheet can win
    // against -- so the layout could not be reinterpreted when the tablet is
    // turned on its side. The rule in app.css derives the effective count from
    // this, and the portrait block overrides that derivation.
    row.style.setProperty("--cols", String(layout.columns));
    for (const id of layout.tiles) {
      const def = catalogue[id];
      if (!def) continue;
      const rdef = resolved(def);
      const kind = kindOf(id, rdef);
      const g = makeGauge(kind, rdef);
      const tile = h("div.drive-tile", { data: { kind } },
                     h("div.drive-tile-k", def.label));
      tile.appendChild(g.el);
      cells.push({ id, def, g, tile });
      row.appendChild(tile);
    }
    buildHero();
    paint();
  }

  function buildHero() {
    clear(heroSlot);
    const def = catalogue[layout.hero] || catalogue.speed;
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

  // BUILT ONCE. THIS RUNS FOUR TIMES A SECOND ON THE SCREEN USED AT SPEED.
  //
  // It cleared the row and made both buttons again on every live sample. A tap
  // whose pointerdown and pointerup straddle a rebuild fires its click on the
  // nearest surviving ancestor rather than on the button, so the press simply
  // does not happen -- about a quarter of the time, at random, on the one
  // screen somebody is using while driving. The same defect was found and
  // fixed in the hub and in the vehicle bar; this was the third instance and
  // the worst-placed.
  //
  // The Customise button is HIDDEN rather than removed while moving, for the
  // same reason the write chips grey instead of vanishing: a control that
  // disappears is a control somebody hunts for at sixty.
  let exitBtn = null;
  let editBtn = null;
  let modeRow = null;
  let markedMode = null;
  const modeBtns = new Map();

  function buildControls() {
    if (exitBtn) return;
    // Still #dash, and deliberately: the button says Workshop and #dash is the
    // workshop overview. The app's two-homes problem was a router that moved
    // the user on its own; a labelled button going where its label says is not
    // that, and repointing it at Home would have made the word a lie.
    exitBtn = h("button.drive-exit", {
      onclick: () => { location.hash = "#dash"; },
    }, "Workshop");
    editBtn = h("button.drive-exit", {
      onclick: () => { editing = !editing; paintEditor(); },
    }, "Customise");
    controls.appendChild(exitBtn);
    controls.appendChild(editBtn);

    // WHICH MODE THE SWITCH IS IN, WRITTEN DOWN.
    //
    // ECON, NORMAL and SPORT change the pedal map and how hard IMA assists, and
    // no identifier anybody has found reports which is selected — so the only
    // way to label a drive is for the person who moved the switch to say so.
    // Four labelled windows exist on this car and every one was recorded
    // parked, where the three modes are identical by construction. The reason
    // there is no driving data is that collecting it meant typing in a moving
    // car.
    //
    // THESE STAY WHEN THE CAR IS MOVING, and `Customise` above does not. That
    // is deliberate and it is not an oversight of the same rule: rearranging
    // gauges at 70 mph is a mis-tap that ruins the screen you are reading,
    // while this one writes a note. Nothing here reaches the vehicle — it
    // cannot select a mode, only record the one you selected — and a wrong tap
    // is undone by tapping the right one.
    modeRow = h("div.drive-modes");
    for (const m of ["econ", "normal", "sport"]) {
      const b = h("button.drive-mode", {
        type: "button",
        onclick: () => {
          api.markDriveMode(m).then(() => {
            markedMode = m; paintModes();
            // Tell the vehicle bar, which keeps the mode in frame on every
            // screen. An event rather than an import: neither file should have
            // to know the other exists to agree about this.
            document.dispatchEvent(new CustomEvent("omacar:drivemode", { detail: m }));
          })
            .catch(() => toast("could not write that down"));
        },
      }, m.toUpperCase());
      modeRow.appendChild(b);
      modeBtns.set(m, b);
    }
    controls.appendChild(modeRow);
    api.driveMode().then((d) => { markedMode = d && d.mode; paintModes(); })
      .catch(() => {});
  }

  function paintModes() {
    for (const [m, b] of modeBtns) {
      if (m === markedMode) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    }
  }

  function paintControls() {
    buildControls();
    const moving = (store.values.SPEED || 0) > 3;
    // Only when stopped. This is the one rule the editor does not bend.
    //
    // A RESERVED SLOT, not a slot that appears -- the same trick, and for the
    // same reason, as .subbar-lead in app.css. `hidden` collapses the button
    // and everything below it jumps; with the mode markers now sharing this
    // block, the Exit target moved 64px down the screen the moment the car
    // started rolling. A control that moves out from under a thumb at the
    // instant somebody is reaching for it is the failure that check exists to
    // catch, and it caught this.
    //
    // visibility keeps the space, and still takes the button out of reach and
    // out of the tab order, so the rule above is unchanged.
    editBtn.style.visibility = moving ? "hidden" : "";
    const want = editing ? "Done" : "Customise";
    if (editBtn.textContent !== want) editBtn.textContent = want;
  }

  function paint() {
    // What decides (moving, running, the Customise lock) reads the sample, as
    // it always has. What is DRAWN comes from store.shown, and during a
    // hand-off every live readout is marked paused -- dimmed by app.css, its
    // tone dropped, and the line under the hero says why. This screen used to
    // go on drawing the hand-off's last values as current for the whole of it.
    const v = store.values, car = store.car;
    const s = store.shown, sv = s.values || {};
    const moving = (v.SPEED || 0) > 3;
    const running = (v.RPM || 0) > 200;

    const hero = catalogue[layout.hero] || catalogue.speed;
    const heroState = readingState(hero, s);
    heroSlot.dataset.state = heroState;
    const hv = drawnAs(hero.get(sv, s, car), heroState);
    if (heroGauge) {
      heroGauge.update(hv, hero.read ? hero.read(sv, s, car) : null);
    } else {
      heroV.textContent = hv.v;
      heroV.className = "drive-speed" + (hv.tone ? " " + hv.tone : "");
      heroU.textContent = hv.n;
    }
    stateEl.textContent = store.connected
      ? (moving ? "" : running ? "idling" : "parked")
      : store.paused ? pausedNote(store.pausedSince, true) : "no link";
    wrap.dataset.state = store.connected ? (moving ? "driving" : "still") : "offline";

    for (const c of cells) {
      const st = readingState(c.def, s);
      c.tile.dataset.state = st;
      const out = drawnAs(c.def.get(sv, s, car), st);
      c.g.update(out, c.def.read ? c.def.read(sv, s, car) : null);
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
    for (const [id, def] of Object.entries(catalogue)) {
      if (!def.hero) continue;
      heroRow.appendChild(h("button", {
        "aria-pressed": layout.hero === id ? "true" : "false",
        onclick: () => { layout.hero = id; save(); },
      }, def.label));
    }
    editor.appendChild(heroRow);

    const heroDef = resolved(catalogue[layout.hero] || catalogue.speed);
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
      const def = catalogue[id];
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
    for (const [id, def] of Object.entries(catalogue)) {
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

  // THE CAR CAN CHANGE UNDER A SCREEN THAT NEVER MOVES.
  //
  // Mounting is not the only moment a different vehicle becomes the current
  // one. The snapshot is re-polled every twenty seconds, and `omacar use` on
  // the laptop moves the whole app to another car without this tablet
  // navigating anywhere at all — the tablet in the car is often the surface
  // nobody is touching. Deriving only at mount would leave it showing the
  // other car's learned readouts until somebody happened to walk over and
  // press something.
  //
  // Rebuilt only when the teaching actually changed. build() tears the row
  // down and makes it again, and this file has already been bitten once by
  // rebuilding things under a driver's thumb (see the note above
  // buildControls); doing that every twenty seconds because a snapshot arrived
  // would be reintroducing it by another route.
  const offCar = store.on("car", () => {
    const key = learnedKey(store.car);
    if (key === taught) return;
    taught = key;
    catalogue = { ...TILES, ...learnedFor(store.car) };
    build();
    paintEditor();
  });
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
    offCar();
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
