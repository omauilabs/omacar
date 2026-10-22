// The service book. What was done, when, and what is next.
//
// Every item is scored on Honda's own Maintenance Minder scale, which counts
// down rather than up: 15% is book it, 5% is now, nought is past due. Whichever
// of the mileage interval and the time interval is further along is the one
// that decides — that is the manufacturer's rule and it is the right one,
// because brake fluid does not care how far you have driven.

import { h, store, api, toast, confirmDialog, dist, isoDate, grouped,
         lifeTone, U } from "../core.js";

// The service history, from documents.
//
// The schedule answers "am I due". This answers "what has actually been done",
// which is the question a buyer asks and the one the schedule cannot reach:
// it only ever stored a last-done date, overwritten each time.
//
// Every entry names the document it came from and how the match was made,
// because an entry somebody disagrees with has to be traceable to the page it
// was read off.
function historyCard(root) {
  const box = h("section.card",
    h("div.eyebrow", "From your documents"),
    h("div.title", "Service history"),
    h("p.lede", "Loading…"));
  root.appendChild(box);

  api.serviceHistory().then((r) => {
    const rows = r.timeline || [];
    while (box.firstChild) box.removeChild(box.firstChild);
    box.appendChild(h("div.eyebrow", "From your documents"));
    box.appendChild(h("div.title", "Service history"));
    if (!rows.length) {
      box.appendChild(h("p.lede",
        "Nothing yet. Add a receipt under Docs, press “Read it”, and anything "
        + "it says was done can be added here — which is also what makes the "
        + "schedule above count from a real date rather than from nothing."));
      return;
    }
    box.appendChild(h("div.svc-timeline", ...rows.map((e) => h("div.svc-entry",
      h("span.svc-when", e.at ? isoDate(new Date(e.at * 1000).toISOString()) : "—"),
      h("span.svc-item", e.item),
      h("span.mono.svc-km", e.km ? dist(e.km) : ""),
      h("span.svc-how",
        e.source_doc ? `doc #${e.source_doc}` : "entered by hand",
        e.confidence ? ` · ${e.confidence}` : "")))));
  }).catch(() => {
    while (box.firstChild) box.removeChild(box.firstChild);
    box.appendChild(h("p.lede", "Could not read the service history."));
  });
}

export default function service(root) {
  const car = store.car;
  const s = car && car.service;

  root.appendChild(h("section.sect",
    h("div.head", h("div", h("div.eyebrow", "Maintenance"),
      h("div.title", "Service schedule")),
      h("button.btn.right", { onclick: () => window.print() }, "Print"))));

  // Neither the odometer nor the service history is on the car — see
  // lib/book.py — so both are edited here rather than only displayed.
  root.appendChild(odometerCard());
  historyCard(root);

  if (!s) {
    root.appendChild(h("div.card",
      h("div.title", "No service record yet"),
      h("p.lede", { style: { marginTop: "8px" } },
        "Maintenance Minder lives in the instrument cluster behind a "
        + "manufacturer protocol, so no scan tool at any price can read what "
        + "has been done to this car. Start a schedule and log the work as you "
        + "do it."),
      h("div.row", { style: { marginTop: "12px" } },
        h("button.btn.primary", { onclick: () => act(
          { action: "start" }, "Started a schedule.") }, "Start a schedule"))));
    return;
  }
  const next = s.next;

  root.appendChild(h("div.card.tint-" + (lifeTone(next.life) || "ok"),
    h("div.eyebrow", "Next due"),
    h("div.row.wrapline", { style: { marginTop: "6px", alignItems: "baseline" } },
      h("div.title", { style: { fontSize: "1.5rem" } }, next.item),
      next.code ? h("span.pill", "Minder " + next.code) : null,
      h("div.title.right", { style: { fontSize: "1.5rem",
        color: `var(--${lifeTone(next.life) === "bad" ? "bad" : lifeTone(next.life) === "warn" ? "warn" : "ok"})` } },
        Math.max(0, next.life) + "%")),
    h("div.meter", { style: { marginTop: "10px" } },
      h("i", { style: { width: Math.max(0, next.life) + "%",
        background: `var(--${lifeTone(next.life) === "bad" ? "bad" : lifeTone(next.life) === "warn" ? "warn" : "ok"})` } })),
    h("p.lede", { style: { marginTop: "10px" } },
      [next.km_left !== null && next.km_left !== undefined
        ? (next.km_left < 0 ? "overdue by " + dist(Math.abs(next.km_left)) : "in " + dist(next.km_left))
        : null,
       // A date already past is a date it went overdue on, not a deadline: the
       // owner's oil read "by 2 Sep 2026" in October, under a red 0%.
       next.due_on ? (next.due_on < new Date().toISOString().slice(0, 10)
         ? "overdue since " + isoDate(next.due_on) : "by " + isoDate(next.due_on)) : null,
       next.last_on ? `last done ${isoDate(next.last_on)}${next.last_km ? " at " + dist(next.last_km) : ""}` : null,
      ].filter(Boolean).join("   ·   ")),
    next.note ? h("p.muted", { style: { marginTop: "4px" } }, next.note) : null));

  const tbl = h("table.tbl",
    h("thead", h("tr",
      h("th", "Item"), h("th", "Minder"), h("th.num", "Life"),
      h("th.num", "Remaining"), h("th", "Due"), h("th", "Last done"),
      h("th", "Notes"), h("th.noprint", h("span.sr-only", "Mark done")))));
  const tb = h("tbody");
  for (const it of s.items) {
    const tone = lifeTone(it.life);
    tb.appendChild(h("tr",
      h("td", h("div", { style: { fontWeight: "600" } }, it.item),
        h("div.meter.thin", { style: { marginTop: "5px", maxWidth: "170px" } },
          h("i", { style: { width: Math.max(0, it.life) + "%",
            background: `var(--${tone === "bad" ? "bad" : tone === "warn" ? "warn" : "ok"})` } }))),
      h("td.muted", it.code || "—"),
      h("td.num", { style: { color: `var(--${tone === "bad" ? "bad" : tone === "warn" ? "warn" : "ok"})`,
                             fontWeight: "600" } }, Math.max(0, it.life) + "%"),
      h("td.num.muted", it.km_left === null || it.km_left === undefined ? "—"
        : it.km_left < 0 ? "over by " + dist(Math.abs(it.km_left)) : dist(it.km_left)),
      h("td.muted", it.due_on ? isoDate(it.due_on) : "—"),
      h("td.muted", it.last_on ? `${isoDate(it.last_on)}${it.last_km ? "  ·  " + dist(it.last_km) : ""}` : "—"),
      h("td.muted", { style: { maxWidth: "26ch" } }, it.note || ""),
      // The whole point of a service book is recording that something was
      // done. One button per row, at the odometer reading as it stands.
      h("td.noprint", h("button.btn.sm", { onclick: async () => {
        const ok = await confirmDialog({
          title: `Log ${it.item} as done today?`,
          body: "It is recorded at the current odometer reading and starts "
                + "counting down again from there.",
          confirm: "Log it", tone: "primary",
        });
        if (!ok) return;
        act({ action: "log", item: it.item }, `${it.item} logged.`);
      } }, "Done today"))));
  }
  tbl.appendChild(tb);
  root.appendChild(h("div.card",
    h("div.row", h("div.eyebrow", "The book"),
      // The same words the Report uses: "11 due or due soon" there was "11
      // overdue" here, for the same eleven items.
      h("span.muted.right", s.overdue
        ? `${s.overdue} overdue${s.due > s.overdue ? ` · ${s.due - s.overdue} due soon` : ""}`
        : s.due ? `${s.due} due soon` : "nothing due")),
    h("div", { style: { marginTop: "10px", overflowX: "auto" } }, tbl)));

  root.appendChild(h("p.muted",
    "Whichever interval is further along decides — mileage or time. "
    + "That is the manufacturer's rule, and it is why brake fluid comes due "
    + "on a car that has barely moved."));

  root.appendChild(addCard());
}

