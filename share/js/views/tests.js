// Functional tests — commanding the car rather than only asking it.
//
// The difference between a reader and a scan tool. A fan you can switch on is
// a fan you can prove; a cylinder you can silence is a compression test you
// did not have to do. Every test here shows a live trace of the channels it
// affects while it runs, because the reading during the command IS the result
// — a pass or fail printed afterwards would be somebody else's judgement.
//
// The catalogue marks every test as needing a manufacturer protocol, because
// on a real car it does. Generic OBD-II has Mode 08 in the standard and almost
// nobody implements it.

import { h, clear, store, api, toast, confirmDialog, temp, U } from "../core.js";
import { explain } from "../learn.js";
import { scope, PALETTE } from "../charts.js";

const CH = {
  rpm: { id: "rpm", label: "Engine speed", unit: "rpm", dp: 0, get: (v) => v.RPM },
  load: { id: "load", label: "Engine load", unit: "%", dp: 0, get: (v) => v.ENGINE_LOAD },
  coolant: { id: "coolant", label: "Coolant", unit: "temp", dp: 1, get: (v) => v.COOLANT_TEMP },
  stft: { id: "stft", label: "Short fuel trim", unit: "%", dp: 1, get: (v) => v.SHORT_FUEL_TRIM_1 },
  throttle: { id: "throttle", label: "Throttle", unit: "%", dp: 0, get: (v) => v.THROTTLE_POS },
};

