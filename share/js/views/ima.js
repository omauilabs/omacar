// IMA — the hybrid half of the car, and an honest map of what is missing.
//
// WHY THIS SCREEN IS MOSTLY A GAP REGISTER.
//
// The obvious version of this page is a wall of hybrid gauges: state of
// charge, pack current, assist and regen, cell temperature. Every one of them
// would be pointing at nothing. Not one live IMA quantity has ever been
// captured from this car — service 0x22 was swept across 0x0000-0x0FFF on both
// hybrid controllers and held nothing but part numbers, and service 0x21 has
// never been asked with a header of the right width. A dial reading zero next
// to the words "state of charge" is not a placeholder on a 190,000-mile
// hybrid. It is a number somebody will act on.
//
// So the register below shows a STATE per quantity instead of a value per
// quantity, and the undiscovered ones carry the exact command that would find
// them. "SOC has never answered on this car — ask service 0x21, here is the
// line" is a genuinely more useful screen than a gauge that has never had a
// reading behind it, and it is the only version that is true.
//
// WHAT IS REAL HERE, AND IT IS THE GOOD PART.
//
// Both hybrid controllers answer service 0x19 subfunction 0x0A with a
// catalogue of every fault code they can set: 49 from the battery module, 26
// from the motor module, identical across three separate drive logs. That is a
// map of what Honda built those ECUs to measure, and no generic scan tool
// shows it. It is the centre of this page.
//
// THE ONE THING THIS SCREEN MUST NEVER GET WRONG.
//
// Every DTC status byte ever captured from either module says the monitor has
// not run this cycle. The flagged list is therefore a list of monitors that
// have not completed, NOT a list of faults. Presented the other way round it
// reads as eighteen hybrid faults on a healthy car, which is the single worst
// thing this page could do to somebody. The caveat is the headline of that
// section, not a footnote under it.

import { h, clear, store, api, toast, since, fullDate, grouped } from "../core.js";
import { explain } from "../learn.js";
import { lookup, decode } from "../knowledge.js";
import { makeGauge } from "../gauges.js";
import { sparkline } from "../charts.js";

// core.js owns the `api` object and this pass does not own core.js, so the
// one-line `ima: () => req("/api/ima")` that belongs beside its siblings is in
// the hand-off note rather than in the file. Until it lands this asks for the
// endpoint directly — carrying the share token exactly the way core.js does,
// so a read-only share link behaves the same on this screen as on every other.
function shareToken() {
  try {
    return new URLSearchParams(location.search).get("k")
      || sessionStorage.getItem("omacar.k") || "";
  } catch { return ""; }
}

