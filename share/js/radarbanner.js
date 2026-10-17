// The radar banner: a slim strip across the top of the stage, on every screen,
// while the detector has an alert -- and for three seconds after the last.
//
// NEVER MODAL. It takes no focus, sits over the stage only (not the top bar,
// not the dock), lets every tap outside its own Mute key fall through to the
// screen under it, and makes NO SOUND: the V1 beeps for itself, and a child
// sleeps near the tablet. Laser is the bad tone, Ka the warn tone, K/X the
// quieter info tone; the band's name is written beside the colour every time.
//
// It hides itself where the alert is already the whole screen: on the Radar
// view, and on Home while Home has a radar card. Elsewhere it is the one thing
// that says a radar is near.
//
// Loaded by app.html as its own module (like awake.js), so no view knows it
// exists.

import { h } from "./core.js";
import { radarFeed, radarView, bannerPhase, bannerHides, HOLD_S } from "./radar.js";
import { strengthBar, arrowGlyph, muteKey, muteController } from "./radarui.js";

function start() {
  const stage = document.getElementById("stage");
  if (!stage) return;
  const band = h("span.rb-band");
  const freq = h("span.rb-freq");
  const capK = h("span.rb-cap");
  const arrows = arrowGlyph(34);
  const bar = strengthBar();
  const others = h("span.rb-others");
  const mute = muteKey(() => ctl.tap());
  const node = h("div.rd-banner", { role: "status", "aria-live": "polite", hidden: true },
    h("span.rb-lamp", { "aria-hidden": "true" }),
    h("div.rb-id", capK, h("div.rb-head", band, freq)),
    arrows.el, bar.el, others, mute.el);
  document.body.appendChild(node);

  let last = null, lastActive = null, shown = null;
  const ctl = muteController(radarFeed, () => last, () => paint());

  // Placed over the stage's top edge, and re-placed whenever the stage moves
  // (a segment row appearing under a tab shifts it).
  function place() {
    const r = stage.getBoundingClientRect();
    node.style.top = `${Math.round(r.top + 8)}px`;
    node.style.left = `${Math.round(r.left + 26)}px`;
    node.style.right = `${Math.round(innerWidth - r.right + 26)}px`;
  }
  if (globalThis.ResizeObserver) new ResizeObserver(place).observe(stage);
  addEventListener("resize", place);

  function paint() {
    const now = Date.now();
    const v = radarView(last, { pending: ctl.pending });
    const active = v.state === "alert";
    if (active) { lastActive = now; shown = v; }
    const homeHasCard = !!document.querySelector('.home-grid [data-card="radar"]');
    const redundant = bannerHides(location.hash, homeHasCard);
    const phase = redundant ? "off" : bannerPhase(active, lastActive === null ? null : lastActive / 1000, now / 1000, HOLD_S);
    if (phase === "off") { node.hidden = true; node.dataset.phase = "off"; return; }
    const s = shown;
    if (!s || !s.pri) { node.hidden = true; return; }
    if (node.hidden) { place(); node.hidden = false; }
    node.dataset.phase = phase;
    node.dataset.tone = s.pri.tone;
    const p = s.pri;
    band.textContent = p.band === "Photo" ? "Photo" : p.band;
    freq.textContent = p.band === "Photo" ? p.head.replace(/^Photo ?/, "") : p.freq;
    capK.textContent = p.dirWord ? `Radar · ${p.dirWord}` : "Radar";
    arrows.set(p.dir, p.tone);
    bar.set(p.strength, p.tone);
    others.textContent = s.others ? `+${s.others}` : "";
    others.hidden = !s.others;
    mute.set(v.muted, v.canMute && active);
  }

  radarFeed.subscribe((st) => { last = st; ctl.settle(st); paint(); });
  // The fade is a clock, not a poll: once the alerts stop, the feed goes quiet
  // and only this timer takes the banner away.
  setInterval(() => { if (!node.hidden) paint(); }, 500);
  addEventListener("hashchange", paint);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
