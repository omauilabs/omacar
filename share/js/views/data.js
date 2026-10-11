// The data lab: several channels on one time axis, with a cursor.
//
// A gauge tells you a value now. A scope tells you what a value did, which is
// the only way to catch anything intermittent — the misfire that happens once
// a minute, the sensor that drops out over a bump, the trim that only goes
// wrong at cruise. The cursor reports every channel at one instant, because
// the diagnostic question is almost never "what was the coolant temperature",
// it is "what was everything else doing when the coolant temperature did that".
//
// Recording is a span, not a copy: the daemon is already writing every sample
// to disk, so a "recording" is two timestamps and a name. Nothing is
// duplicated and nothing can drift out of step with the record.

import { h, clear, store, api, toast, U, temp, speed, clockOf, shortDate, mins } from "../core.js";
import { explain } from "../learn.js";
import { scope, PALETTE } from "../charts.js";

const CHANNELS = [
  { id: "rpm", label: "Engine speed", unit: "rpm", dp: 0, min: 0 },
  { id: "speed", label: "Vehicle speed", unit: "speed", dp: 0, min: 0 },
  { id: "load", label: "Engine load", unit: "%", dp: 0, min: 0, max: 100 },
  { id: "throttle", label: "Throttle", unit: "%", dp: 0, min: 0, max: 100 },
  { id: "coolant", label: "Coolant", unit: "temp", dp: 0 },
  { id: "intake", label: "Intake air", unit: "temp", dp: 0 },
  { id: "maf", label: "Mass air flow", unit: "g/s", dp: 2, min: 0 },
  { id: "stft", label: "Short fuel trim", unit: "%", dp: 1 },
  { id: "ltft", label: "Long fuel trim", unit: "%", dp: 1 },
  { id: "timing", label: "Timing advance", unit: "°", dp: 1 },
  { id: "lphk", label: "Economy", unit: "econ", dp: 1 },
];

const SPANS = [
  { id: 10, label: "10 min" }, { id: 60, label: "1 hour" },
  { id: 360, label: "6 hours" }, { id: 1440, label: "24 hours" },
  { id: 10080, label: "7 days" },
];

const KEY = "omacar.channels";