async function fetchIma() {
  if (typeof api.ima === "function") return api.ima();
  const k = shareToken();
  const r = await fetch("/api/ima" + (k ? "?k=" + encodeURIComponent(k) : ""),
                        { cache: "no-store" });
  if (!r.ok) {
    let msg = String(r.status);
    try { msg = (await r.json()).error || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

// core.js's since() takes an AGE in seconds, not a timestamp -- everything on
// this page is an epoch time out of a drive log, and handing one straight to it
// reports a two-minute-old sample as twenty thousand days ago.
function ago(t) {
  if (t === null || t === undefined) return "";
  return since(Date.now() / 1000 - t);
}

// The four states, and the colour each one earns. Undiscovered gets no tone on
// purpose: it is not a warning, it is an absence, and painting it amber would
// make an unasked question look like a fault.
const STATE_TONE = { measured: "ok", stale: "warn", candidate: "info",
                     undiscovered: "" };
const STATE_LABEL = { measured: "measured", stale: "stale",
                      candidate: "candidate", undiscovered: "not discovered" };

// A module reports "P0A7F-03": the five-character ISO 14229 code plus the
// failure-type byte. knowledge.js decodes the five; the suffix is the part a
// parts-shop reader throws away and is worth showing beside it.
function baseCode(code) { return String(code || "").split("-")[0].toUpperCase(); }
function failType(code) {
  const p = String(code || "").split("-");
  return p.length > 1 ? p[1] : "";
}

// WHY THIS DOES NOT SIMPLY CALL decode() AND TRUST THE ANSWER.
//
// knowledge.js's regex accepts a third character of 0-C. This car reports
// P0DA8 — the third character is D — in every single capture, so that code
// falls straight through to system "Unknown" on a screen about the module that
// reports it. Widening the pattern is the real fix and it belongs in
// knowledge.js; the note in the hand-off says so. Until then this groups what
// the decoder can read and puts what it cannot into a bucket that says exactly
// that, rather than silently filing a real hybrid code under "Unknown".
const UNPARSED = "Outside this app's code decoder";

// The SAE subsystem table reads the THIRD character, and it only means
// anything for codes in the generic range. On a manufacturer-specific code the
// third character is Honda's to define, so borrowing the SAE name for it
// invents a subsystem: it is what filed P15AA under "Vehicle speed control,
// idle control, auxiliary inputs" on a page about a hybrid battery module.
// Those codes get one honest bucket instead.
const MANUF = "Manufacturer-specific — subsystem not defined by SAE";

function groupOf(code) {
  const d = decode(baseCode(code));
  if (!d.decoded) return UNPARSED;
  return d.scope === "manufacturer-specific" ? MANUF : d.system;
}

// What the repo can honestly say about one code, and nothing beyond it.
function describe(code) {
  const b = baseCode(code);
  const known = lookup(store.knowledge, b);
  if (known.known) {
    return { title: known.title, sourced: true,
             note: known.system || "", };
  }
  const d = decode(b);
  if (d.decoded) {
    // No note for a manufacturer-specific code: d.system is the SAE reading of
    // a character Honda defines, so printing it next to "name unknown" would
    // contradict the very thing the title admits.
    const manuf = d.scope === "manufacturer-specific";
    return { title: manuf
      ? "Honda-specific, name unknown" : "Name not in this app's tables",
      sourced: false, note: manuf ? "" : d.system };
  }
  return { title: "Honda-specific, name unknown", sourced: false, note: "" };
}

export default function ima(root) {
  let doc = null;
  let err = null;
  let loading = true;

  const wrap = h("div.ima");
  root.appendChild(wrap);

  // What the last successful fetch looked like, so a quiet poll that finds
  // nothing new can leave the DOM completely alone.
  let sig = "";
  let poll = 0;

  async function load(quiet) {
    if (!quiet) { loading = true; draw(); }
    try { doc = await fetchIma(); err = null; }
    catch (e) {
      // A failed background poll is not worth replacing a working screen with
      // an error card. The car is often unplugged; that is not a fault.
      if (quiet) return;
      err = e.message || String(e); doc = null;
    }
    loading = false;
    const next = JSON.stringify(doc);
    if (quiet && next === sig) return;
    sig = next;
    draw();
  }

  // ---------------------------------------------------------------- header
  function header() {
    const v = (doc && doc.vehicle) || {};
    const q = (doc && doc.quantities) || [];
    const measured = q.filter((x) => x.state === "measured").length;
    return h("section.sect",
      h("div.head.wrapline",
        h("div",
          h("div.eyebrow", "Hybrid"),
          h("div.title", "IMA — battery, motor and what they will tell us")),
        h("div.right.row.wrapline", { style: { gap: "8px" } },
          // The rest of the app owns up to the simulator on every screen. This
          // one has to as well, and more loudly than most: invented hybrid
          // numbers are exactly what this page exists to not do.
          v.simulated ? h("span.pill.warn", "SIMULATED CAR") : null,
          // `() => load()` and not `load`: with a `quiet` parameter the click
          // Event would arrive as that argument and be truthy, so the button
          // would silently stop showing "Reading…" and skip its own redraw.
          h("button.btn.sm", { onclick: () => load(), disabled: loading },
            loading ? "Reading…" : "Refresh"))),
      h("p.lede",
        "Everything below is assembled from what this car has actually said — "
        + "its own fault catalogues, its own sweep results, its own drive logs. "
        + `${measured} of ${q.length} hybrid quantities have ever produced a `
        + "reading here. The rest say so, and say what would find them."));
  }

  // ------------------------------------------------------- the gap register
  //
  // Requirement one, and the reason the page exists: four visible states, one
  // per quantity, with the undiscovered ones carrying their own next step.
  function register() {
    const qs = (doc && doc.quantities) || [];
    const cands = (doc && doc.candidates) || [];
    if (!qs.length) return null;

    // MOST OF THESE ARE UNDISCOVERED FOR THE SAME REASON, AND SAY SO ONCE.
    //
    // The first draft printed the same paragraph and the same 110-character
    // sweep command on all seven undiscovered cards. Repetition that dense
    // stops being emphasis and starts being wallpaper -- the reader's eye
    // skips the block entirely, including the one card that says something
    // different. So a reason shared by more than one quantity is hoisted out
    // and stated once, under the names of everything it covers, and only a
    // quantity with a reason of its own keeps it on its own card.
    const shared = new Map();
    for (const q of qs) {
      if (q.state !== "undiscovered" || !q.command) continue;
      const k = q.command + "\0" + (q.note || "");
      if (!shared.has(k)) shared.set(k, []);
      shared.get(k).push(q);
    }
    const hoisted = new Set();
    const blocks = [];
    for (const [, group] of shared) {
      if (group.length < 2) continue;
      for (const q of group) hoisted.add(q.id);
      blocks.push(h("div.card.ima-gap",
        h("div.eyebrow", "Why these have not been discovered"),
        h("div.ima-gap-names",
          ...group.map((q) => h("span.pill", q.label))),
        h("p.lede", { style: { marginTop: "10px" } }, group[0].note || ""),
        group[0].next ? h("p.muted", { style: { marginTop: "6px" } },
                          group[0].next) : null,
        command(group[0].command, group[0].safety)));
    }

    const grid = h("div.grid.g3");
    for (const q of qs) grid.appendChild(quantityCard(q, hoisted.has(q.id)));

    return h("section.sect",
      h("div.head",
        h("div", h("div.eyebrow", "What we can and cannot see"),
          h("div.title", { style: { fontSize: "1.05rem" } },
            "The hybrid quantities, and the state of each")),
        h("span.muted.right", `${qs.length} tracked`)),
      h("p.lede",
        "A state, not a number. An empty gauge that has never had a reading "
        + "behind it looks like working hybrid support and is not — so nothing "
        + "here draws a needle until something has answered."),
      grid,
      ...blocks,
      legend(),
      cands.length ? candidateNote(cands) : null);
  }

  function quantityCard(q, hoistedReason) {
    const tone = STATE_TONE[q.state] || "";
    return h("div.card.ima-q" + (tone ? ".tint-" + tone : ""),
      h("div.row.wrapline",
        h("span.dot" + (tone ? "." + tone : "")),
        h("span.ima-q-label", q.label),
        h("span.pill" + (tone ? "." + tone : "") + ".right",
          STATE_LABEL[q.state] || q.state)),
      q.value !== null && q.value !== undefined
        ? h("div.stat-tile", { style: { marginTop: "10px" } },
            h("div.v" + (q.state === "stale" ? ".warn" : ".ok"),
              String(q.value), h("small", q.unit || "")),
            q.at ? h("div.n", "read " + ago(q.at)) : null)
        : h("div.ima-q-dash", "—", h("span.ima-q-unit", q.unit || "")),
      h("p.muted.ima-q-about", q.about),
      hoistedReason
        ? h("p.muted.ima-q-next", "Reason and command below.")
        : [q.note ? h("p.lede.ima-q-note", q.note) : null,
           q.next ? h("p.muted.ima-q-next", q.next) : null,
           q.state === "undiscovered" && q.command
             ? command(q.command, q.safety) : null]);
  }

  function legend() {
    const means = (doc && doc.states) || {};
    return h("div.card.flat.ima-legend",
      h("div.eyebrow", "What the four states mean"),
      h("div.ima-legend-grid",
        ...Object.keys(STATE_LABEL).map((k) => h("div.ima-legend-row",
          h("span.pill" + (STATE_TONE[k] ? "." + STATE_TONE[k] : ""),
            STATE_LABEL[k]),
          h("span.muted", means[k] || "")))));
  }

  function candidateNote(cands) {
    return h("div.card.ima-cand",
      h("div.eyebrow", "Unvalidated responders"),
      h("p.lede",
        "Addresses on the hybrid modules that answered, where nobody has "
        + "established what the answer means. The profile format's own rule is "
        + "that an entry below `validated` must not drive a display, so these "
        + "are listed and not drawn."),
      h("div.ima-cand-list", ...cands.map((c) => h("div.ima-cand-row",
        h("div.row.wrapline",
          h("span.ima-mono", c.request || c.id),
          h("span.muted", `${c.header} · ${c.label}`),
          h("span.pill.info.right", c.confidence)),
        h("p.muted", c.why || "")))));
  }

  // --------------------------------------------------- what they monitor
  //
  // The prize. Present it as what it is: an inventory of what each hybrid ECU
  // was built to measure, grouped by the decoder's own subsystem rule, with
  // every code the repo cannot name labelled as unnamed rather than guessed.
  function catalogues() {
    const mods = (doc && doc.modules) || [];
    const withCat = mods.filter((m) => (m.catalogue || []).length);
    if (!withCat.length) return null;

    const total = withCat.reduce((n, m) => n + m.catalogue.length, 0);
    return h("section.sect",
      h("div.head",
        h("div", h("div.eyebrow", "Honda-specific"),
          h("div.title", { style: { fontSize: "1.05rem" } },
            "What the hybrid controllers monitor")),
        h("span.muted.right", `${total} codes`)),
      h("p.lede",
        "Service 0x19, subfunction 0x0A: every fault code each module is "
        + "capable of setting. This is not a list of faults — it is a map of "
        + "what Honda built these two ECUs to measure, and a generic scan tool "
        + "never shows it."),
      ...withCat.map(catalogueCard));
  }

  function catalogueCard(m) {
    const groups = new Map();
    for (const code of m.catalogue) {
      const g = groupOf(code);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(code);
    }
    const named = m.catalogue.filter((c) => describe(c).sourced).length;

    return h("div.card",
      h("div.row.wrapline",
        h("span.ima-mono", m.header),
        h("span.ima-mod-label", m.label),
        h("span.pill.right", `${m.catalogue.length} codes`)),
      h("p.muted", { style: { marginTop: "6px" } },
        `Captured ${m.catalogue_seen} time${m.catalogue_seen === 1 ? "" : "s"}`
        + (m.catalogue_last ? `, most recently ${ago(m.catalogue_last)}` : "")
        // "the rest are named by Honda" was wrong, and wrong in the direction
        // that matters: 54 of the 69 distinct codes captured have '0' as their
        // second character, which is the generic SAE range, not a Honda one.
        // Claiming Honda authorship for codes we simply cannot name invents an
        // origin for them. Say only what is true -- this app has no name for
        // them -- and leave where they came from alone.
        + `. ${named} of ${m.catalogue.length} appear in this app's own `
        + "description table; the rest are shown as unnamed rather than "
        + "guessed at."),
      ...[...groups.entries()]
        .sort((a, b) => b[1].length - a[1].length)
        .map(([name, codes]) => h("div.ima-group",
          h("div.ima-group-head",
            h("span", name),
            h("span.muted.right", String(codes.length))),
          h("div.ima-chips", ...codes.sort().map(codeChip)))));
  }

  function codeChip(code) {
    const d = describe(code);
    return h("span.ima-chip" + (d.sourced ? ".sourced" : ""),
      { title: `${d.title}${d.note ? " · " + d.note : ""}` },
      h("span.ima-chip-c", baseCode(code)),
      failType(code) ? h("span.ima-chip-f", failType(code)) : null);
  }

  // -------------------------------------------------------- what is flagged
  //
  // The dangerous section. The caveat leads.
  function flagged() {
    const mods = ((doc && doc.modules) || []).filter((m) => m.observations);
    if (!mods.length) return null;
    const allNotRun = mods.every((m) => m.all_not_run);
    const obs = mods.reduce((n, m) => n + m.observations, 0);

    const _ex = explain(h, "dtcstatus");

    return h("section.sect",
      h("div.head",
        h("div", h("div.eyebrow", "Currently flagged"),
          h("div.title", { style: { fontSize: "1.05rem" } },
            allNotRun
              ? "Monitors that have not completed — not faults"
              : "What the hybrid modules are flagging")),
        h("span.muted.right", `${obs} observations`)),
      _ex,
      allNotRun
        ? h("div.card.tint-warn",
            h("div.row", h("span.dot.warn"),
              h("span", { style: { fontWeight: "600" } },
                "Every status byte ever captured here says the same thing")),
            h("p.lede", { style: { marginTop: "8px" } },
              `All ${obs} status observations from these modules carry status `
              + "bit 0x40 — the monitor has not run this operation cycle. Not "
              + "one has ever reported a pass, a fail, a pending or a "
              + "confirmed bit."),
            h("p.lede", { style: { marginTop: "8px" } },
              "So the correct sentence is \"these modules have monitors that "
              + "have not completed since the last clear\", not \"this car has "
              + "hybrid fault codes\". The counts swinging between samples say "
              + "the same thing: a fault list does not fluctuate, a not-run "
              + "list does."),
            h("p.lede", { style: { marginTop: "8px" } },
              "Only a long drive changes this. The monitors wait on speed, "
              + "load and temperature, and no amount of probing in a driveway "
              + "will make them run."))
        : null,
      h("div.grid.g2", ...mods.map(flaggedCard)));
  }

  function flaggedCard(m) {
    const latest = (m.count_seen || [])[m.count_seen.length - 1];
    const ceiling = Math.max(1, (m.catalogue || []).length);

    // A real gauge on a real number: how many monitors the module was flagging
    // at the last sample, against the ceiling of how many it could possibly
    // flag. Drawn because there is a measurement behind it — which is exactly
    // why no gauge is drawn for state of charge.
    const g = latest
      ? makeGauge("bar", { label: "Flagged", scale: { min: 0, max: ceiling } })
      : null;
    if (g) {
      g.update({ v: String(latest.n), n: `of ${ceiling} possible`, tone: "" },
               latest.n);
    }

    return h("div.card",
      h("div.row.wrapline",
        h("span.ima-mono", m.header),
        h("span.ima-mod-label", m.label),
        h("span.pill.right", `${m.flagged.length} distinct`)),
      g ? h("div", { style: { marginTop: "10px" } }, g.el) : null,
      latest
        ? h("p.muted", { style: { marginTop: "8px" } },
            `Last sampled ${ago(latest.t)}`
            + (latest.volts ? ` at ${latest.volts} V` : "")
            + (m.distinct_counts && m.distinct_counts.length > 1
              ? `. Across the whole record the count has been `
                + m.distinct_counts.join(", ") + "."
              : "."))
        : null,
      h("div.ima-flags",
        ...m.flagged.map((f) => {
          const d = describe(f.code);
          return h("div.ima-flag",
            h("div.row.wrapline",
              h("span.ima-mono", baseCode(f.code)),
              failType(f.code) ? h("span.ima-chip-f", failType(f.code)) : null,
              h("span.muted.right", `seen ${f.seen}×`)),
            h("div.row.wrapline",
              h("span.muted", d.title),
              d.note ? h("span.muted", "· " + d.note) : null),
            h("div.row.wrapline",
              ...Object.keys(f.flags || {}).map((k) =>
                h("span.pill.warn", k)),
              f.last ? h("span.muted.right", fullDate(f.last)) : null));
        })));
  }

  // ------------------------------------------------------ pack health frame
  function health() {
    const hh = (doc && doc.health) || {};
    const series = hh.series || [];
    const cap = hh.capacity || {};

    return h("section.sect",
      h("div.head",
        h("div", h("div.eyebrow", "Over time"),
          h("div.title", { style: { fontSize: "1.05rem" } },
            "Pack health, and the frame waiting to hold it")),
        hh.span
          ? h("span.muted.right",
              `${hh.span.days < 1 ? "under a day" : Math.round(hh.span.days) + " days"} of record`)
          : null),

      // The honest centrepiece: the series that would show degradation is
      // empty, and saying why is worth more than drawing a flat line.
      h("div.card" + (cap.have ? "" : ".ima-empty"),
        h("div.eyebrow", "Pack capacity"),
        h("div.title", { style: { fontSize: "1rem", marginTop: "2px" } },
          cap.have ? "Trend" : "No history to draw"),
        h("p.lede", { style: { marginTop: "6px" } }, cap.why || ""),
        cap.fills_when
          ? h("p.muted", { style: { marginTop: "8px" } },
              "It fills from the first reading onward: " + cap.fills_when)
          : null),

      series.length
        ? h("div.grid.g2", ...series.map(seriesCard))
        : h("div.card", h("p.lede",
            "Nothing longitudinal has been captured yet. Every drive with "
            + "`omacar dtclog` running adds points here.")));
  }

  function seriesCard(s) {
    const canvas = h("canvas.ima-spark", { height: 44 });
    // Queued rather than drawn inline: the canvas has no width until it is in
    // the document, and a sparkline sized against zero draws nothing at all.
    requestAnimationFrame(() => {
      try {
        const tint = getComputedStyle(document.documentElement)
          .getPropertyValue("--info").trim();
        sparkline(canvas, s.points.map((p) => p.v), { height: 44, tint });
      } catch { /* a canvas the browser will not size is not worth a crash */ }
    });
    return h("div.card",
      h("div.row.wrapline",
        h("span", { style: { fontWeight: "600", fontSize: ".82rem" } }, s.label),
        h("span.muted.right", s.what)),
      canvas,
      h("div.row.wrapline", { style: { marginTop: "6px" } },
        h("span.muted", `${s.n} samples`),
        h("span.muted.right", `${s.min} – ${s.max}`)),
      s.note ? h("p.muted", { style: { marginTop: "6px" } }, s.note) : null);
  }

  // ------------------------------------------------------------- discovery
  function discovery() {
    const d = (doc && doc.discovery) || {};
    const answered = d.answered || [];
    const sweeps = d.sweeps || [];
    const ranges = (d.ranges || []).filter((r) => r.hybrid);
    const next = d.next || [];
    const _ex = explain(h, "prospect");

    return h("section.sect",
      h("div.head",
        h("div", h("div.eyebrow", "Discovery"),
          h("div.title", { style: { fontSize: "1.05rem" } },
            "What has been asked, what answered, what is next")),
        h("span.muted.right", `${sweeps.length} sweeps on record`)),
      _ex,

      h("div.grid.g2",
        h("div.card",
          h("div.eyebrow", "Answered"),
          answered.length
            ? h("div.ima-rows", ...answered.map((a) => h("div.ima-row",
                h("div.row.wrapline",
                  h("span.dot.ok"),
                  h("span.ima-mono", a.header),
                  h("span.muted", `${a.service} ${a.sub}`)),
                h("p.muted", a.what
                  + (a.at ? " · last " + ago(a.at) : "")))))
            : h("p.lede", "Nothing has answered yet.")),

        h("div.card",
          h("div.eyebrow", "Swept"),
          sweeps.length
            ? h("div.ima-rows", ...sweeps.map(sweepRow))
            : h("p.lede", "No sweep has been saved for this car.")),
      ),

      h("div.card",
        h("div.eyebrow", "Identifier space still unasked"),
        h("p.lede",
          d.frontier_empty
            ? "No automatic sweep has ever recorded a range on this car, so the "
              + "resume file is empty. `omacar discover` writes it as it goes "
              + "and picks up where it left off weeks later."
            : "What the resumable sweep has covered so far, per module and "
              + "service."),
        h("div.ima-rows", ...ranges.map((r) => h("div.ima-row",
          h("div.row.wrapline",
            h("span.ima-mono", r.header),
            h("span.muted", r.service),
            h("span.muted.right",
              `${r.percent}% of ${grouped(r.total)} identifiers`)),
          h("div.meter", { style: { marginTop: "6px" } },
            h("i", { style: { width: Math.max(0.5, r.percent) + "%" } })),
          r.next
            ? h("p.muted", { style: { marginTop: "4px" } },
                "next unasked block: " + r.next)
            : h("p.muted", { style: { marginTop: "4px" } }, "range complete"))))),

      next.length
        ? h("div.sect",
            h("div.eyebrow", "What to run next"),
            ...next.map(nextCard))
        : null);
  }

  function sweepRow(s) {
    // A responder with a zero-length payload answered with nothing. Saying so
    // beside the hit is the difference between a screen that reports three
    // finds and one that reports the truth about them.
    const hollow = s.empty && s.empty >= s.responders && s.responders > 0;
    return h("div.ima-row",
      h("div.row.wrapline",
        h("span.dot" + (s.responders && !hollow ? ".ok" : "")),
        h("span.muted", s.service),
        h("span.ima-mono", (s.headers || []).join(" ")),
        h("span.muted.right", s.at ? ago(s.at) : "")),
      h("p.muted",
        hollow
          ? `${s.responders} "responders", all with empty payloads — the `
            + "adapter echoing the request, not the module answering."
          : s.responders
            ? `${s.responders} responder${s.responders === 1 ? "" : "s"}, `
              + `${s.varying} with bytes that moved`
            : "nothing answered"));
  }

  function nextCard(st) {
    return h("div.card",
      h("div.row.wrapline",
        h("span", { style: { fontWeight: "600" } }, st.title),
        st.cost ? h("span.pill.right", st.cost) : null),
      h("p.lede", { style: { marginTop: "6px" } }, st.why),
      st.note ? h("p.muted", { style: { marginTop: "6px" } }, st.note) : null,
      command(st.command, st.safety));
  }

  // One command, copyable. The safety line rides with it rather than living in
  // a paragraph somewhere above, because the line that matters is the one
  // beside the thing you are about to run.
  function command(cmd, safety) {
    return h("div.ima-cmd",
      h("div.row.wrapline",
        h("code.ima-cmd-text", cmd),
        h("button.btn.sm.right", {
          onclick: async () => {
            try {
              await navigator.clipboard.writeText(cmd);
              toast("Command copied.");
            } catch {
              toast("Could not copy — select it by hand.", "warn");
            }
          },
        }, "Copy")),
      safety ? h("p.muted.ima-cmd-safety", safety) : null);
  }

  // ------------------------------------------------------------------ draw
  function draw() {
    clear(wrap);
    if (loading && !doc) {
      wrap.appendChild(h("div.card", h("div.skel")));
      return;
    }
    if (err) {
      wrap.appendChild(h("div.card.tint-bad",
        h("div.title", { style: { fontSize: "1rem" } },
          "Could not read the IMA record"),
        h("p.lede", err),
        h("p.muted",
          "This screen reads files on disk and never talks to the car, so this "
          + "is the server, not the adapter.")));
      return;
    }
    if (doc && doc.error) {
      wrap.appendChild(h("div.card.tint-warn",
        h("div.title", { style: { fontSize: "1rem" } },
          "The IMA record could not be assembled"),
        h("p.lede", doc.error)));
    }
    wrap.appendChild(header());
    const reg = register(); if (reg) wrap.appendChild(reg);
    const cat = catalogues(); if (cat) wrap.appendChild(cat);
    const fl = flagged(); if (fl) wrap.appendChild(fl);
    wrap.appendChild(health());
    wrap.appendChild(discovery());
  }

  draw();
  load();

  // WHILE THE CAR IS PLUGGED IN, THIS SCREEN HAS TO MOVE.
  //
  // It did not. The view rendered once at mount and then froze: open it on a
  // drive and the state of charge would sit at whatever it was the moment you
  // opened the page, for the whole journey, with no indication it had stopped
  // being now. That is worse than showing nothing, because a stale number
  // reads exactly like a live one.
  //
  // Six seconds, not four times a second. /api/ima is assembled by parsing
  // drive logs on every call, and it is a diagnostic page rather than a gauge
  // — pack charge moves on the timescale of a hill. The quiet poll only
  // redraws when the payload actually changed, so a parked car costs one
  // request and no repaint at all.
  poll = setInterval(() => load(true), 6000);
  return () => clearInterval(poll);
}
