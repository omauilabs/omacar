// Cabin → Child seat: is the seat occupied, why OmaCar thinks so, and the
// alert's settings (lib/childalert.py, doc/child-alert.md).
//
// "Child aboard" is one tap and stays available while moving: it only ever
// makes the alert MORE likely. "Car seat is empty" and every setting are
// parked-only -- greyed while moving, never hidden -- because each of them can
// make it less likely, and the server refuses the settings while moving too.

import { h, icon, store, temp, toast, withToken } from "../core.js";
import { BABY, ROLE_WORD, seatView, ask } from "../childalert.js";

const ON_OFF = [[true, "On"], [false, "Off"]];

export default function childAlertView(root) {
  const srcT = h("span.cs-src");
  const word = h("div.cs-word"), line = h("p.cs-line");
  const camB = h("b"), camK = h("span");
  const cam = h("div.cs-cam", camK, camB);
  const aboard = h("button.cs-btn.cs-key", { type: "button", onclick: () => act("aboard") }, "Child aboard");
  const empty = h("button.cs-btn", { type: "button", onclick: () => act("clear") }, "Car seat is empty");
  const reads = h("div.cs-read");
  const hero = h("section.cs-hero",
    h("div.cs-head", h("span.cs-ico", icon(BABY, 24)), h("h1.cs-name", "Child seat"), srcT),
    h("div.cs-state", h("span.cs-k", "Right now"), word, line),
    h("div.cs-mid", reads, cam),
    h("div.cs-acts", aboard, empty));

  const lock = h("span.cs-lock", { hidden: true }, "Parked only");
  const rows = {};
  const seg = (key, opts) => {
    const track = h("div.cs-track", { role: "group" });
    const btns = opts.map(([val, label]) => {
      const b = h("button.chip", { type: "button", onclick: () => save({ [key]: val }) }, label);
      b._val = val;
      track.appendChild(b);
      return b;
    });
    return { node: track, paint(cfg, locked) {
      for (const b of btns) {
        b.setAttribute("aria-current", cfg[key] === b._val ? "page" : "false");
        b.disabled = locked;
      }
    } };
  };
  const row = (key, title, sub, control) => {
    rows[key] = control;
    return h("div.cs-row", h("div.cs-rk", h("span.cs-rt", title), h("span.cs-rs", sub)), control.node);
  };
  const heatV = h("span");
  const down = h("button", { type: "button", "aria-label": "Lower", onclick: () => stepHeat(-1) }, "−");
  const up = h("button", { type: "button", "aria-label": "Higher", onclick: () => stepHeat(1) }, "+");
  const heat = { node: h("div.cs-step", down, heatV, up), paint(cfg, locked) {
    heatV.textContent = temp(cfg.heat_c);
    down.disabled = locked || cfg.heat_c <= 25;
    up.disabled = locked || cfg.heat_c >= 40;
  } };
  const set = h("section.cs-set",
    h("div.cs-set-h", h("span.cs-set-t", "Settings"), lock),
    row("enabled", "Alert when the car is switched off", "With the car seat occupied", seg("enabled", ON_OFF)),
    row("chime", "Chime after a minute", "The only sound OmaCar makes on its own", seg("chime", ON_OFF)),
    row("heat_c", "Heat warning", "More urgent above this, or rising 3° in 10 min", heat),
    row("camera", "Camera facing the seat", "Watched for a face; none means Child aboard only",
        seg("camera", [[null, "None"], ...["front", "rear", "cabin"].map((r) => [r, ROLE_WORD[r].replace(" camera", "")])])),
    h("div.cs-ladder",
      h("span.cs-set-t", "When the car is switched off with the seat occupied"),
      ...[["0 s", "The question fills the tablet, which will not sleep until you answer"],
          ["30 s", "An alert in the outbox for your phone, again every 10 min"],
          ["1 min", "The chime, every 20 s, if it is on"],
          ["Hot", "An urgent alert, and again for every 5° more"]].map(([k, v]) =>
        h("div.cs-step-row", h("span.cs-when", k), h("span.cs-what", v)))));

  const fact = (k, v, tone) => h("div.cs-fact", { data: { tone } }, h("span.cs-fk", k), h("span.cs-fv", v));
  const facts = h("section.cs-facts",
    fact("Checked by tests", "The latch, the alert's timing, the heat escalation, and that the tablet will not sleep during an alert.", "ok"),
    fact("Not yet verified", "Seeing a sleeping child, face turned, in a dark car. The drive's latch is what covers that.", "warn"),
    fact("Your phone", "Alerts are queued in OmaCar's outbox. Delivery to a phone needs omarchy-mobile, which is not built yet.", "warn"));

  root.appendChild(h("div.cs", hero, set, facts));

  let last = null;
  async function act(what) {
    await ask(what);
    toast(what === "aboard" ? "Child aboard: the alert is armed" : "Car seat marked empty", "ok");
    tick(true);
  }
  async function save(change) {
    try {
      const r = await fetch(withToken("/api/child-alert/config"), { method: "POST", cache: "no-store",
        body: JSON.stringify(change) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) toast(body.error || "Not saved", "bad");
    } catch { toast("Not saved: no answer from the server", "bad"); }
    tick(true);
  }
  function stepHeat(d) {
    const c = (last && last.config && last.config.heat_c) || 30;
    save({ heat_c: Math.max(25, Math.min(40, c + d)) });
  }

  function paint(pub) {
    last = pub;
    const v = seatView(pub, { locked: !!store.lockedAsMoving });
    hero.dataset.tone = v.tone;
    reads.replaceChildren(...v.readouts.map((r) => h("div.cs-r", h("span.cs-rk2", r.k), h("span.cs-rv", r.v))));
    word.textContent = v.word;
    line.textContent = v.line;
    srcT.textContent = v.source;
    srcT.dataset.tone = v.sourceTone;
    camK.textContent = `${v.camera.k}:`;
    camB.textContent = v.camera.v;
    cam.dataset.tone = v.camera.tone;
    aboard.hidden = !v.canAboard;
    empty.hidden = v.state !== "occupied";
    empty.disabled = !v.canClear;
    lock.hidden = !v.locked;
    const cfg = Object.assign({ enabled: true, chime: true, heat_c: 30, camera: null }, v.config);
    const off = !pub || !v.canSet || !!pub.demo;
    for (const r of Object.values(rows)) r.paint(cfg, off);
  }

  let timer = null, gone = false;
  async function tick(once) {
    try {
      const r = await fetch(withToken("/api/child-alert"), { cache: "no-store" });
      paint(r.ok ? await r.json() : null);
    } catch { paint(null); }
    if (!once && !gone) timer = setTimeout(() => tick(), 2000);
  }
  tick();
  return () => { gone = true; clearTimeout(timer); };
}
