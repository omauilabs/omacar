// Now Playing: Omarchy Radio's own screen in the meetup demo.
//
// Routed at #nowplaying (register() below adds it to the demo's extra views),
// and reached from Home's phone card. It is a plain screen: the station set in
// type, the song large with its artist under it, a bar you can scrub, the
// three transport buttons at a size a thumb can hit on a moving car's dash,
// and the seven songs with the playing one marked. There is no artwork on
// purpose; the station has none, and a placeholder cover would be the one
// thing on the screen that was made up.
//
// It talks to the Radio contract and nothing else (demo/js/radio.js), so the
// same song carries on when the driver leaves for CarPlay and comes back.

import { h, clear } from "../../../js/core.js";
import { getRadio, mmss, snapshot } from "../radio.js";

// Filled shapes, drawn on a 24 grid. The stroke rounds their corners.
const SOLID = {
  play: '<path d="M8 4.8v14.4L19.6 12z"/>',
  pause: '<path d="M7 5h3.4v14H7zM13.6 5H17v14h-3.4z"/>',
  prev: '<path d="M5.4 5h2.4v14H5.4zM19 5.2v13.6L9.4 12z"/>',
  next: '<path d="M16.2 5h2.4v14h-2.4zM5 5.2v13.6L14.6 12z"/>',
};
const LINES = {
  // The playing row's marker: three bars at rest. Still, not animated: nothing
  // on this screen moves that is not a measurement (share/css/radio.css).
  bars: '<path d="M5.5 13.5v5M12 5.5v13M18.5 9.5v9"/>',
};

// An inline glyph in a span, so its size and colour come from the stylesheet
// (currentColor) and a theme change reaches it with no repaint.
export function glyph(name, size = 24) {
  const solid = SOLID[name];
  const body = solid || LINES[name];
  const attrs = solid
    ? 'fill="currentColor" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"'
    : 'fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"';
  return h("span.np-g", {
    html: `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" ${attrs}>${body}</svg>`,
  });
}

