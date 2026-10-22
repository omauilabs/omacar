// Settings > Accessibility: the rows, drawn with the sheet's own row() so they
// are the same 56 px whole-width targets as everything else in it.
//
// With OmaAccess installed, every row changes OmaAccess (lib/access.py runs
// `omaaccess set`), so the desktop, the panel and the dashboard never disagree.
// Without it, they are OmaCar's own. The heading says which.
//
// Colour filters and the magnifier are not offered here: OmaAccess draws them
// under the whole screen, and its own panel is where they are set.

import { h } from "./core.js";
import { access, setAccess, SCALE_STEPS } from "./a11y.js";

const TOGGLES = [
  ["contrast", "High contrast", "Brighter text, solid panels and clear edges."],
  ["still", "Reduce motion", "Nothing slides, fades or pulses."],
  ["dyslexia", "Dyslexia-friendly font", "OpenDyslexic, with more space between letters and lines."],
  ["big", "Larger touch targets", "Every button grows to at least 64 px."],
  ["flash", "Flash for sounds", "The screen's edge glows when OmaCar makes a sound."],
  ["captions", "Captions for sounds", "Every chime, alarm and spoken line also in words."],
  ["speak", "Read alerts aloud", "In OmaAccess's voice. Never overnight with the car off, except safety alerts."],
];

export function accessSettingsBlock({ row, redraw, toast }) {
  const s = access.settings;
  const frag = document.createDocumentFragment();
  const from = access.source === "omaaccess"
    ? "Following OmaAccess. Changes here change it everywhere on the tablet."
    : "OmaAccess is not installed, so these are OmaCar's own.";
  frag.appendChild(h("h3.sheet-h", { id: "a11y-h" }, "Accessibility"));
  frag.appendChild(h("p.a11y-src.sheet-note", from));

  const change = async (name, value, said) => {
    try {
      await setAccess(name, value);
      redraw();
      toast(said);
    } catch (e) {
      toast("Could not change it: " + (e.message || e), "bad");
    }
  };

  const pct = Math.round((Number(s.scale) || 1) * 100);
  frag.appendChild(row("Text size", "On top of the size above. 100% is as designed.", `${pct}%`, () => {
    const i = SCALE_STEPS.findIndex((v) => v >= (Number(s.scale) || 1) - 0.001);
    const next = SCALE_STEPS[(i + 1) % SCALE_STEPS.length];
    change("scale", next, `Text size ${Math.round(next * 100)}%.`);
  }));
  for (const [name, label, note] of TOGGLES) {
    const on = !!s[name];
    frag.appendChild(row(label, note, on ? "On" : "Off",
      () => change(name, !on, `${label} ${on ? "off" : "on"}.`), on));
  }
  if (s.reader) {
    frag.appendChild(h("p.sheet-note", "The screen reader is on. OmaCar's alerts reach it through the page, so OmaCar does not speak over it."));
  }
  return frag;
}
