// Ready for a long drive? Vehicle → Care → Pre-trip, and one tap from the
// power-on screen.
//
// A PARKED TOOL. Stepping a distance, ticking a walk-round and dating a service
// item are all things for a stationary driver, so while the car moves (or is
// handing over) this shows one line, "Available when you stop", and nothing
// else. The judging is share/js/pretrip.js (pure, tested); what is kept between
// visits is lib/pretrip.py.
//
// EVERY LAMP SAYS WHERE ITS FACT CAME FROM. Car-derived rows carry "Simulated"
// when the record is the simulator's, and the screen's header says so once more.
// A check that could not be made is a grey lamp saying so, never a green one.

import { h, clear, store, api, toast, confirmDialog, dist, U } from "../core.js";
import { buildChecks, verdict, stepTrip, CHECKLIST, STEP } from "../pretrip.js";

const WHY_PARKED = "Available when you stop";

// "Never recorded: tap to set" offers these. The odometer at such a date is not
// known, so only "Today" counts distance as well as time.
const WHEN = [
  { k: "Today", days: 0 }, { k: "1 month", days: 30 }, { k: "3 months", days: 91 },
  { k: "6 months", days: 182 }, { k: "1 year", days: 365 }, { k: "2 years", days: 730 },
];

export default function pretripView(root) {
  let doc = null, battery = null, locked = null, open = null, dead = false;
  const body = h("div.pt");
  root.appendChild(body);

  const kmPerUnit = () => 1 / U.units.km;
  const fmtDist = (km) => dist(km);

  function inputs() {
    const car = store.car || {};
    const v = store.values;
    return {
      faults: car.faults, readiness: car.readiness, service: car.service,
      gaps: doc ? doc.gaps : null,
      battery12: { volts: v.CONTROL_MODULE_VOLTAGE, rpm: v.RPM, connected: store.connected },
      ima: battery,
      tpms: doc ? doc.tpms : null,
      fuel: { tripKm: doc ? doc.trip_km : null, fuelPct: store.connected ? v.FUEL_LEVEL : null,
              tankL: car.vehicle && car.vehicle.tank_l,
              lphk: car.perf && car.perf.year && car.perf.year.lphk, fmtDist },
      ticks: doc ? doc.ticks : {},
      checklist: doc && doc.checklist && doc.checklist.length ? doc.checklist : CHECKLIST,
    };
  }

  const sim = () => !!((store.car && store.car.simulated) || store.sample.simulated);

  function paint() {
    if (dead) return;
    clear(body);
    locked = !!store.lockedAsMoving;
    if (locked) {
      body.appendChild(h("div.pt-away", { role: "status" },
        h("div.pt-away-k", "Ready for a long drive?"),
        h("div.pt-away-v", WHY_PARKED),
        h("div.pt-away-n", "The check, the trip distance and the walk-round are for a parked car.")));
      return;
    }
    if (!store.car || !doc) {
      body.appendChild(h("div.pt-away", { role: "status" }, h("div.pt-away-v", "Reading the car…")));
      return;
    }
    const inp = inputs();
    const checks = buildChecks(inp);
    const v = verdict(checks);
    const simulated = sim();
    const list = inp.checklist;

    // ---- left: the verdict, the trip, the walk-round
    const hero = h("section.pt-hero", { "data-tone": v.tone },
      h("div.pt-hero-k", h("span", "Ready for a long drive?"),
        simulated ? h("span.pt-sim", "Simulated") : null,
        h("button.pt-new", { type: "button", onclick: newTrip }, "New trip")),
      h("div.pt-verdict", h("span.pt-halo", { "aria-hidden": "true" }), h("span", v.label)),
      h("div.pt-tally",
        tally("ok", v.ok, "fine"), tally("warn", v.warn, "to note"),
        tally("bad", v.bad, "to fix"), tally("unknown", v.unknown, "unknown")));

    const km = doc.trip_km;
    const trip = h("section.pt-card.pt-trip",
      h("div.pt-card-k", "Trip distance", h("span.pt-card-s", `  ·  steps of ${STEP} ${U.units.dist}, compared with the fuel in the tank`)),
      h("div.pt-stepper",
        h("button.pt-step", { type: "button", "aria-label": `Shorter by ${STEP}`,
          disabled: !km, onclick: () => setTrip(stepTrip(km, -1, kmPerUnit())) }, "−"),
        h("div.pt-trip-v", km ? [h("span.display-num", String(Math.round(km * U.units.km))),
          h("span.pt-unit", U.units.dist)] : h("span.pt-none", "Not set")),
        h("button.pt-step", { type: "button", "aria-label": `Longer by ${STEP}`,
          onclick: () => setTrip(stepTrip(km, +1, kmPerUnit())) }, "+")));

    const walk = h("section.pt-card.pt-walk",
      h("div.pt-card-k", "Walk-round, ticked by you"),
      h("div.pt-ticks", ...list.map((c) => {
        const on = doc.ticks[c.id] === true;
        return h("button.pt-tick", { type: "button", role: "checkbox",
          "aria-checked": String(on), "data-on": on ? "1" : "", onclick: () => tick(c.id, !on) },
          h("span.pt-box", { "aria-hidden": "true" }, on ? "✓" : ""), h("span", c.label));
      })));

    // ---- right: every check, then the gaps
    const rows = h("section.pt-checks", ...checks.map((c) => row(c, simulated)));

    const gaps = doc.gaps || [];
    const gapCard = gaps.length ? h("section.pt-card.pt-gaps",
      h("div.pt-card-k", "Never recorded"),
      h("div.pt-card-n", "Their due dates are guesses until you say when they were done."),
      ...gaps.map((g) => gapRow(g))) : null;

    body.appendChild(h("div.pt-left", hero, trip, walk));
    body.appendChild(h("div.pt-right", rows, gapCard));
    // The dates for an opened gap are below the fold on a small stage.
    const opened = body.querySelector('.pt-gap[data-open="1"]');
    if (opened) requestAnimationFrame(() => opened.scrollIntoView({ block: "nearest" }));
  }

  const tally = (tone, n, word) => h("span.pt-t", { "data-tone": tone, "data-zero": n ? "" : "1" },
    h("span.pt-dot"), h("b", String(n)), ` ${word}`);

  function row(c, simulated) {
    // Rows whose fact is the car's own read carry the simulator's label.
    const fromCar = ["codes", "readiness", "service", "battery12", "fuel"].includes(c.id);
    const tag = c.simulated || (simulated && fromCar) ? "Simulated" : null;
    return h("div.pt-row", { "data-lamp": c.lamp, "data-id": c.id },
      h("span.pt-lamp", { role: "img", "aria-label": c.lamp }),
      h("div.pt-main", h("div.pt-title", c.title), h("div.pt-line", c.line)),
      tag ? h("span.pt-src", tag) : null);
  }

  function gapRow(g) {
    const isOpen = open === g.item;
    const el = h("div.pt-gap", { "data-open": isOpen ? "1" : "" },
      h("button.pt-gap-b", { type: "button", "aria-expanded": String(isOpen),
        onclick: () => { open = isOpen ? null : g.item; paint(); } },
        h("span.pt-gap-n", g.item), h("span.pt-gap-a", isOpen ? "Last done:" : "Tap to set")));
    if (isOpen) {
      el.appendChild(h("div.pt-when", ...WHEN.map((w) =>
        h("button.pt-when-b", { type: "button", onclick: () => record(g, w) }, w.k === "Today" ? "Today" : w.k + " ago"))));
      el.appendChild(h("div.pt-card-n", "Today also starts the distance count from the odometer. Earlier dates count by time only until it is next done."));
    }
    return el;
  }

  // ---- actions (parked only: the view is not drawn while moving, and each
  // handler re-checks, because a tap can land the moment the car moves)
  const parked = () => {
    if (store.lockedAsMoving) { toast(WHY_PARKED); paint(); return false; }
    return true;
  };

  async function save(body_) {
    try { doc = await api.setPretrip(body_); } catch (e) { toast(String(e.message || e), "bad"); }
    paint();
  }
  const setTrip = (km) => parked() && save({ trip_km: km });
  const tick = (id, value) => parked() && save({ tick: id, value });

  async function newTrip() {
    if (!parked()) return;
    const ok = await confirmDialog({ title: "Start a new trip?",
      body: "Clears the trip distance and the walk-round ticks. Service dates are kept.",
      confirm: "New trip", tone: "primary" });
    if (ok) save({ new_trip: true });
  }

  async function record(g, w) {
    if (!parked()) return;
    try {
      await api.service(w.days === 0 ? { action: "log", item: g.item }
        : { action: "record", item: g.item, days_ago: w.days });
      toast(`${g.item}: recorded.`);
      open = null;
      await store.refreshCar();
      doc = await api.pretrip();
    } catch (e) { toast(String(e.message || e), "bad"); }
    paint();
  }

  async function load() {
    try { doc = await api.pretrip(); } catch { doc = doc || { trip_km: null, ticks: {}, gaps: null, tpms: null, checklist: [] }; }
    try { battery = await api.battery(); } catch { battery = null; }
    paint();
  }

  // Repaint when the lock flips or the car's own data lands; never on every
  // live sample, which would throw away a half-made tap.
  const onLive = () => { if (!!store.lockedAsMoving !== locked) paint(); };
  const offLive = store.on("live", onLive);
  const offCar = store.on("car", () => { if (!locked) paint(); });
  store.refreshCar();
  load();
  paint();
  const timer = setInterval(() => { if (!store.lockedAsMoving) load(); }, 30000);

  return () => { dead = true; offLive(); offCar(); clearInterval(timer); };
}