export function nowPlayingView(root, { radio = getRadio() } = {}) {
  let gone = false;
  let dragging = false;     // a finger is on the bar: the radio's clock must not fight it
  let rowsFor = -1;         // how many rows the list was built for
  // The last value written to each place, so a tick that changes nothing
  // writes nothing (and a bar under a finger is never moved by a repaint).
  const seen = {};
  const put = (key, el, value) => { if (seen[key] !== value) { seen[key] = value; el.textContent = value; } };

  // ---- the station card: type only ------------------------------------------
  const station = h("section.np-station.card", { "aria-label": "Station" },
    h("div.np-st-name", "OMARCHY RADIO"),
    h("div.np-st-tag", "every song a pull request"));

  // ---- the song ---------------------------------------------------------------
  const eyebrow = h("div.np-eyebrow");
  const title = h("h2.np-title");
  const artist = h("div.np-artist");
  const range = h("input.np-range", {
    type: "range", min: "0", max: "0", step: "1", value: "0", disabled: true,
    "aria-label": "Position in the song",
  });
  const pos = h("span.np-pos", "0:00");
  const dur = h("span.np-dur", "0:00");

  const fill = (at, of) => {
    const p = of > 0 ? Math.min(100, Math.max(0, (at / of) * 100)).toFixed(2) + "%" : "0%";
    if (seen.p !== p) { seen.p = p; range.style.setProperty("--p", p); }
  };
  // Dragging previews the time and does not seek on every pixel: a seek is a
  // Range request to the server, and only where the finger lands is asked for.
  range.addEventListener("input", () => {
    const v = Number(range.value);
    put("pos", pos, mmss(v));
    fill(v, Number(range.max));
  });
  range.addEventListener("change", () => radio.seek(Number(range.value)));
  range.addEventListener("pointerdown", () => { dragging = true; });
  for (const type of ["pointerup", "pointercancel", "blur"]) range.addEventListener(type, () => { dragging = false; });

  const prev = h("button.np-btn.np-prev", { type: "button", "aria-label": "Previous song",
    onclick: () => radio.prev() }, glyph("prev", 28));
  const play = h("button.np-btn.np-play", { type: "button", "aria-label": "Play",
    onclick: () => radio.toggle() }, glyph("play", 38));
  const next = h("button.np-btn.np-next", { type: "button", "aria-label": "Next song",
    onclick: () => radio.next() }, glyph("next", 28));

  const now = h("section.np-now.card", { "aria-label": "Now playing" },
    eyebrow,
    h("div.np-words", title, artist),
    h("div.np-scrub", range, h("div.np-times", pos, dur)),
    h("div.np-ctl", prev, play, next));

  // ---- the seven ---------------------------------------------------------------
  const list = h("ol.np-tracks");
  const count = h("span.np-count");
  const roster = h("section.np-list.card", { "aria-label": "The station's songs" },
    h("div.np-list-h", h("span", "The station"), count), list);

  function buildRows(tracks) {
    clear(list);
    tracks.forEach((t, i) => list.appendChild(h("li",
      h("button.np-track", { type: "button", onclick: () => (i === radio.index ? radio.toggle() : radio.play(i)) },
        h("span.np-tn"),
        h("span.np-tx", h("span.np-tt", t.title), h("span.np-ta", t.artist))))));
    rowsFor = tracks.length;
    seen.mark = null;
  }

  const node = h("div.np", station, now, roster, h("div.np-src", "radio.omarchy.org"));
  root.classList.add("np-root");
  root.appendChild(node);

  function paint() {
    if (gone) return;
    const s = snapshot(radio);
    if (rowsFor !== s.count) buildRows(radio.tracks);

    put("title", title, s.title || "Omarchy Radio");
    put("artist", artist, s.error || s.artist || (s.count ? "" : "Loading the station…"));
    if (seen.tone !== !!s.error) { seen.tone = !!s.error; artist.dataset.tone = s.error ? "warn" : ""; }
    put("eyebrow", eyebrow, (s.playing ? "Now playing" : s.position > 0.5 ? "Paused" : "Ready")
      + (s.count ? ` · ${s.index + 1} of ${s.count}` : ""));
    put("count", count, s.count ? `${s.count} song${s.count === 1 ? "" : "s"}` : "");

    // The length is unknown until the file's header has been read; until then
    // the bar is inert and both clocks say 0:00, never a made-up number.
    const ready = s.duration > 0;
    if (range.disabled === ready) range.disabled = !ready;
    // max before value: a value is clamped to the max it is set against.
    const max = String(ready ? Math.ceil(s.duration) : 0);
    if (range.max !== max) range.max = max;
    if (!dragging) {
      range.value = String(s.position);
      fill(s.position, s.duration);
      put("pos", pos, mmss(s.position));
    }
    put("dur", dur, mmss(s.duration));

    if (seen.playing !== s.playing) {
      seen.playing = s.playing;
      play.setAttribute("aria-label", s.playing ? "Pause" : "Play");
      play.replaceChildren(glyph(s.playing ? "pause" : "play", 38));
    }

    const mark = s.index + ":" + s.playing;
    if (seen.mark !== mark) {
      seen.mark = mark;
      list.querySelectorAll(".np-track").forEach((b, i) => {
        const current = i === s.index;
        if (current) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
        const n = b.querySelector(".np-tn");
        n.replaceChildren(current && s.playing ? glyph("bars", 22) : String(i + 1));
      });
    }
  }

  const off = radio.subscribe(paint);
  paint();
  // A failed load says so through the radio's own error line, which paint()
  // shows; here it only must not be an unhandled rejection.
  Promise.resolve(radio.load()).then(paint, paint);

  return function unmount() {
    gone = true;
    off();
    root.classList.remove("np-root");
    clear(root);
  };
}

export default nowPlayingView;

// Adds the screen to the demo's routable views (boot.js calls this).
export function register(D) {
  const view = { id: "nowplaying", label: "Now Playing", title: "Omarchy Radio", mount: nowPlayingView, fast: false };
  const at = D.extraViews.findIndex((v) => v.id === view.id);
  if (at >= 0) D.extraViews[at] = view; else D.extraViews.push(view);
}
