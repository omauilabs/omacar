// Readiness monitors and Mode 06 — the two screens nobody else bothers with.
//
// Readiness answers the question people actually walk into a shop with: will
// it pass. Mode 06 is the ECU showing its working — the measured value of each
// self-test next to the limit it was judged against. Every OBD-II car has had
// it since 1996 and almost no consumer tool surfaces it, which is a waste,
// because a catalyst test sitting at 95% of its limit has passed and is also
// about to stop passing, and that is a thing worth knowing a year early.
//
// The screen used to render every one of those as its own card: eleven
// monitors, four of them for tests this vehicle does not support, drawn at
// opacity .45 with the value "n/a"; then eleven Mode 06 cards, most of them
// reporting a test that passed with room to spare. Twenty-four cards, of which
// six carried the answer. That is not thoroughness, it is hiding: the two
// monitors that have not run were the same size, weight and colour as the nine
// that had. So the exceptions get cards and everything else goes behind a
// disclosure — still one click away, still exportable, no longer competing
// with the answer for the first screenful.

import { h, store, pct } from "../core.js";
import { explain } from "../learn.js";

// A missing headroom is not a low one. Sorting it as -1 keeps untested rows out
// of the flagged group rather than floating them to the top of it.
const hr = (t) => (t.headroom === null || t.headroom === undefined ? -1 : t.headroom);

export default function health(root) {
  // Learn mode: renders only when the reader asked for it.
  const _ex = explain(h, "readiness");
  if (_ex) root.appendChild(_ex);

  const car = store.car;
  if (!car) { root.appendChild(h("div.card", h("div.skel"))); return; }
  const r = car.readiness || { monitors: [] };
  const m6 = car.mode06 || [];

  const supported = r.monitors.filter((m) => m.supported);
  const incomplete = supported.filter((m) => !m.complete);
  const complete = supported.filter((m) => m.complete);
  const unsupported = r.monitors.filter((m) => !m.supported);

  root.appendChild(h("section.sect",
    h("div.head", h("div", h("div.eyebrow", "Emissions"),
      h("div.title", "Readiness and on-board tests")))));

  // ---- the verdict ----
  // The count lives in the tile on the right, so the prose beside it does not
  // say it again; what the prose is for is the part a number cannot carry,
  // which is what the number means at the test station.
  root.appendChild(h("div.card.tint-" + (r.ready ? "ok" : "warn"),
    h("div.row.wrapline",
      h("div",
        h("div.eyebrow", "Emissions test"),
        h("div.title", { style: { fontSize: "1.4rem", marginTop: "2px" } },
          r.ready ? "Ready to test" : "Not ready"),
        h("p.lede", { style: { marginTop: "6px", maxWidth: "68ch" } },
          r.ready
            ? "Every supported monitor has run and completed. The car can be presented for a test."
            : "Most jurisdictions allow one incomplete non-continuous monitor on an "
              + "OBD-II car and none allow two.")),
      h("div.right",
        h("div.stat-tile", h("div.k", "Complete"),
          h("div.v" + (r.ready ? ".ok" : ".warn"),
            `${complete.length}/${supported.length}`))))));

  // ---- the monitors ----
  // Only the ones that have not run get a card. The reason a monitor has not
  // run is the useful half and it used to sit in a separate card further down
  // the page, which meant reading the name in one place and the explanation in
  // another; it belongs on the same card as the monitor it is about.
  const mons = h("section.sect", h("div.eyebrow", "Monitors"));
  if (incomplete.length) {
    const grid = h("div.grid.g-fit");
    for (const m of incomplete) {
      grid.appendChild(h("div.card",
        h("div.row",
          h("span.dot.warn"),
          h("span", { style: { fontWeight: "600", fontSize: ".82rem" } }, m.name)),
        h("div.row", { style: { marginTop: "6px" } },
          h("span.muted", m.kind === "continuous" ? "continuous" : "trip-based"),
          h("span.pill.warn.right", "incomplete")),
        m.why ? h("p.lede", { style: { marginTop: "8px" } }, m.why) : null));
    }
    mons.appendChild(grid);
  }

  // Everything that is working, in one line you can open. The unsupported
  // monitors are named here rather than dropped: "n/a" on a card is not
  // information, but "this vehicle does not have that test" is, once.
  if (complete.length || unsupported.length) {
    mons.appendChild(h("details.gloss",
      h("summary", `${complete.length} of ${supported.length} monitors complete`),
      h("div.learn-body",
        complete.length
          ? h("div.row.wrapline", { style: { gap: "6px" } },
              ...complete.map((m) => h("span.pill.ok", m.name)))
          : null,
        unsupported.length
          ? h("p.muted", { style: { marginTop: complete.length ? "10px" : "0" } },
              `Not supported by this vehicle: ${unsupported.map((m) => m.name).join(", ")}.`)
          : null)));
  }
  root.appendChild(mons);

  // ---- Mode 06 ----
  if (m6.length) {
    // Descending headroom, so the test closest to its limit is the first thing
    // read, and the split falls where the meaning changes: failed, or inside
    // the last 15% of its allowance. Everything below that line has passed with
    // room and is a table row, not a card.
    const ordered = [...m6].sort((a, b) => hr(b) - hr(a));
    const flagged = ordered.filter((t) => t.pass === false || hr(t) > 0.85);
    const quiet = ordered.filter((t) => !(t.pass === false || hr(t) > 0.85));
    const failed = flagged.filter((t) => t.pass === false).length;
    const marginal = flagged.length - failed;
    const tally = [failed ? `${failed} failed` : null,
                   marginal ? `${marginal} marginal` : null,
                   quiet.length ? `${quiet.length} passing` : null]
      .filter(Boolean).join(" · ");

    root.appendChild(h("section.sect",
      h("div.head",
        h("div", h("div.eyebrow", "Mode 06"),
          h("div.title", { style: { fontSize: "1.05rem" } }, "On-board monitoring test results")),
        h("span.muted.right", tally)),
      h("p.lede", "Each self-test's measured value against its limit.")));

    if (flagged.length) {
      const box = h("div.grid.g2");
      for (const t of flagged) box.appendChild(m6card(t));
      root.appendChild(box);
    }

    if (quiet.length) {
      root.appendChild(h("details.gloss",
        h("summary", `${quiet.length} tests passing with room to spare`),
        h("div.learn-body",
          h("table.tbl",
            h("thead", h("tr",
              h("th", "Test"), h("th", "Component"),
              h("th.num", "Value"), h("th.num", "Limit"), h("th.num", "Of limit"))),
            h("tbody", ...quiet.map((t) => h("tr",
              h("td", t.name),
              h("td.muted", t.component || ""),
              h("td.num", `${t.value} ${t.unit || ""}`.trim()),
              h("td.num", t.hi !== null && t.hi !== undefined ? String(t.hi)
                : t.lo !== null && t.lo !== undefined ? `min ${t.lo}` : "—"),
              h("td.num", hr(t) >= 0 ? pct(hr(t) * 100) : "—"))))))));
    }
  }
}

