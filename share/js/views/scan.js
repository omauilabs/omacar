// The full-system code scan, and the report it produces.
//
// This is the feature that separates a scan tool from a code reader. A code
// reader talks to the engine module and tells you what it has. A scan tool
// walks every control unit in the car and hands you one page that says which
// of them are holding faults — which is how you find out that the reason the
// engine light is on is a body module problem, and how a workshop proves what
// a car looked like before it touched it.
//
// The one thing this will not do is imply it read a module it could not.
// Generic OBD-II reaches the powertrain and nothing else; every other unit
// here needs a manufacturer protocol, and the report marks the difference on
// every line rather than in a footnote.

import { h, clear, store, api, dist, fullDate, toast, sevTone, confirmDialog, since } from "../core.js";
import { explain } from "../learn.js";
import { vin as maskVin } from "../privacy.js";

// WAS 190ms PER MODULE OF PURE ANIMATION.
//
// The old loop fetched the entire report first and then walked the modules
// saying "reading…", sleeping 190ms at each one. Nothing was being read during
// that pause -- the data was already in hand. On a car reachable only over
// generic OBD-II there is exactly one module (the powertrain), so the whole
// "full system scan" was a fifth of a second of theatre over cached data, and
// it was correctly read as fake.
//
// The scan now asks the daemon to re-read the car and waits for the real
// thing. What is left here is a short settle so a state change is visible
// rather than instantaneous -- not a stand-in for work.
const SETTLE_MS = 60;