export default function data(root, { arg } = {}) {
  // Learn mode: renders only when the reader asked for it.
  const _ex = explain(h, "pid");
  if (_ex) root.appendChild(_ex);

  let picked = load();
  // Wide by default and narrowing on demand. A parked car has nothing in the
  // last ten minutes, and a lab that opens on an empty grid looks broken when
  // it is merely idle.
  let mins = 1440;
  let rows = [];
  let mapper = null;
  let alive = true;
  let following = true;
  let widened = false;

  // A saved recording is a span, not a copy — the samples are already on disk,
  // so opening one is a matter of pointing the same viewer at two timestamps.
  // Nothing is duplicated, so nothing can drift out of step with the record.
  let playing = null;
  const banner = h("div");
  root.appendChild(banner);

  root.appendChild(h("section.sect",
    h("div.head", h("div", h("div.eyebrow", "Scanner"),
      h("div.title", { id: "lab-title" }, "Data lab")),
      h("div.right.row.wrapline",
        h("div.seg", SPANS.map((s) => h("button", {
          "aria-pressed": s.id === 1440 ? "true" : "false",
          onclick: (e) => {
            mins = s.id;
            for (const b of e.target.parentElement.children) b.setAttribute("aria-pressed", "false");
            e.target.setAttribute("aria-pressed", "true");
            refresh();
          },
        }, s.label))),
        h("button.btn", { onclick: () => saveRecording() }, "Save recording")))));

  // Channel picker. Colour is assigned by pick order rather than fixed per
  // channel, so two selected channels are always maximally distinguishable.
  const pick = h("div.chanpick");
  root.appendChild(h("div.card",
    h("div.eyebrow", "Channels"),
    h("div", { style: { marginTop: "10px" } }, pick)));

  const canvas = h("canvas");
  const cursorLine = h("div.cursorline", { style: { display: "none" } });
  const scopeBox = h("div.scope", canvas, cursorLine);
  root.appendChild(scopeBox);

  const readout = h("div.grid.g4");
  root.appendChild(h("div.card",
    h("div.row", h("div.eyebrow", "At the cursor"),
      h("span.muted.right", { id: "cursor-t" }, "hover the trace")),
    h("div", { style: { marginTop: "10px" } }, readout)));

  const stats = h("div.card");
  root.appendChild(stats);

  function paintPicker() {
    clear(pick);
    CHANNELS.forEach((c) => {
      const i = picked.indexOf(c.id);
      const on = i >= 0;
      pick.appendChild(h("button", {
        "aria-pressed": on ? "true" : "false",
        style: on ? { background: PALETTE[i % PALETTE.length],
                      borderColor: PALETTE[i % PALETTE.length], color: "#06090A" } : {},
        onclick: () => {
          if (on) picked = picked.filter((x) => x !== c.id);
          else if (picked.length < 6) picked = picked.concat(c.id);
          else { toast("Six channels is the most that stays readable."); return; }
          localStorage.setItem(KEY, JSON.stringify(picked));
          paintPicker(); draw();
        },
      }, c.label));
    });
  }

  function chans() {
    return picked.map((id, i) => {
      const c = CHANNELS.find((x) => x.id === id);
      return Object.assign({}, c, {
        tint: PALETTE[i % PALETTE.length],
        get: (r) => convert(c, r[c.id]),
      });
    });
  }

  function draw() {
    const cs = chans();
    mapper = scope(canvas, { rows, channels: cs, height: Math.max(220, 62 * cs.length) });
    paintStats(cs);
  }

  function paintStats(cs) {
    clear(stats);
    stats.appendChild(h("div.row", h("div.eyebrow", "Over this span"),
      h("span.muted.right", rows.length ? `${rows.length} samples` : "")));
    if (!rows.length) { stats.appendChild(h("div.empty", "Nothing recorded in this span.")); return; }
    const tbl = h("table.tbl", h("thead", h("tr",
      h("th", "Channel"), h("th.num", "Min"), h("th.num", "Mean"),
      h("th.num", "Max"), h("th.num", "Last"))));
    const tb = h("tbody");
    for (const c of cs) {
      const vals = rows.map(c.get).filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
      if (!vals.length) continue;
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      tb.appendChild(h("tr",
        h("td", h("div.chan", h("span.swatch", { style: { background: c.tint } }), c.label)),
        h("td.num", fmt(vals.reduce((a, b) => Math.min(a, b)), c)),
        h("td.num", fmt(mean, c)),
        h("td.num", fmt(vals.reduce((a, b) => Math.max(a, b)), c)),
        h("td.num", fmt(vals[vals.length - 1], c))));
    }
    tbl.appendChild(tb);
    stats.appendChild(h("div", { style: { marginTop: "10px", overflowX: "auto" } }, tbl));
  }

  async function openRecording(id) {
    try {
      const { records } = await api.records({ kind: "movie", n: 200 });
      const rec = (records || []).find((x) => String(x.id) === String(id));
      if (!rec || !rec.t0) { toast("That recording is not in the book.", "bad"); return; }
      playing = rec;
      document.getElementById("lab-title").textContent = "Recording";
      clear(banner);
      banner.appendChild(h("div.card.tint-ai",
        h("div.row.wrapline",
          h("span.pill.info", "recording"),
          h("div", { style: { fontWeight: "600" } }, rec.label || `#${rec.id}`),
          h("span.muted", `${shortDate(rec.t0)}  ${clockOf(rec.t0)} — ${clockOf(rec.t1)}`
            + `   ·   ${Math.round((rec.t1 - rec.t0) / 60)} min`
            + (rec.payload && rec.payload.rows ? `   ·   ${rec.payload.rows} samples` : "")),
          h("div.right.row", { style: { gap: "8px" } },
            store.aiOn ? h("button.btn.ai.sm", { onclick: () => {
              // Hand the advisor the span rather than the rows: it reads the
              // shape of each channel far better than ten thousand numbers,
              // and the bundle stays small enough to be cheap.
              sessionStorage.setItem("omacar.aiSpan", JSON.stringify([rec.t0, rec.t1]));
              location.hash = "#advisor/recording";
            } }, "Ask the advisor to read it") : null,
            h("button.btn.sm", { onclick: () => { location.hash = "#data"; location.reload(); } },
              "Back to live")))));
      const r = await api.history({ from: rec.t0, to: rec.t1, n: 3000 });
      rows = r.rows || [];
      following = false;
      draw();
    } catch (e) { toast(String(e.message || e), "bad"); }
  }

  async function refresh() {
    if (playing) return;
    try {
      let r = await api.history({ mins, n: 2500 });
      // If the chosen span is empty, say so — but widen once automatically on
      // the FIRST load, so opening the lab on a parked car lands on the last
      // drive rather than on an empty grid.
      if (!(r.rows || []).length && !widened) {
        widened = true;
        const bigger = SPANS.map((s) => s.id).filter((m) => m > mins);
        for (const m of bigger) {
          r = await api.history({ mins: m, n: 2500 });
          if ((r.rows || []).length) {
            mins = m;
            for (const b of document.querySelectorAll(".seg button"))
              b.setAttribute("aria-pressed", String(b.textContent ===
                (SPANS.find((s) => s.id === m) || {}).label));
            break;
          }
        }
      }
      if (!alive) return;
      rows = r.rows || [];
      draw();
    } catch (e) { toast(String(e.message || e), "bad"); }
  }

  async function saveRecording() {
    if (!rows.length) { toast("Nothing to save in this span.", "bad"); return; }
    const t0 = rows[0].t, t1 = rows[rows.length - 1].t;
    try {
      const r = await api.saveRecording(
        // "7h 27m", as Replay writes a session, not "447 min".
        `${mins(t1 - t0)} from ${shortDate(t0)} ${clockOf(t0)}`, t0, t1);
      toast(`Saved recording #${r.id} — ${r.rows} samples. It is in the Log.`);
    } catch (e) { toast(String(e.message || e), "bad"); }
  }

  scopeBox.addEventListener("mousemove", (e) => {
    if (!mapper) return;
    const box = scopeBox.getBoundingClientRect();
    const hit = mapper.at(e.clientX - box.left);
    cursorLine.style.display = "block";
    cursorLine.style.left = hit.x + "px";
    document.getElementById("cursor-t").textContent =
      `${shortDate(hit.row.t)}  ${clockOf(hit.row.t)}:${String(Math.floor(hit.row.t % 60)).padStart(2, "0")}`;
    clear(readout);
    for (const c of chans()) {
      const v = c.get(hit.row);
      readout.appendChild(h("div.stat-tile",
        h("div.k", h("span.chan", h("span.swatch", { style: { background: c.tint } }), c.label)),
        h("div.v", { style: { fontSize: "1.25rem" } }, fmt(v, c) || "—")));
    }
  });
  scopeBox.addEventListener("mouseleave", () => { cursorLine.style.display = "none"; });

  paintPicker();
  if (arg && arg.startsWith("rec:")) openRecording(arg.slice(4));
  else refresh();
  // Follow the live edge while the car is moving; a span you have to reload by
  // hand is a span that is always a minute stale.
  const timer = setInterval(() => { if (following && store.state === "driving") refresh(); }, 4000);
  window.addEventListener("resize", draw);

  return () => { alive = false; clearInterval(timer); window.removeEventListener("resize", draw); };
}

function load() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY));
    if (Array.isArray(v) && v.length) return v.slice(0, 6);
  } catch { /* a corrupt preference is not worth a crash */ }
  return ["rpm", "speed", "load", "ltft"];
}

// Channels come off the bus metric. Convert once, here, and everything
// downstream — the trace, the cursor, the statistics — is already in the
// units the rest of the tool uses.
function convert(c, v) {
  if (v === null || v === undefined) return null;
  if (c.unit === "temp") return U.imperial ? v * 9 / 5 + 32 : v;
  if (c.unit === "speed") return v * U.units.km;
  if (c.unit === "econ") return v > 0 ? (U.imperial ? 235.214583 / v : v) : null;
  return v;
}

function fmt(v, c) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const unit = c.unit === "temp" ? U.units.temp : c.unit === "speed" ? U.units.speed
    : c.unit === "econ" ? U.units.econ : c.unit;
  return `${Number(v).toFixed(c.dp)} ${unit}`;
}