// One test, drawn as a measurement against the window it was judged in. Kept
// as a function because only the tests that need looking at are drawn this
// way now, and the rest are a table.
function m6card(t) {
  const failed = t.pass === false;
  const marginal = !failed && hr(t) > 0.85;
  const tint = failed ? "var(--bad)" : marginal ? "var(--warn)" : "var(--ok)";
  return h("div.card" + (failed ? ".tint-bad" : marginal ? ".tint-warn" : ""),
    h("div.row.wrapline",
      h("span", { style: { fontWeight: "600", fontSize: ".84rem" } }, t.name),
      h("span.pill" + (failed ? ".bad" : marginal ? ".warn" : ".ok") + ".right",
        failed ? "failed" : marginal ? "marginal" : "pass")),
    h("div.row", { style: { marginTop: "2px" } },
      h("span.muted", t.component), h("span.muted.right", "TID " + t.mid)),
    h("div.range", { style: { marginTop: "12px" } },
      h("i", { style: { width: Math.max(2, Math.min(100, (t.headroom || 0) * 100)) + "%",
                        background: tint } }),
      // Where the limit sits on this track. Drawn even when the value is
      // past it, so an over-limit reading reads as "over" rather than as
      // a full bar that could just be a maximum.
      t.hi !== null && t.hi !== undefined ? h("b", { style: { left: "100%" } }) : null),
    h("div.row", { style: { marginTop: "8px" } },
      h("span.muted", t.lo !== null && t.lo !== undefined ? `min ${t.lo}` : ""),
      h("span", { style: { fontWeight: "700", fontSize: "1.05rem", color: tint,
                           margin: "0 auto" } }, `${t.value} ${t.unit}`),
      h("span.muted", t.hi !== null && t.hi !== undefined ? `max ${t.hi}` : "")),
    t.headroom !== null && t.headroom !== undefined
      ? h("p.muted", { style: { marginTop: "6px" } },
          `${Math.round(t.headroom * 100)}% of the limit`)
      : null,
    t.note ? h("p.muted", { style: { marginTop: "6px" } }, t.note) : null);
}
