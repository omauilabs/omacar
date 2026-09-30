// Home's "Phone integration" card, as the owner's mockup draws it (mock 3):
// Apple CarPlay, Android Auto, and, for the meetup, a third row that is Omarchy
// Radio's mini player.
//
// It replaces the live app's phoneCard (share/js/views/home.js) through the
// demo's one door, OMACAR_DEMO.cards.phone, and has the same shape: a Home card
// is { node, paint(), destroy() }. The live card's Music and Nursery chips are
// not here on purpose: the demo's music is the radio, and there is no nursery.
//
// The CarPlay and Android Auto glyphs are neutral drawings (a rounded square
// with a play triangle; a stylised A wedge) and their names are text. Neither
// company's mark is drawn here, or fetched.

import { h, icon } from "../../../js/core.js";
import { ICONS } from "../../../js/icons.js";
import { getRadio, snapshot } from "../radio.js";
import { glyph } from "../views/nowplaying.js";

const go = (id) => { location.hash = "#" + id; };

// A tap on a card is part of editing, never a navigation, while Home's layout
// is being edited (home.js and homeedit.js mark the grid `.editing`). The
// stopPropagation is the live card's own: the card's wrapper must not also
// hear a tap that a row took.
const tap = (fn) => (e) => {
  e.stopPropagation();
  if (!e.currentTarget.closest(".editing")) fn();
};

const CARPLAY = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="currentColor" '
  + 'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M8.4 5.6v12.8L18.6 12z"/></svg>';
// An "A" with no crossbar: a wedge with a notch cut from its foot.
const ANDROID = '<svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true" fill="currentColor" '
  + 'stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M12 3.6 21 20.4h-4.6L12 12.2l-4.4 8.2H3z"/></svg>';

export function phoneCard({ onCarPlay = () => go("carplay"), onAndroidAuto = () => go("androidauto"), radio = getRadio() } = {}) {
  const chevron = () => h("span.chev", icon(ICONS.chevron, 18));

  const carplay = h("button.hc-row.pc-row.pc-carplay", { type: "button", onclick: tap(onCarPlay) },
    h("span.pc-tile.pc-tile-cp", { html: CARPLAY }),
    h("span.pc-words", "Apple CarPlay", h("span.sub", "Connect your iPhone")),
    chevron());

  const android = h("button.hc-row.pc-row.pc-android", { type: "button", onclick: tap(onAndroidAuto) },
    h("span.pc-tile.pc-tile-aa", { html: ANDROID }),
    h("span.pc-words", "Android Auto", h("span.sub", "Connect your Android")),
    chevron());

  // The radio row. Its tap opens Now Playing; the play button inside it is its
  // own target and does not (tap() stops the click before it reaches the row).
  // The row is a div holding two buttons, not a button holding a button.
  const name = h("span.pc-radio-title", "Omarchy Radio");
  const by = h("span.pc-radio-by");
  const play = h("button.pc-radio-play", { type: "button", "aria-label": "Play",
    onclick: tap(() => radio.toggle()) }, glyph("play", 20));
  const radioRow = h("div.hc-row.pc-row.pc-radio", { onclick: tap(() => go("nowplaying")) },
    h("button.pc-radio-open", { type: "button", "aria-label": "Open Now Playing" },
      h("span.pc-or", { "aria-hidden": "true" }, "OR"),
      h("span.pc-radio-text", name, by)),
    play);

  const node = h("div.card.hc.hc-phone.pc",
    h("div.hc-title", icon(ICONS.phone, 18), "Phone integration"),
    carplay, android, radioRow);

  let gone = false;
  const seen = {};
  function paint() {
    if (gone) return;
    const s = snapshot(radio);
    const title = s.title || "Omarchy Radio";
    const artist = s.artist ? " · " + s.artist : "";
    if (seen.title !== title) { seen.title = title; name.textContent = title; }
    if (seen.artist !== artist) { seen.artist = artist; by.textContent = artist; }
    if (seen.playing !== s.playing) {
      seen.playing = s.playing;
      radioRow.dataset.playing = s.playing ? "1" : "0";
      play.setAttribute("aria-label", s.playing ? "Pause" : "Play");
      play.replaceChildren(glyph(s.playing ? "pause" : "play", 20));
    }
  }

  const off = radio.subscribe(paint);
  paint();
  // So the row can show the first song's name before anything is tapped. Loading
  // plays nothing; a failure is the radio's to report, and is not a crash here.
  Promise.resolve(radio.load()).then(paint, () => {});

  return {
    node,
    paint,
    destroy() { gone = true; off(); },
  };
}

export default phoneCard;

// Sets Home's phone card (boot.js calls this). `deps.radio` is for a caller
// that has its own player; by default it is the page's one.
export function register(D, deps = {}) {
  D.cards.phone = () => phoneCard({ radio: deps.radio });
}
