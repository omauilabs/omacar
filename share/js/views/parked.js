// Navigation -> Parked: where the car was left.
//
// The stills large (rear and front, as the recorder saw them when the car was
// turned off), when, the level if the pressure could say, and the place with a
// Copy key for a maps app. The last ten parks are down the side; a tap shows
// that one. Nothing here records anything: lib/parkspot.py did, on the tablet.

import { h, icon, toast } from "../core.js";
import { parkFeed, parkView, copyText, PARK_ICON, COPY_ICON } from "../parkspot.js";

export default function parkedView(root) {
  let last = null, pick = null, sig = "", copyWhat = null;
  const lamp = h("span.ps-lamp", { "aria-hidden": "true" });
  const srcT = h("span.ps-src-t");
  const src = h("span.ps-src", lamp, srcT);
  const stills = h("div.ps-stills");
  const when = h("div.ps-big"), ago = h("div.ps-sub");
  const lvBig = h("div.ps-big"), lvSub = h("div.ps-sub");
  const place = h("div.ps-place"), acc = h("div.ps-sub");
  const copy = h("button.ps-copy", { type: "button" }, icon(COPY_ICON, 18), h("span", "Copy for maps"));
  const list = h("div.ps-list");
  const emptyT = h("div.ps-empty-t"), emptyS = h("div.ps-empty-s");
  const body = h("div.ps-main", stills,
    h("div.ps-facts",
      h("section.card.ps-fact", h("span.ps-cap", "Parked"), when, ago),
      h("section.card.ps-fact", h("span.ps-cap", "Level"), lvBig, lvSub),
      h("section.card.ps-fact.ps-pl", h("span.ps-cap", "Place"), place, acc, copy)));
  const view = h("div.ps", { "data-state": "none" },
    h("div.ps-head", icon(PARK_ICON, 22), h("h2.ps-title", "Where I parked"), src),
    h("div.ps-grid", body,
      h("aside.ps-side", h("span.ps-cap", "Last parks"), list)),
    h("div.ps-empty", emptyT, emptyS));
  root.appendChild(view);

  copy.onclick = async () => {
    if (!copyWhat) return;
    toast((await copyText(copyWhat)) ? "Coordinates copied" : "Could not copy", "");
  };

  const text = (n, s) => { if (n.textContent !== s) n.textContent = s; };

  function paint() {
    const v = parkView(last, { pick });
    view.dataset.state = v.state;
    src.dataset.tone = v.sourceTone;
    text(srcT, v.source);
    if (v.state === "none") {
      text(emptyT, v.line); text(emptyS, v.sub);
      return;
    }
    const c = v.current;
    text(when, c.when); text(ago, c.ago);
    text(lvBig, c.level ? c.level.label : "—");
    text(lvSub, c.level
      ? `level ≈ ${c.level.label} (estimate)` + (c.level.detail ? ` · ${c.level.detail}` : "")
      : "Not known: no pressure reading or no street reference");
    text(place, c.fix.text);
    place.dataset.none = c.fix.has ? "" : "1";
    text(acc, c.fix.accuracy);
    copyWhat = c.fix.copy;
    copy.hidden = !c.fix.has;
    // The stills are rebuilt only when the park shown changes, so a poll never
    // makes a picture flicker.
    const s = `${c.id}${c.simulated ? "s" : ""}:${c.stills.map((x) => x.url || "").join("|")}`;
    if (s !== sig) {
      sig = s;
      stills.replaceChildren(...c.stills.map((x) => h("figure.ps-still", { "data-have": x.url ? "1" : "" },
        x.url ? h("img", { src: x.url, alt: `${x.label} camera when parked`, draggable: "false" })
              : h("div.ps-nostill", x.note),
        h("figcaption", h("span", x.label), h("span", c.simulated ? `Simulated · ${c.clock}` : c.clock)))));
    }
    list.replaceChildren(...v.history.map((r) => {
      const b = h("button.ps-row", { type: "button", "data-on": r.id === c.id ? "1" : "" },
        h("span.ps-rt", r.when),
        h("span.ps-rp", r.fix.has ? r.fix.text : "No GPS fix"),
        h("span.ps-rl", r.level ? r.level.label : ""),
        r.simulated ? h("span.ps-rs", "Simulated") : "");
      b.onclick = () => { pick = r.id; paint(); };
      return b;
    }));
  }

  const off = parkFeed.subscribe((st) => { last = st; paint(); });
  paint();
  return () => off();
}
