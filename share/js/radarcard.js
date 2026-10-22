// Home's radar card: the Valentine One on the dash (lib/v1.py).
//
// CALM WHEN NOTHING IS THERE. Idle it says how many alerts there were today,
// when the last one was, and where its link comes from -- and nothing moves.
// ACTIVE, it is the priority alert and little else: the band and frequency as
// large as the card allows, the V1's own arrows, its eight bars, and how many
// other bogeys there are. Mute is one lit key; no sound ever comes from here.
//
// Drawn INTO Home's slot when given one, like the other cards, so the grid
// placement Home put on that node is kept. The data is radar.js's one feed.

import { h, icon } from "./core.js";
import { radarFeed, radarView } from "./radar.js";
import { RADAR_ICON, strengthBar, arrowGlyph, muteKey, muteController } from "./radarui.js";

const set = (n, s) => { if (n.textContent !== s) n.textContent = s; };

export function radarCard(host = null) {
  const lamp = h("span.rd-lamp", { "aria-hidden": "true" });
  const srcT = h("span.rd-src-t");
  const src = h("span.rd-src", lamp, srcT);

  // idle
  const todayN = h("span.rd-today-n.display-num");
  const todayK = h("span.rd-today-k", "alerts today");
  const lastL = h("span.rd-line");
  const modeL = h("span.rd-line.rd-dim");
  const idle = h("div.rd-idle",
    h("div.rd-today", h("span.rd-cap", "Today"), todayN, todayK),
    h("div.rd-idle-r", lastL, modeL));

  // active
  const band = h("span.rd-band");
  const freq = h("span.rd-freq");
  const unit = h("span.rd-unit", "GHz");
  const dirWord = h("span.rd-cap");
  const arrows = arrowGlyph(76);
  const bar = strengthBar();
  const others = h("span.rd-others");
  const active = h("div.rd-active",
    h("div.rd-pri", h("span.rd-cap", "Priority"),
      h("div.rd-head", band, h("span.rd-fq", freq, unit))),
    h("div.rd-dir", arrows.el, dirWord),
    h("div.rd-sig", h("span.rd-cap", "Signal"), bar.el, others));

  const lampLine = h("span.rd-line.rd-foot-l");
  const mute = muteKey(() => ctl.tap());

  const node = host || h("div.card.hc");
  node.classList.add("hc-radar");
  node.append(
    h("div.rd-top", icon(RADAR_ICON, 18), h("span.rd-title", "Radar"), src),
    h("div.rd-body", idle, active),
    h("div.rd-foot", lampLine, mute.el));

  let last = null;
  const ctl = muteController(radarFeed, () => last, () => paint());

  function paint() {
    const v = radarView(last, { pending: ctl.pending });
    node.dataset.state = v.state;
    node.dataset.tone = v.pri ? v.pri.tone : "";
    src.dataset.tone = v.lampTone;
    set(srcT, v.lamp);
    idle.hidden = v.state === "alert";
    active.hidden = v.state !== "alert";
    if (v.state === "alert") {
      const p = v.pri;
      set(band, p.band === "Photo" ? "Photo" : p.band);
      set(freq, p.band === "Photo" ? (p.head.replace(/^Photo ?/, "")) : p.freq);
      unit.hidden = !p.freq;
      set(dirWord, p.dirWord);
      arrows.set(p.dir, p.tone);
      bar.set(p.strength, p.tone);
      set(others, v.others ? `+${v.others} more bogey${v.others > 1 ? "s" : ""}` : "Only one");
    } else {
      set(todayN, String(v.today));
      set(todayK, v.today === 1 ? "alert" : "alerts");
      set(lastL, v.state === "absent" ? v.line : v.state === "offline" ? v.line : v.lastText);
      set(modeL, v.mode || "");
    }
    set(lampLine, v.state === "alert" ? v.todayText : v.state === "idle" ? v.line : "");
    mute.set(v.muted, v.canMute);
  }

  const off = radarFeed.subscribe((st) => { last = st; ctl.settle(st); paint(); });
  paint();
  return { node, paint, destroy() { off(); } };
}
