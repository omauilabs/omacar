// Replay — walk back through a drive you already took.
//
// The tool could already record: `samples` holds every reading at 1 Hz, and
// trips segment them into drives. What it could not do was give any of that
// back. You could see a chart of the last twenty minutes and nothing else --
// no way to step to the moment a fault set, no way to read the exact values at
// that instant, no way to get the numbers out.
//
// That gap matters most for the thing this tool is for. An intermittent fault
// is by definition not happening while you look at it; the whole value is in
// the recording. So: pick a drive, scrub to the moment, read every channel at
// that instant, and export the span if you want to work on it elsewhere.

import { h, store, api, toast, clockOf, mins } from "../core.js";
import { scope, PALETTE } from "../charts.js";
import { explain } from "../learn.js";

const CHANNELS = [
  { key: "speed", label: "Speed", unit: "km/h" },
  { key: "rpm", label: "RPM", unit: "" },
  { key: "load", label: "Load", unit: "%" },
  { key: "throttle", label: "Throttle", unit: "%" },
  { key: "coolant", label: "Coolant", unit: "°C" },
  { key: "intake", label: "Intake", unit: "°C" },
  { key: "maf", label: "MAF", unit: "g/s" },
  { key: "stft", label: "Short trim", unit: "%" },
  { key: "ltft", label: "Long trim", unit: "%" },
  { key: "timing", label: "Timing", unit: "°" },
];

