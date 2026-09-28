// Vehicle: the car, system by system, from the last full scan.
//
// Built to the owner's mockup (doc/design/mockups/5.webp): a status headline,
// the X-ray render with a callout on every system the scan covers, the system
// list, live signals, and the three actions. Everything on it is a reading or
// a scan result; a system nobody has scanned says so, and is never "Normal".

import { h, clear, icon, store, api, dist, shortDate, clockOf } from "../core.js";
import { ICONS } from "../icons.js";
import { groupSystems, headline, worstOf } from "../systems.js";
import { makeSignalTile } from "../sigtile.js";
import { learnedFor } from "../readings.js";
import { asset } from "../assets.js";

const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };
const go = (id) => { location.hash = "#" + id; };   // taps only
const WORD = { ok: "Normal", warn: "Check", bad: "Fault", unknown: "Not scanned" };
const SYS_ICON = { engine: "gauge", hybrid: "leaf", brakes: "health", electrical: "battery", other: "dash" };
const LIVE = [["rpm", "Engine speed"], ["coolant", "Coolant"], ["volts", "12V system"], ["charge", "Hybrid pack"]];
const CALLOUTS = [["engine", "Engine"], ["hybrid", "Hybrid system"], ["brakes", "Brakes"],
                  ["steering", "Steering"], ["tyres", "Tyres"]];

function listRows(groups) {
  const by = Object.fromEntries(groups.map((g) => [g.id, g]));
  const rest = [by.steering, by.tyres, by.other].filter(Boolean);
  const mods = rest.flatMap((g) => g.modules);
  return [by.engine, by.hybrid, by.brakes, by.electrical,
          { id: "other", label: "All other systems",
            sub: mods.length ? `${mods.length} module${mods.length === 1 ? "" : "s"}` : "None found",
            status: worstOf(rest) }];
}

function sysRow(g) {
  return h("button.vh-sys-row", { type: "button", onclick: () => go("codes") },
    icon(ICONS[SYS_ICON[g.id]] || ICONS.codes, 22),
    h("span", g.label, h("span.sub", g.sub)),
    h("span.vh-sys-st", { data: { tone: g.status } },
      icon(g.status === "ok" ? ICONS.check : ICONS.codes, 18), WORD[g.status]),
    h("span.chev", icon(ICONS.chevron, 18)));
}

