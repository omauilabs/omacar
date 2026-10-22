import { eq } from "./assert.js";
import { attrsFor, wordsFor, flashAllowed, FLASH_GAP, SCALE_STEPS } from "../js/a11y.js";

// share/js/a11y.js: OmaAccess's settings onto <html>, and every sound in words.
const base = { scale: 1, contrast: false, still: false, dyslexia: false, big: false,
  flash: false, captions: false, reader: false, speak: false, filter: "none", lang: "auto" };
const s = (over) => Object.assign({}, base, over);

export default [
  ["nothing on: no data-a11y attribute is set, and the page is English", () => {
    const a = attrsFor(s({}));
    eq([a["data-a11y-contrast"], a["data-a11y-dyslexia"], a["data-a11y-big"], a["data-a11y-filter"], a.lang],
       [null, null, null, null, "en"]);
  }],
  ["each setting turns on its own attribute", () => {
    const a = attrsFor(s({ contrast: true, still: true, dyslexia: true, big: true }));
    eq([a["data-a11y-contrast"], a["data-a11y-still"], a["data-a11y-dyslexia"], a["data-a11y-big"]],
       ["", "", "", ""]);
  }],
  ["a colour filter is named, so charts can pick the matching palette", () =>
    eq(attrsFor(s({ filter: "deutan" }))["data-a11y-filter"], "deutan")],
  ["Spanish from OmaAccess sets lang=es; anything else stays en", () =>
    eq([attrsFor(s({ lang: "es" })).lang, attrsFor(s({ lang: "auto" })).lang], ["es", "en"])],
  ["every sound has words: chime, alarm and the drowsy lines", () =>
    eq(wordsFor([{ kind: "chime" }, { kind: "voice", clip: "l1" }, { kind: "swell" }]),
       ["Chime", "“You seem tired. Plan a break soon.”"])],
  ["Level 3 says pull over, once, however many cues carry it", () =>
    eq(wordsFor([{ kind: "duck" }, { kind: "alarm", hold: true }, { kind: "voice", clip: "l3" }, { kind: "alarm" }]),
       ["Alarm", "“Pull over now.”"])],
  ["music moves (swell, duck, fade) are not sounds to caption", () =>
    eq(wordsFor([{ kind: "swell" }, { kind: "duck" }, { kind: "fade" }]), [])],
  ["the flash never strobes: one per FLASH_GAP, and 2 s is under 3 a second", () => {
    eq([flashAllowed(1000, 0), flashAllowed(2000, 0), flashAllowed(5000, 3500)], [false, true, false]);
    eq(FLASH_GAP >= 1000 / 3, true);
  }],
  ["text size steps run 100% to 200%, rising", () =>
    eq([SCALE_STEPS[0], SCALE_STEPS[SCALE_STEPS.length - 1],
        SCALE_STEPS.every((v, i) => i === 0 || v > SCALE_STEPS[i - 1])], [1, 2, true])],
];