// Every write here reloads, because the countdown, the odometer and the next
// due item all move together and a half-updated schedule is a confusing one.
async function act(body, said) {
  try {
    await api.service(body);
    toast(said);
    await store.refreshCar();
    location.reload();
  } catch (e) { toast(String(e.message || e), "bad"); }
}

// The odometer is not an OBD-II PID. It is a reading you gave once plus
// distance integrated from road speed since, and it says so rather than
// presenting a derived figure as if it came off the bus.
function odometerCard() {
  const car = store.car;
  const v = car.vehicle || {};
  const input = h("input", { type: "text", inputmode: "numeric",
    placeholder: `reading in ${U.units.dist}`, style: { maxWidth: "210px" } });

  return h("div.card",
    h("div.row.wrapline",
      h("div",
        h("div.eyebrow", "Odometer"),
        h("div.title", { style: { fontSize: "1.5rem", marginTop: "2px" } },
          car.odometer ? dist(car.odometer) : "not set")),
      h("form.right.row", { style: { gap: "8px" }, onsubmit: async (e) => {
        e.preventDefault();
        const n = parseFloat((input.value || "").replace(/,/g, ""));
        if (!Number.isFinite(n) || n < 0) { toast("That is not a reading.", "bad"); return; }
        try {
          // Typed in whatever unit is on screen, which is the unit on the dash.
          await api.setOdometer(n / U.units.km);
          toast("Odometer set. It counts on from there by itself.");
          await store.refreshCar();
          location.reload();
        } catch (e2) { toast(String(e2.message || e2), "bad"); }
      } }, input, h("button.btn", { type: "submit" }, "Set"))),
    h("p.muted", { style: { marginTop: "8px" } },
      v.odometer_at
        ? "OBD-II has no odometer PID — not one scan tool at any price can read "
          + "the dashboard. This is the reading you gave plus distance "
          + "integrated from road speed since. Correct it any time."
        : "There is no odometer PID in OBD-II. Give it the reading once and it "
          + "counts on from there, which is also what makes the mileage "
          + "intervals below count down."));
}

function addCard() {
  const name = h("input", { type: "text", placeholder: "Item, e.g. Timing belt" });
  const far = h("input", { type: "text", inputmode: "numeric",
    placeholder: U.units.dist, style: { maxWidth: "130px" } });
  const days = h("input", { type: "text", inputmode: "numeric",
    placeholder: "days", style: { maxWidth: "110px" } });

  return h("form.card", { onsubmit: (e) => {
    e.preventDefault();
    if (!name.value.trim()) return;
    act({ action: "add", item: name.value.trim(),
          interval_km: (parseFloat(far.value) || 0) / U.units.km,
          interval_days: parseInt(days.value, 10) || 0 }, "Added.");
  } },
    h("div.eyebrow", "Add an item"),
    h("p.muted", { style: { marginTop: "4px" } },
      "The starter schedule is right for most cars and wrong for none in a way "
      + "that matters. Your handbook wins over it."),
    h("div.row.wrapline", { style: { marginTop: "10px" } },
      name, far, days, h("button.btn", { type: "submit" }, "Add")));
}
