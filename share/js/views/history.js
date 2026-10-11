// The log: how the car has been driven, and everything the tool has filed.
//
// Performance windows are always shown next to the window before them, because
// a figure on its own is trivia and a pair is a trend. The record book below is
// the workshop's memory — every scan, every recording, every advisor answer,
// with the odometer reading it was taken at.

import { h, store, api, dist, econ, econVal, econDelta, vol, money, mins,
         grouped, shortDate, clockOf, fullDate, isoDate, since, U, MONTH_NAMES }
  from "../core.js";
import { barsAndLine, sparkline } from "../charts.js";

export default function history(root) {
  const car = store.car;
  const perf = car && car.perf;
  if (!perf) { root.appendChild(h("div.empty", "No driving history yet.")); return; }

  root.appendChild(h("section.sect",
    h("div.head", h("div", h("div.eyebrow", "Records"), h("div.title", "Drive log")),
      h("span.muted.right", `since ${isoDate(perf.since)}`))));

  // ---- the four windows ----
  const tiles = h("div.grid.g4");
  for (const [label, key] of [["Today", "day"], ["Last 7 days", "week"],
                              ["This month", "month"], ["This year", "year"]]) {
    const w = perf[key];
    if (!w) continue;
    const d = w.prev ? econDelta(w.lphk, w.prev.lphk) : null;
    tiles.appendChild(h("div.card", h("div.stat-tile",
      h("div.k", label),
      h("div.v", dist(w.km, false), h("small", U.units.dist)),
      h("div.n" + (d && d.tone ? "." + d.tone : ""),
        econ(w.lphk) + (d && d.text ? `   ${d.arrow}${d.text} vs previous` : "")))));
  }
  root.appendChild(tiles);

  // ---- twelve months ----
  const months = perf.months || [];
  if (months.length) {
    const cv = h("canvas");
    root.appendChild(h("div.card",
      h("div.row.wrapline",
        h("div.eyebrow", "Twelve months"),
        h("div.right.row", { style: { gap: "14px" } },
          h("span.chan", h("span.swatch", { style: { background: "#4FA8E8", height: "8px", width: "8px", borderRadius: "2px" } }), U.units.dist),
          h("span.chan", h("span.swatch", { style: { background: "#E5B457" } }), U.units.econ))),
      h("div", { style: { marginTop: "12px" } }, cv)));
    // TWELVE SLOTS, ending this month. Drawn from the months that had drives
    // alone, two months of records filled the card as two bars, each half its
    // width, under the title "Twelve months", labelled "S" and "O".
    const byMonth = new Map(months.map((m) => [m.month, m]));
    const now = new Date();
    const slots = [];
    for (let i = 11; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
      const m = byMonth.get(key);
      slots.push({ label: MONTH_NAMES[d.getMonth()],
                   bar: m ? (m.km || 0) * U.units.km : 0,
                   line: m ? econVal(m.lphk) : null });
    }
    requestAnimationFrame(() => barsAndLine(cv, slots, { height: 170 }));
  }

  // ---- the year in numbers ----
  const y = perf.year;
  if (y) {
    root.appendChild(h("div.card",
      h("div.eyebrow", "This year"),
      h("div.grid.g4", { style: { marginTop: "12px" } },
        stat("Distance", dist(y.km)),
        stat("Fuel", vol(y.litres) + (y.cost ? `  ·  ${money(y.cost)}` : "")),
        stat("Trips", `${y.trips} over ${y.days} days`),
        stat("Engine running", mins(y.engine_s)),
        stat("Fastest", y.top_kph ? `${Math.round(y.top_kph * U.units.km)} ${U.units.speed}` : "—"),
        stat("Average economy", econ(y.lphk)),
        stat("Odometer", perf.odometer ? dist(perf.odometer) : "—"),
        stat("Records from", isoDate(perf.since)))));
  }

  // ---- trips ----
  const trips = car.trips || [];
  if (trips.length) {
    const tbl = h("table.tbl", h("thead", h("tr",
      h("th", "Trip"), h("th", "When"), h("th.num", "Distance"),
      h("th.num", "Economy"), h("th.num", "Duration"), h("th.num", "Top"))));
    const tb = h("tbody");
    for (const t of trips) {
      tb.appendChild(h("tr",
        h("td", h("div", { style: { fontWeight: "600" } }, t.label || t.kind),
          h("div.muted", t.kind)),
        h("td.muted", `${shortDate(t.t0)}  ${clockOf(t.t0)}`),
        h("td.num", dist(t.km)),
        h("td.num", econ(t.lphk)),
        h("td.num.muted", mins((t.moving_s || 0) + (t.idle_s || 0))),
        h("td.num.muted", `${Math.round((t.top_kph || 0) * U.units.km)} ${U.units.speed}`)));
    }
    tbl.appendChild(tb);
    root.appendChild(h("div.card", h("div.eyebrow", "Recent trips"),
      h("div", { style: { marginTop: "10px", overflowX: "auto" } }, tbl)));
  }

  // ---- what the watchdog saw ----
  const alertBox = h("div.card");
  alertBox.appendChild(h("div.row", h("div.eyebrow", "Alert timeline"),
    h("span.muted.right", "raised by the watchdog, whether or not you were looking")));
  const alertList = h("div", { style: { marginTop: "10px", display: "grid", gap: "6px" } });
  alertList.appendChild(h("div.skel"));
  alertBox.appendChild(alertList);
  root.appendChild(alertBox);

  api.alerts(40).then(({ records }) => {
    alertList.replaceChildren();
    if (!records || !records.length) {
      alertList.appendChild(h("div.empty",
        "Nothing raised. Start the watchdog with: omacar watch start"));
      return;
    }
    for (const a of records) {
      const p = a.payload || {};
      alertList.appendChild(h("div.rowitem", { style: { cursor: p.code ? "pointer" : "default" },
        onclick: p.code ? () => { location.hash = "#codes/" + p.code; } : null },
        h("span.dot" + (p.urgency === "critical" ? ".bad"
          : p.urgency === "normal" ? ".warn" : ".info")),
        h("span", { style: { fontWeight: "600", minWidth: "13em" } }, p.title || a.label),
        h("span.desc", p.body || ""),
        h("span.muted", `${shortDate(a.at)}  ${clockOf(a.at)}`)));
    }
  }).catch(() => {
    alertList.replaceChildren(h("div.empty", "Alert timeline unavailable."));
  });

  // ---- the record book ----
  const book = h("div.card");
  book.appendChild(h("div.row", h("div.eyebrow", "Record book"),
    h("span.muted.right", "scans, recordings and advisor answers")));
  const list = h("div", { style: { marginTop: "10px", display: "grid", gap: "6px" } });
  list.appendChild(h("div.skel"));
  book.appendChild(list);
  root.appendChild(book);

  api.records({ n: 40 }).then(({ records }) => {
    list.replaceChildren();
    if (!records || !records.length) {
      list.appendChild(h("div.empty", "Nothing filed yet. Run a scan."));
      return;
    }
    for (const r of records) {
      const kindPill = { scan: "info", movie: "", clear: "warn", ai: "ai", test: "warn" }[r.kind] || "";
      const openable = r.kind === "movie" && r.t0;
      list.appendChild(h(openable ? "button.rowitem" : "div.rowitem",
        openable
          ? { onclick: () => { location.hash = "#data/rec:" + r.id; } }
          : { style: { cursor: "default" } },
        h("span.pill" + (kindPill ? "." + kindPill : ""), r.kind),
        h("span.desc", r.label || ""),
        r.odo ? h("span.muted", dist(r.odo)) : null,
        h("span.muted", since(Date.now() / 1000 - r.at)),
        openable ? h("span.muted", "open →") : null));
    }
  }).catch(() => { list.replaceChildren(h("div.empty", "Record book unavailable.")); });
}

function stat(k, v) {
  return h("div.stat-tile", h("div.k", k),
    h("div.v", { style: { fontSize: "1.2rem" } }, v));
}
