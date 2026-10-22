// Navigation -> Radar: the Valentine One in full.
//
// The alert table live, the direction compass, the rolling log of the last 50
// and the facts about the link. Everything is radar.js's one feed; nothing here
// makes a sound (the V1 beeps for itself) and nothing records where a radar
// was -- the log is band, frequency, time and peak strength only.

import { h, icon } from "../core.js";
import { radarFeed, radarView, logRows, BAND_ORDER } from "../radar.js";
import { RADAR_ICON, strengthBar, arrowGlyph, compass, muteKey, muteController } from "../radarui.js";

export default function radarView_(root) {
  let last = null;
  const lamp = h("span.rd-lamp", { "aria-hidden": "true" });
  const srcT = h("span.rd-src-t");
  const src = h("span.rd-src", lamp, srcT);
  const mute = muteKey(() => ctl.tap());
  const ctl = muteController(radarFeed, () => last, () => paint());

  const comp = compass(250);
  const lamps = BAND_ORDER.map((b) => [b, h("span.rd-bl", { "data-band": b }, h("i"), b)]);
  const priBar = strengthBar();
  const priLine = h("div.rd-pl");
  const table = h("div.rd-rows");
  const tableK = h("span.rd-cap");
  const log = h("div.rd-log");
  const logK = h("span.rd-cap");
  const facts = h("dl.rd-facts");

  root.appendChild(h("div.rd-view",
    h("div.rd-vhead", icon(RADAR_ICON, 22), h("h2.rd-vtitle", "Radar detector"), src, mute.el),
    h("div.rd-vgrid",
      h("div.rd-left",
        h("section.card.rd-c-compass",
          h("span.rd-cap", "Direction"), comp.el,
          h("div.rd-bl-row", lamps.map(([, n]) => n)),
          priLine, priBar.el),
        h("section.card.rd-c-facts", h("span.rd-cap", "Connection"), facts)),
      h("div.rd-right",
        h("section.card.rd-c-table", h("div.rd-sech", h("span.rd-cap", "Alerts"), tableK), table),
        h("section.card.rd-c-log", h("div.rd-sech", h("span.rd-cap", "Last 50"), logK), log)))));

  const text = (n, s) => { if (n.textContent !== s) n.textContent = s; };

  function paint() {
    const v = radarView(last, { pending: ctl.pending });
    src.dataset.tone = v.lampTone;
    text(srcT, v.lamp);
    mute.set(v.muted, v.canMute);
    comp.set(v.rows);
    const on = new Set(v.rows.map((r) => r.band));
    for (const [b, n] of lamps) n.dataset.on = on.has(b) ? "1" : "";
    const p = v.pri;
    priBar.set(p ? p.strength : 0, p ? p.tone : "");
    text(priLine, p ? `${p.head} · ${p.dirWord}` : v.line || v.lastText);

    text(tableK, v.rows.length ? `${v.rows.length} now` : v.todayText);
    table.replaceChildren(...(v.rows.length ? v.rows.map((r) => {
      const a = arrowGlyph(42), b = strengthBar();
      a.set(r.dir, r.tone); b.set(r.strength, r.tone);
      return h("div.rd-row", { "data-tone": r.tone, "data-pri": r.priority ? "1" : "" },
        h("span.rd-rh", r.head), h("span.rd-rd", r.dirWord), a.el, b.el);
    }) : [h("div.rd-empty", v.state === "idle" ? "All quiet. Nothing in range." : v.line || "No alerts")]));

    const rows = logRows(last);
    text(logK, rows.length ? `${v.today} today` : "");
    log.replaceChildren(...(rows.length ? rows.map((r) => {
      const b = strengthBar(); b.set(r.peak, r.tone);
      return h("div.rd-lrow", { "data-tone": r.tone },
        h("span.rd-lt", r.time), h("span.rd-lh", r.head), h("span.rd-ld", r.dirWord), b.el);
    }) : [h("div.rd-empty", "Nothing logged yet")]));

    facts.replaceChildren(...v.facts.flatMap(([k, val]) => [h("dt", k), h("dd", val)]));
  }

  const off = radarFeed.subscribe((st) => { last = st; ctl.settle(st); paint(); });
  paint();
  return () => off();
}