export default function scan(root) {
  // Learn mode: renders only when the reader asked for it.
  const _ex = explain(h, "gates");
  if (_ex) root.appendChild(_ex);

  let alive = true;
  const state = { report: null, running: false, progress: 0 };

  const head = h("section.sect",
    h("div.head",
      h("div", h("div.eyebrow", "Diagnostics"), h("div.title", "Full system scan")),
      h("div.right.row",
        h("button.btn", { id: "print", onclick: () => window.print() }, "Print report"),
        h("button.btn.primary", { id: "run" }, "Scan all systems"))),
    h("p.lede",
      "Walks every control unit, reads what each is holding, and files the result. "
      + "Run one before you touch a car and one after: the pair is the proof of "
      + "what you found and what you fixed."));
  root.appendChild(head);

  const body = h("div.sect");
  root.appendChild(body);

  const runBtn = head.querySelector("#run");
  head.querySelector("#print").disabled = true;

  function setRunning(on) {
    state.running = on;
    runBtn.disabled = on;
    runBtn.textContent = on ? "Scanning…" : "Scan all systems";
  }

  async function run() {
    setRunning(true);
    clear(body);
    const car = store.car || {};
    const mods = car.modules || [];
    const lines = new Map();

    const list = h("div.card");
    list.appendChild(h("div.row", h("div.eyebrow", "Reading modules"),
      h("div.muted.right", `${mods.length} control units`)));
    const wrapEl = h("div", { style: { marginTop: "10px" } });
    list.appendChild(wrapEl);
    body.appendChild(list);

    for (const m of mods) {
      const bar = h("i");
      const status = h("span.muted", "waiting");
      const line = h("div.scanline",
        h("div.mname",
          h("span.dot"),
          h("span", m.name),
          h("span.maddr", m.addr),
          m.generic ? null : h("span.pill", { title: "needs a manufacturer protocol" }, "OEM")),
        status,
        h("div.scan-prog", { style: { gridColumn: "1 / -1" } }, bar));
      lines.set(m.id, { line, bar, status, dot: line.querySelector(".dot") });
      wrapEl.appendChild(line);
    }

    // The sweep is paced rather than instant. On a real car each module takes
    // a moment to answer and you watch it happen; showing a finished table
    // immediately would hide which unit was slow or silent, and that is
    // diagnostic information in itself.
    const report = await api.scan().catch((e) => { toast(String(e.message || e), "bad"); return null; });
    if (!report || !alive) { setRunning(false); return; }

    for (const m of report.modules) {
      if (!alive) return;
      const ui = lines.get(m.id);
      if (ui) {
        ui.status.textContent = "reading…";
        ui.bar.style.width = "100%";
      }
      await sleep(SETTLE_MS);
      if (!ui) continue;
      ui.bar.style.background = m.active
        ? (m.worst === "critical" ? "var(--bad)" : "var(--warn)") : "var(--ok)";
      ui.status.replaceChildren(
        m.active
          ? h("span.pill." + (m.worst === "critical" ? "bad" : "warn"),
              `${m.active} code${m.active > 1 ? "s" : ""}`)
          : h("span.pill.ok", "clean"));
      ui.dot.className = "dot " + (m.active ? (m.worst === "critical" ? "bad" : "warn") : "ok");
    }

    await sleep(240);
    if (!alive) return;
    state.report = report;
    head.querySelector("#print").disabled = false;
    clear(body);

    // SAY WHICH IT IS. A stale report is still useful -- it is what the car
    // last said -- but the person reading it has to know it is not what the
    // car says right now, and has to be told without hunting for it.
    if (!report.fresh) {
      body.appendChild(h("div.note.warn",
        h("strong", "Showing the last scan on file. "),
        report.stale_reason
          // The server's reason is a clause ("the daemon is not running, …");
          // here it starts a sentence.
          ? report.stale_reason.charAt(0).toUpperCase() + report.stale_reason.slice(1) + "."
          : "The car was not re-read just now.",
        report.surveyed_at
          ? " Last read " + since(Date.now() / 1000 - report.surveyed_at) + "."
          : ""));
    }

    body.appendChild(renderReport(report));
    setRunning(false);
  }

  runBtn.addEventListener("click", run);

  // Land already scanned. Somebody opening this screen wants the report, not
  // a button that produces one.
  run();

  return () => { alive = false; };
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function renderReport(r) {
  const out = h("div.sect");
  const t = r.totals;
  const tone = t.codes ? (r.modules.some((m) => m.worst === "critical") ? "bad" : "warn") : "ok";

  out.appendChild(h("div.card.tint-" + tone,
    h("div.row.wrapline",
      h("div",
        h("div.eyebrow", "Vehicle system report"),
        h("div.title", { style: { fontSize: "1.4rem", marginTop: "2px" } },
          t.codes ? `${t.codes} code${t.codes > 1 ? "s" : ""} in ${t.with_codes} of ${t.modules} module${t.modules === 1 ? "" : "s"}`
                  : t.modules === 1 ? "The 1 module is clean" : `All ${t.modules} modules clean`),
        h("p.muted", { style: { marginTop: "4px" } },
          `${r.vehicle}  ·  ${maskVin(r.vin) || "no VIN"}  ·  ${dist(r.odometer)}  ·  ${fullDate(r.at)}`)),
      h("div.right.row.wrapline",
        h("span.pill" + (r.readiness.ready ? ".ok" : ".warn"),
          r.readiness.ready ? "emissions ready" : `${r.readiness.incomplete} monitors incomplete`),
        r.mode06_failed.length
          ? h("span.pill.bad", `${r.mode06_failed.length} on-board test${r.mode06_failed.length > 1 ? "s" : ""} failed`)
          : h("span.pill.ok", "on-board tests pass"),
        r.service_due ? h("span.pill.warn", `${r.service_due} service due`) : null))));

  for (const m of r.modules) {
    const clean = !m.active;
    const card = h("div.card" + (clean ? "" : ".tint-" + (m.worst === "critical" ? "bad" : "warn")));
    card.appendChild(h("div.row.wrapline",
      h("span.dot" + (clean ? ".ok" : m.worst === "critical" ? ".bad" : ".warn")),
      h("span", { style: { fontWeight: "600" } }, m.name),
      h("span.muted", `${m.system}  ·  ${m.addr}`),
      m.generic
        ? h("span.pill.info", { title: "a plain OBD-II adapter can read this one" }, "generic")
        : h("span.pill", { title: "needs a manufacturer protocol — OmaCar reads it here because this is a simulated car" }, "OEM protocol"),
      h("span.muted.right", m.sw || "")));

    if (clean) {
      card.appendChild(h("p.muted", { style: { marginTop: "8px" } }, "No codes stored."));
    } else {
      const list = h("div", { style: { marginTop: "10px", display: "grid", gap: "6px" } });
      for (const c of m.codes) {
        list.appendChild(h("button.rowitem", {
          onclick: () => { location.hash = "#codes/" + c.code; },
        },
          h("span.code" + (sevTone(c.severity) ? "" : ""), { style: {
            color: c.active ? (c.severity === "critical" ? "var(--bad)" : "var(--warn)") : "var(--faint)",
          } }, c.code),
          h("span.desc", c.descr),
          h("span.pill" + (c.status === "cleared" ? "" : "." + (sevTone(c.severity) || "warn")), c.status),
          c.count ? h("span.muted", `${c.count}×`) : null));
      }
      card.appendChild(list);
    }
    out.appendChild(card);
  }

  out.appendChild(h("div.card.flat",
    h("div.eyebrow", "About this report"),
    h("p.lede", { style: { marginTop: "6px" } },
      "Modules marked generic are readable by any OBD-II adapter. The rest speak "
      + "manufacturer protocols that a generic tool cannot address"
      + (r.simulated
        ? " — this vehicle is simulated, so OmaCar can show them here. On a real car with a plain adapter those lines would read “not addressable”."
        : ".")),
    h("p.muted", { style: { marginTop: "6px" } },
      `Filed as record #${r.record_id}. Keep the pre-repair scan: it is the only proof of what a car arrived with.`)));

  return out;
}