export default function tests(root) {
  // Learn mode: renders only when the reader asked for it.
  const _ex = explain(h, "mode06");
  if (_ex) root.appendChild(_ex);

  let cat = null, alive = true, running = null;
  // The trace is sampled from the live store rather than fetched: a test lasts
  // a few seconds and the samples are already arriving five times a second.
  let trace = [];
  let stopFns = [];

  root.appendChild(h("section.sect",
    h("div.head", h("div", h("div.eyebrow", "Bidirectional"),
      h("div.title", "Functional tests"))),
    h("p.lede",
      "Commands the car and shows you what it did. Every test below needs a "
      + "manufacturer protocol on a real vehicle — generic OBD-II cannot "
      + "actuate anything worth actuating.")));

  // WHAT PRESSING THE BUTTON ACTUALLY DOES, SAID ON THE SCREEN THAT HAS THE
  // BUTTON.
  //
  // The lede above is true and too soft. A command from this screen is written
  // to a file, and today the only thing that reads that file is the simulator:
  // on a real car the button runs, the trace stays flat, and nothing on the
  // vehicle moves. Somebody filming this, or trusting it in a workshop, would
  // have no way to tell that from a test that ran and found nothing wrong --
  // which is the worst failure a diagnostic tool has, because it looks like a
  // result.
  //
  // Read from the vehicle record rather than hardcoded, so it stops saying the
  // simulator sentence on the day a real actuator path lands.
  root.appendChild(h("div.card.tint-warn.tests-reach", h("div.reach")));

  function paintReach() {
    const el = root.querySelector(".tests-reach .reach");
    if (!el) return;
    const sim = !!(store.car && store.car.simulated);
    clear(el);
    if (sim) {
      el.appendChild(h("div.title", "This is the simulated car"));
      el.appendChild(h("p.lede",
        "The commands below reach the simulator, and it answers: the trace you "
        + "see is what the model did. That is a real exercise of this screen "
        + "and of the safety gates behind it. It is not your car."));
    } else {
      el.appendChild(h("div.title", "These do not reach this car yet"));
      el.appendChild(h("p.lede",
        "A command from here is written for the vehicle to pick up, and on a "
        + "real car nothing picks it up yet. Actuator control is UDS service "
        + "0x2F, which needs a controllable identifier for this specific "
        + "model — none has been discovered for this one — and on most modules "
        + "it sits behind security access whose key is not public. The button "
        + "will run and the trace will stay flat. That is the tool being "
        + "honest, not the car being healthy."));
    }
  }
  paintReach();
  stopFns.push(store.on("car", paintReach));

  const body = h("div.sect");
  root.appendChild(body);
  body.appendChild(h("div.card", h("div.skel")));

  fetch("data/tests.json", { cache: "no-store" })
    .then((r) => r.json())
    .then((d) => { if (!alive) return; cat = d; paint(); })
    .catch(() => { clear(body); body.appendChild(h("div.empty", "Test catalogue unavailable.")); });

  function paint() {
    clear(body);
    for (const seq of cat.sequences || []) body.appendChild(sequenceCard(seq));
    const groups = {};
    for (const t of cat.tests || []) (groups[t.group] = groups[t.group] || []).push(t);
    for (const [group, list] of Object.entries(groups)) {
      body.appendChild(h("div.eyebrow", group));
      const grid = h("div.grid.g2");
      for (const t of list) grid.appendChild(testCard(t));
      body.appendChild(grid);
    }
  }

  // ---- one test -----------------------------------------------------------
  function testCard(t) {
    const traceBox = h("div", { style: { display: "none" } });
    const canvas = h("canvas");
    const readout = h("div.row.wrapline", { style: { marginTop: "8px", gap: "18px" } });
    traceBox.appendChild(h("div.scope", { style: { marginTop: "12px" } }, canvas));
    traceBox.appendChild(readout);

    const btn = h("button.btn", "Run for " + t.seconds + "s");
    const bar = h("div.meter.thin", { style: { marginTop: "10px", display: "none" } }, h("i"));

    btn.addEventListener("click", () => run(t, { btn, bar, traceBox, canvas, readout }));

    return h("div.card",
      h("div.row.wrapline",
        h("div", { style: { fontWeight: "600" } }, t.name),
        h("span.pill.right", t.protocol === "manufacturer" ? "OEM protocol" : "generic")),
      h("p.lede", { style: { marginTop: "6px" } }, t.what),
      h("p.muted", { style: { marginTop: "6px" } }, t.why),
      h("div.row.wrapline", { style: { marginTop: "10px", gap: "14px" } },
        h("span.muted", t.needs)),
      t.caution ? h("p", { style: { marginTop: "8px", fontSize: ".74rem", color: "var(--warn)" } },
        "⚠  " + t.caution) : null,
      h("div.row", { style: { marginTop: "12px" } }, btn),
      bar, traceBox,
      h("p.muted", { style: { marginTop: "10px" } },
        h("span.eyebrow", "Expect  "), t.expect));
  }

  async function run(t, ui) {
    if (running) { toast("A test is already running.", "bad"); return; }
    if (t.caution) {
      const ok = await confirmDialog({
        title: "Run " + t.name + "?",
        body: h("div.sect", h("p.lede", t.caution),
          h("p.lede", h("span.eyebrow", "Requires  "), t.needs)),
        confirm: "Run the test",
        tone: "danger",
      });
      if (!ok) return;
    }
    running = t.id;
    trace = [];
    ui.btn.disabled = true;
    ui.btn.textContent = "Running…";
    ui.bar.style.display = "block";
    ui.traceBox.style.display = "block";

    const chans = (t.watch || []).map((id, i) =>
      Object.assign({}, CH[id], { tint: PALETTE[i % PALETTE.length],
                                  get: (row) => row[id] }));

    try {
      await api.actuate({ test: t.id, duration: t.seconds });
    } catch (e) {
      toast(String(e.message || e), "bad");
      finish(ui, t);
      return;
    }

    const started = Date.now();
    const ms = t.seconds * 1000 + 1200;
    const tick = setInterval(() => {
      const frac = Math.min(1, (Date.now() - started) / ms);
      ui.bar.firstChild.style.width = (frac * 100) + "%";
      const v = store.values;
      trace.push({ t: Date.now() / 1000, rpm: v.RPM, load: v.ENGINE_LOAD,
                   coolant: v.COOLANT_TEMP, stft: v.SHORT_FUEL_TRIM_1,
                   throttle: v.THROTTLE_POS });
      if (chans.length) scope(ui.canvas, { rows: trace, channels: chans, height: 62 * chans.length + 30 });
      paintReadout(ui.readout, chans, trace);
      if (frac >= 1) { clearInterval(tick); finish(ui, t); }
    }, 200);
    stopFns.push(() => clearInterval(tick));
  }

  function finish(ui, t) {
    running = null;
    ui.btn.disabled = false;
    ui.btn.textContent = "Run for " + t.seconds + "s";
    ui.bar.style.display = "none";
    api.actuate({ stop: true }).catch(() => { /* the command expires on its own */ });
  }

  function paintReadout(el, chans, rows) {
    clear(el);
    for (const c of chans) {
      const vals = rows.map(c.get).filter((v) => v !== null && v !== undefined);
      if (!vals.length) continue;
      const first = vals[0], last = vals[vals.length - 1];
      const d = last - first;
      el.appendChild(h("div.stat-tile",
        h("div.k", c.label),
        h("div.v", { style: { fontSize: "1.2rem" } }, fmt(last, c)),
        h("div.n" + (Math.abs(d) > 0.5 ? ".ok" : ""),
          (d >= 0 ? "+" : "") + fmt(d, c, true) + " since start")));
    }
  }

  // ---- the guided sequence ------------------------------------------------
  //
  // Four steps, one table, and the interpretation at the bottom. A balance
  // test is only useful as a comparison, so the result is a set rather than
  // four separate readings.
  function sequenceCard(seq) {
    const results = [];
    const tbody = h("tbody");
    const table = h("table.tbl",
      h("thead", h("tr", h("th", "Cylinder"), h("th.num", "Idle before"),
        h("th.num", "Idle with it off"), h("th.num", "Drop"), h("th", ""))),
      tbody);
    const verdict = h("div", { style: { marginTop: "12px" } });
    const btn = h("button.btn.primary", "Run the sequence");
    const bar = h("div.meter.thin", { style: { marginTop: "10px", display: "none" } }, h("i"));

    btn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "Run a cylinder balance test?",
        body: h("div.sect", h("p.lede", seq.caution),
          h("p.lede", h("span.eyebrow", "Requires  "), seq.needs)),
        confirm: "Run it",
      });
      if (!ok || running) return;
      running = seq.id;
      results.length = 0;
      clear(tbody); clear(verdict);
      btn.disabled = true;
      bar.style.display = "block";

      for (let i = 0; i < seq.steps.length; i++) {
        const step = seq.steps[i];
        btn.textContent = `Cylinder ${i + 1} of ${seq.steps.length}…`;
        // Hold the engine at idle for the baseline as well as the measurement.
        // Taking "before" from a car that happens to be moving and "during"
        // from one held at idle compares two different engines: every drop
        // comes out inflated and the difference between cylinders — the only
        // thing the test is for — is squeezed out of the numbers.
        await api.actuate({ test: "hold_idle", duration: 12 }).catch(() => {});
        await sleep(2600);
        const before = median(await sampleFor(1600, (v) => v.RPM));
        await api.actuate({ test: step, duration: seq.seconds }).catch(() => {});
        await sleep(1400);                       // let the drop settle
        const during = median(await sampleFor(seq.seconds * 1000 - 1600, (v) => v.RPM));
        results.push({ cyl: i + 1, before, during, drop: before - during });
        bar.firstChild.style.width = (((i + 1) / seq.steps.length) * 100) + "%";
        paintSeq();
      }
      await api.actuate({ stop: true }).catch(() => {});
      running = null;
      btn.disabled = false;
      btn.textContent = "Run the sequence";
      bar.style.display = "none";
      paintSeq(true);
    });

    function paintSeq(done) {
      clear(tbody);
      const drops = results.map((r) => r.drop).filter((d) => Number.isFinite(d));
      const best = drops.length ? Math.max(...drops) : 0;
      for (const r of results) {
        const share = best > 0 ? r.drop / best : 1;
        const weak = share < 1 - (seq.tolerance || 0.2);
        tbody.appendChild(h("tr",
          h("td", { style: { fontWeight: "600" } }, "Cylinder " + r.cyl),
          h("td.num.muted", Math.round(r.before) + " rpm"),
          h("td.num.muted", Math.round(r.during) + " rpm"),
          h("td.num", { style: { color: weak ? "var(--bad)" : "var(--ok)", fontWeight: "600" } },
            Math.round(r.drop) + " rpm"),
          h("td",
            h("div.meter.thin", { style: { minWidth: "110px" } },
              h("i", { style: { width: Math.max(3, share * 100) + "%",
                                background: weak ? "var(--bad)" : "var(--ok)" } })),
            weak ? h("div.muted", { style: { marginTop: "4px", color: "var(--bad)" } },
              Math.round((1 - share) * 100) + "% below the best cylinder") : null)));
      }
      if (!done) return;
      clear(verdict);
      if (!drops.length) return;
      const worst = results.reduce((a, b) => (a.drop < b.drop ? a : b));
      const share = best > 0 ? worst.drop / best : 1;
      if (share < 1 - (seq.tolerance || 0.2)) {
        verdict.appendChild(h("div.card.tint-bad",
          h("div.eyebrow", "Result"),
          h("div", { style: { marginTop: "6px", fontWeight: "600", fontSize: "1.02rem" } },
            `Cylinder ${worst.cyl} is not contributing its share`),
          h("p.lede", { style: { marginTop: "6px" } },
            `Switching it off cost ${Math.round(worst.drop)} rpm against `
            + `${Math.round(best)} rpm for the best cylinder — `
            + `${Math.round((1 - share) * 100)}% less. `
            + "It was already producing less than the others. Check ignition first "
            + "(swap the coil to another cylinder and see if the fault follows), "
            + "then the injector, then compression."),
          store.aiOn
            ? h("div.row", { style: { marginTop: "10px" } },
                h("button.btn.ai", { onclick: () => {
                  location.hash = "#advisor";
                  setTimeout(() => {
                    const el = document.querySelector('input[type="text"]');
                    if (el) {
                      el.value = `A cylinder balance test at idle gave these drops: `
                        + results.map((r) => `cylinder ${r.cyl} ${Math.round(r.drop)} rpm`).join(", ")
                        + `. What does that mean for this car, given its code history?`;
                      el.focus();
                    }
                  }, 350);
                } }, "Ask the advisor about this result"))
            : null));
      } else {
        verdict.appendChild(h("div.card.tint-ok",
          h("div.eyebrow", "Result"),
          h("div", { style: { marginTop: "6px", fontWeight: "600" } },
            "All four cylinders contributing evenly"),
          h("p.lede", { style: { marginTop: "6px" } },
            `The spread between best and worst is `
            + `${Math.round((1 - share) * 100)}%, inside the ${Math.round((seq.tolerance || 0.2) * 100)}% `
            + "this test calls even. Whatever the complaint is, it is not one dead cylinder.")));
      }
    }

    return h("div.card.tint-warn",
      h("div.row.wrapline",
        h("div.eyebrow", "Guided sequence"),
        h("span.pill.right", "OEM protocol")),
      h("div.title", { style: { fontSize: "1.2rem", marginTop: "4px" } }, seq.name),
      h("p.lede", { style: { marginTop: "6px" } }, seq.what),
      h("p.muted", { style: { marginTop: "6px" } }, seq.why),
      h("p", { style: { marginTop: "8px", fontSize: ".74rem", color: "var(--warn)" } },
        "⚠  " + seq.caution),
      h("div.row", { style: { marginTop: "12px" } }, btn),
      bar,
      h("div", { style: { marginTop: "14px", overflowX: "auto" } }, table),
      verdict,
      h("p.muted", { style: { marginTop: "10px" } },
        h("span.eyebrow", "Expect  "), seq.expect));
  }

  // Sample the live store for a while and hand back the values. The store is
  // already being refreshed four times a second by the router for this view.
  function sampleFor(ms, pick) {
    return new Promise((resolve) => {
      const out = [];
      const t = setInterval(() => {
        const v = pick(store.values);
        if (v !== null && v !== undefined) out.push(v);
      }, 160);
      setTimeout(() => { clearInterval(t); resolve(out); }, Math.max(400, ms));
      stopFns.push(() => clearInterval(t));
    });
  }

  return () => {
    alive = false;
    for (const f of stopFns) { try { f(); } catch { /* nothing to do */ } }
    // Never leave an actuator commanded because somebody changed screens.
    api.actuate({ stop: true }).catch(() => {});
  };
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function median(list) {
  const v = list.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return NaN;
  return v[Math.floor(v.length / 2)];
}

function fmt(v, c, signed) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const unit = c.unit === "temp" ? U.units.temp : c.unit;
  const n = c.unit === "temp" && !signed ? Number(temp(v, false)) : Number(v);
  return n.toFixed(c.dp) + " " + unit;
}