export default function replay(root) {
  let spans = [];          // trips and saved recordings, newest first
  let chosen = null;
  let rows = [];
  let cursor = 0;          // index into rows
  let playing = false;
  let rate = 1;
  let timer = null;
  let picked = ["speed", "rpm", "coolant", "stft"];

  const wrap = h("div.replay");
  root.appendChild(wrap);

  async function loadSpans() {
    try {
      const [hist, recs] = await Promise.all([
        api.trips ? api.trips() : Promise.resolve({ trips: [] }),
        api.records(),
      ]);
      const trips = (hist.trips || []).map((t) => ({
        kind: "drive", t0: t.t0, t1: t.t1,
        label: `Drive · ${t.km != null ? t.km.toFixed(1) + " km" : "?"}`,
        detail: `${clockOf(t.t0)} — ${clockOf(t.t1)}`,
      }));
      const movies = (recs.records || [])
        .filter((r) => r.kind === "movie" && r.t0 && r.t1)
        .map((r) => ({
          kind: "recording", t0: r.t0, t1: r.t1,
          label: r.label || "Recording",
          detail: `${clockOf(r.t0)} — ${clockOf(r.t1)}`,
        }));
      spans = [...trips, ...movies].sort((a, b) => b.t0 - a.t0);
    } catch (e) {
      spans = [];
      toast("Could not list drives: " + (e.message || e), "bad");
    }
    draw();
  }

  async function open(span) {
    stop();
    chosen = span;
    rows = [];
    cursor = 0;
    draw();
    try {
      // Undecimated. /api/history thins to fit a graph, which is right for a
      // chart and wrong when you are stepping to one specific second.
      const r = await api.history({ from: span.t0, to: span.t1, n: 100000 });
      rows = r.samples || r.rows || [];
    } catch (e) {
      toast("Could not load that span: " + (e.message || e), "bad");
    }
    draw();
  }

  function stop() {
    playing = false;
    if (timer) { clearInterval(timer); timer = null; }
  }

  function play() {
    if (!rows.length) return;
    stop();
    playing = true;
    // Step by wall-clock, not by frame: samples are ~1 Hz, so at 4x we advance
    // four rows a second rather than redrawing four times as often.
    timer = setInterval(() => {
      cursor += 1;
      if (cursor >= rows.length - 1) { cursor = rows.length - 1; stop(); }
      paintCursor();
    }, 1000 / rate);
    draw();
  }

  // Only the parts that change with the cursor, so scrubbing stays smooth
  // rather than rebuilding the whole view on every step.
  function paintCursor() {
    const row = rows[cursor];
    if (!row) return;
    const slider = wrap.querySelector(".rp-slider");
    if (slider && Number(slider.value) !== cursor) slider.value = String(cursor);
    const at = wrap.querySelector(".rp-at");
    if (at) at.textContent = clockOf(row.t);
    const el = wrap.querySelector(".rp-elapsed");
    if (el && rows.length) el.textContent = mins(row.t - rows[0].t) + " in";
    for (const c of CHANNELS) {
      const cell = wrap.querySelector(`[data-ch="${c.key}"] .rp-v`);
      if (!cell) continue;
      const v = row[c.key];
      cell.textContent = v == null ? "—"
        : (Math.abs(v) >= 100 ? Math.round(v) : Number(v).toFixed(1));
    }
    drawChart();
  }

  function drawChart() {
    const canvas = wrap.querySelector(".rp-canvas");
    if (!canvas || !rows.length) return;
    const channels = picked.map((k, i) => {
      const c = CHANNELS.find((x) => x.key === k);
      // scope() reads each channel through `get`, because the data lab hands it
      // converted values rather than raw ones (data.js does the unit maths in
      // its own accessor). Replay stores display units in the row already, so
      // the accessor is a plain lookup -- but it has to be here, or scope()
      // throws on ch.get and the canvas stays blank while the readout tiles
      // below it keep updating, which is why this looked half-working.
      return { key: k, label: c ? c.label : k, tint: PALETTE[i % PALETTE.length],
               get: (r) => r[k] };
    });
    scope(canvas, { rows, channels, height: 260 });
    // The cursor line, drawn over the scope rather than by it, so scrubbing
    // does not mean re-rendering every trace.
    //
    // fit() inside scope() has already applied setTransform(dpr,...), so this
    // context measures in CSS pixels. Scaling by the device ratio a second time
    // put the cursor at twice its true offset on any HiDPI screen -- off the
    // right edge for anything past halfway through the drive.
    const ctx = canvas.getContext("2d");
    const frac = rows.length > 1 ? cursor / (rows.length - 1) : 0;
    const x = 4 + frac * (canvas.clientWidth - 8);
    ctx.save();
    // A theme token rather than white: in the night-red look a white line is
    // the brightest thing on a screen deliberately kept dark.
    ctx.strokeStyle = getComputedStyle(document.documentElement)
      .getPropertyValue("--ink").trim() || "#E7F0EE";
    ctx.globalAlpha = 0.55;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.clientHeight);
    ctx.stroke();
    ctx.restore();
  }

  function draw() {
    while (wrap.firstChild) wrap.removeChild(wrap.firstChild);

    const ex = explain(h, "replay");
    if (ex) wrap.appendChild(ex);

    // ---- pick a span ----
    wrap.appendChild(h("section.card",
      h("div.eyebrow", "Recorded"),
      h("div.title", "Replay a drive"),
      spans.length
        ? h("div.rp-spans", ...spans.slice(0, 12).map((s) =>
            h("button.rp-span" + (chosen && chosen.t0 === s.t0 ? ".on" : ""), {
              onclick: () => open(s),
            }, h("div.rp-span-l", s.label), h("div.rp-span-d", s.detail))))
        : h("p.lede", "No drives recorded yet. The daemon logs samples whenever "
            + "it is connected, and a drive is segmented automatically once you "
            + "move and then stop.")));

    if (!chosen) return;

    if (!rows.length) {
      wrap.appendChild(h("section.card", h("p.lede", "Loading…")));
      return;
    }

    // ---- transport ----
    const slider = h("input.rp-slider", {
      type: "range", min: "0", max: String(rows.length - 1),
      value: String(cursor), "aria-label": "Position in the recording",
      oninput: (e) => { stop(); cursor = Number(e.target.value); paintCursor(); },
    });

    wrap.appendChild(h("section.card",
      h("div.row.wrapline", { style: { gap: "12px", alignItems: "center" } },
        h("button.btn.primary.rp-play", {
          onclick: () => { playing ? stop() : play(); draw(); },
        }, playing ? "Pause" : "Play"),
        h("div.seg",
          ...[1, 4, 16].map((r) => h("button.btn.sm" + (rate === r ? ".on" : ""), {
            onclick: () => { rate = r; if (playing) play(); else draw(); },
          }, r + "×"))),
        h("div", { style: { flex: "1" } }),
        h("div.rp-time",
          h("span.rp-at", clockOf(rows[cursor].t)),
          h("span.rp-elapsed", mins(rows[cursor].t - rows[0].t) + " in")),
        h("button.btn", {
          onclick: () => {
            // A real download, not a blob built in the page: the server
            // already streams this and knows how to name the file.
            window.location.href =
              `/export.csv?from=${chosen.t0}&to=${chosen.t1}`;
          },
        }, "Export CSV")),
      slider,
      h("canvas.rp-canvas", { height: "260" })));

    // ---- values at the cursor ----
    wrap.appendChild(h("section.card",
      h("div.eyebrow", "At this moment"),
      h("div.rp-grid", ...CHANNELS.map((c) => {
        const on = picked.includes(c.key);
        return h("button.rp-cell" + (on ? ".on" : ""), {
          data: { ch: c.key },
          title: on ? "Hide from the chart" : "Show on the chart",
          onclick: () => {
            picked = on ? picked.filter((k) => k !== c.key)
                        : [...picked, c.key].slice(-6);
            draw();
          },
        },
          h("div.rp-k", c.label),
          h("div.rp-v", "—"),
          h("div.rp-u", c.unit));
      }))));

    paintCursor();
  }

  draw();
  loadSpans();

  return () => stop();
}