export default function vehicle(root) {
  let alive = true;
  const statusIco = h("span.vh-icon", icon(ICONS.check, 44));
  const title = h("div.vh-title");
  const when = h("div.vh-when");
  const carName = h("div.vh-car"), spec = h("div.vh-spec"), odo = h("div.vh-odo.display-num");

  const img = h("img.car-img", { alt: "", hidden: true, draggable: "false" });
  const slot = h("div.car-slot", h("span", "X-ray render not installed · omacar assets sync"));
  const stage = h("div.car-stage", img, slot);
  const callouts = new Map();
  for (const [id, label] of CALLOUTS) {
    const co = h("div.callout", { hidden: true, data: { sys: id, tone: "" } },
      h("span.co-dot"), h("span.co-k", label), h("span.co-v"));
    callouts.set(id, co);
    stage.appendChild(co);
  }
  asset("crz-xray").then((a) => {
    if (!a || !a.url) return;
    img.src = a.url; img.hidden = false; slot.hidden = true;
    for (const [id, co] of callouts) {
      const at = a.anchors && a.anchors[id];
      if (!at) continue;
      co.style.left = (at[0] * 100) + "%";
      co.style.top = (at[1] * 100) + "%";
      co.hidden = false;
    }
  });

  const sysList = h("div.vh-sys-list");
  const liveRow = h("div.vh-live");
  let tiles = [];
  function useTiles(list) {
    clear(liveRow);
    tiles = list;
    for (const t of tiles) { t.node.classList.add("card"); liveRow.appendChild(t.node); }
  }
  useTiles(LIVE.map(([id, label]) => makeSignalTile(id, { label })));

  // OBD-II AND HONDA ENHANCED, ONLY WHEN THERE IS SOMETHING ENHANCED TO SHOW:
  // a signal the profile has validated. The mockup's switch is not drawn over
  // candidates nobody has checked.
  const obdChip = h("button.chip", { type: "button", "aria-pressed": "true" }, "OBD-II");
  const hondaChip = h("button.chip", { type: "button", "aria-pressed": "false" }, "Honda enhanced");
  const srcRow = h("div.vh-src", { hidden: true }, h("span.vh-src-k", "Data source"), obdChip, hondaChip);
  obdChip.onclick = () => {
    obdChip.setAttribute("aria-pressed", "true"); hondaChip.setAttribute("aria-pressed", "false");
    useTiles(LIVE.map(([id, label]) => makeSignalTile(id, { label })));
    paint();
  };
  hondaChip.onclick = () => {
    const learned = Object.entries(learnedFor(store.car)).slice(0, 4);
    if (!learned.length) return;
    obdChip.setAttribute("aria-pressed", "false"); hondaChip.setAttribute("aria-pressed", "true");
    useTiles(learned.map(([id, def]) => makeSignalTile(id, { def, label: def.label })));
    paint();
  };

  const codesCard = h("div.card.vh-codes", { role: "button", tabindex: "0", onclick: () => go("codes"),
                                             onkeydown: (e) => { if (e.key === "Enter") go("codes"); } });
  const insight = h("div.card.vh-insight", { hidden: true, role: "button", tabindex: "0",
                                             onclick: () => go("advisor"),
                                             onkeydown: (e) => { if (e.key === "Enter") go("advisor"); } });
  const actions = h("div.vh-actions",
    srcRow,
    h("span.vh-gap"),
    h("button.btn.btn-primary", { type: "button", onclick: () => go("scan") }, icon(ICONS.scan, 18), " Scan vehicle"),
    h("button.btn", { type: "button", onclick: () => go("replay") }, icon(ICONS.rec, 18), " Record session"),
    h("button.btn", { type: "button", onclick: () => go("report") }, icon(ICONS.report, 18), " Export"));

  root.appendChild(h("div.veh",
    h("div.vh-head",
      h("div.vh-status", statusIco, h("div", title, when)),
      h("div.vh-id", carName, spec, odo)),
    h("div.card.vh-hero", stage),
    h("div.card.vh-systems", h("div.hc-title", "Vehicle systems"), sysList),
    h("div.card.vh-livecard", h("div.hc-title", icon(ICONS.agent, 18), "Live signals"), liveRow),
    actions,
    codesCard,
    insight));

  let sysKey = null, codesKey = null;

  function paintCodes(car) {
    const act = (car && car.active_faults) || [];
    const key = act.map((f) => f.code).join();
    if (key === codesKey) return;
    codesKey = key;
    clear(codesCard);
    codesCard.appendChild(h("div.hc-title", icon(ICONS.codes, 18), "Diagnostic codes",
      h("span.chev", icon(ICONS.chevron, 18))));
    if (!act.length) {
      codesCard.appendChild(h("div.vh-none", icon(ICONS.check, 28), h("span", "No active codes")));
      return;
    }
    for (const f of act.slice(0, 3)) {
      codesCard.appendChild(h("div.vh-code", h("span.vh-code-c.display-num", f.code), h("span", f.descr || "")));
    }
    if (act.length > 3) codesCard.appendChild(h("div.vh-more", `and ${act.length - 3} more`));
  }

  function paint() {
    if (!alive) return;
    const car = store.car;
    const groups = groupSystems(car && car.modules, car && car.active_faults);
    const hl = headline(groups);
    text(title, hl.text);
    statusIco.dataset.tone = hl.tone;
    const v = (car && car.vehicle) || {};
    text(when, v.surveyed_at ? `Last scan: ${shortDate(v.surveyed_at)}, ${clockOf(v.surveyed_at)}`
                             : "No full scan has been run on this car yet");
    text(carName, [v.year, v.make, v.model].filter(Boolean).join(" ") || (car && car.title) || "");
    text(spec, [v.engine, v.drivetrain].filter(Boolean).join("  ·  "));
    text(odo, car && car.odometer ? dist(car.odometer) : "");

    const rows = listRows(groups);
    const key = rows.map((g) => g.id + ":" + g.status).join("|");
    if (key !== sysKey) {
      sysKey = key;
      clear(sysList);
      for (const g of rows) sysList.appendChild(sysRow(g));
    }
    for (const [id, co] of callouts) {
      const g = groups.find((x) => x.id === id);
      const st = g ? g.status : "unknown";
      co.dataset.tone = st === "ok" ? "ok" : st;
      text(co.querySelector(".co-v"), WORD[st]);
    }
    srcRow.hidden = !((car && car.signals) || []).length;
    for (const t of tiles) t.paint(car, store.sample);
    paintCodes(car);
  }

  api.aiHistory().then((d) => {
    const r = ((d && d.records) || []).find((x) => x.payload && x.payload.data && x.payload.data.headline);
    if (!r || !alive) return;
    clear(insight);
    insight.append(h("div.hc-title", icon(ICONS.agent, 18), "Oma Agent insight",
                     h("span.chev", icon(ICONS.chevron, 18))),
                   h("div.vh-insight-t", r.payload.data.headline));
    insight.hidden = false;
  }).catch(() => {});

  const offLive = store.on("live", paint);
  const offCar = store.on("car", paint);
  paint();
  return () => { alive = false; offLive(); offCar(); };
}
