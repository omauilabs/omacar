// CarPlay, as the meetup demo draws it (doc/design/2026-09-30-meetup-demo.md
// §4, Task 5 of the plan). Opened from Home's Phone integration card.
//
// Drawn fresh in the platform's general style and nothing more: a dock down the
// left with the time, the signal and three recent apps; a home grid of
// rounded-square tiles; Maps with its guidance card and trip tray; Now Playing
// with the art on the left. The tiles are colours and glyphs written in
// ../projection.js, the art is the station's name in type, and the name
// "CarPlay" is in the dock's status area and nowhere else.
//
// The OmaCar tile is the car maker's tile a real head unit has: it goes back
// to OmaCar (deps.back()).

import { h, clear } from "../../../js/core.js";
import {
  withDeps, takeover, createRouter, openScreen, glyph, runClock, onLive,
  mapPane, paintTurn, turnOf, tripOf, setText, mediaButtons, bindMedia, stationArt,
  FAVOURITES, THREAD, EVENT, whenText,
} from "../projection.js";

export { openScreen };

const APPS = ["maps", "nowplaying", "phone", "messages", "podcasts", "calendar", "settings", "omacar"];
const LABEL = {
  maps: "Maps", nowplaying: "Now Playing", phone: "Phone", messages: "Messages",
  podcasts: "Podcasts", calendar: "Calendar", settings: "Settings", omacar: "OmaCar",
};
const GLYPH = { maps: "pin", nowplaying: "bars", phone: "handset", messages: "bubble", podcasts: "podmic",
                settings: "gear", omacar: "omacar" };
const DOW = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

function iconOf(id, now) {
  if (id === "calendar") {
    const d = new Date(now());
    return h("span.cp-cal", h("b.cp-cal-dow", DOW[d.getDay()]), h("b.cp-cal-num", String(d.getDate())));
  }
  return glyph(GLYPH[id]);
}

function tile(id, now, cls, onclick) {
  return h(`button.${cls}`, { data: { app: id }, "aria-label": LABEL[id], onclick },
    h("span.proj-icon", iconOf(id, now)),
    cls === "proj-app" ? h("span.proj-label", LABEL[id]) : null);
}

// A title bar: an optional back chevron, the title, an optional button.
function bar(title, { back, right } = {}) {
  return h("header.cp-bar",
    back ? h("button.cp-back", { "aria-label": "Back", onclick: back }, glyph("back")) : null,
    h("div.cp-bar-title", title),
    right || null);
}

export function carplayView(deps) {
  const d = withDeps(deps);
  return (root, { arg } = {}) => takeover(root, "cp", (el) => make(el, d, arg));
}

function make(el, d, arg) {
  const recents = ["maps", "nowplaying", "messages"];
  const stage = h("main.cp-stage");
  const recentBox = h("div.cp-recents");
  const home = h("button.cp-home", { "aria-label": "Home", onclick: () => router.go("home") }, glyph("grid"));
  el.append(
    h("aside.cp-dock",
      h("div.proj-status.cp-status",
        h("div.proj-time"),
        h("div.cp-net", glyph("signal"), h("span", "5G")),
        h("div.cp-name", "CarPlay")),
      recentBox,
      home),
    stage);

  const open = (id) => (id === "omacar" ? d.back() : router.go(id));
  const paintRecents = () => {
    clear(recentBox);
    for (const id of recents) recentBox.appendChild(tile(id, d.now, "cp-recent", () => open(id)));
  };

  // ---------------------------------------------------------------- screens
  const screens = {
    home(host) {
      host.append(h("div.cp-homescr",
        h("div.proj-grid.cp-grid", APPS.map((id) => tile(id, d.now, "proj-app", () => open(id)))),
        h("div.cp-dots", h("i.on"), h("i"))));
    },

    maps(host) {
      const pane = mapPane(d, "carplay");
      const card = h("div.proj-turn.cp-turn",
        h("div.cp-turn-top", h("span.proj-turn-ic"), h("span.proj-turn-dist")),
        h("div.proj-turn-street"));
      const col = (k) => h(`div.cp-eta-col.k-${k}`, h("b"), h("span"));
      const eta = h("div.proj-eta.cp-eta", col("arrive"), col("left"), col("miles"),
        h("button.cp-end", { onclick: () => router.go("home") }, "End"));
      host.append(h("div.cp-maps", pane.pane, card, eta,
        h("div.cp-mapbtns",
          h("button", { "aria-label": "Voice" }, glyph("speaker")),
          h("button", { "aria-label": "Zoom in" }, glyph("plus")),
          h("button", { "aria-label": "Zoom out" }, glyph("minus")))));
      pane.start();
      const q = (s) => eta.querySelector(s);
      const off = onLive((s) => {
        paintTurn(card, turnOf(s));
        const t = tripOf(s && s.demo, d.now());
        eta.hidden = !t;
        if (t) {
          setText(q(".k-arrive b"), t.arrive); setText(q(".k-arrive span"), "arrival");
          setText(q(".k-left b"), t.left.n); setText(q(".k-left span"), t.left.u);
          setText(q(".k-miles b"), t.miles); setText(q(".k-miles span"), "mi");
        }
        pane.paint();
      });
      return () => { off(); pane.stop(); };
    },

    nowplaying(host) {
      const box = h("div.cp-np",
        bar("Now Playing", {
          back: () => router.go("home"),
          right: h("button.cp-bar-btn", { "aria-label": "Up Next", onclick: () => router.go("queue") }, glyph("list")),
        }),
        h("div.cp-np-body",
          stationArt(),
          h("div.cp-np-info",
            h("div.pj-title"), h("div.pj-artist"), h("div.pj-album", "Omarchy Radio"),
            h("div.pj-bar", h("div.pj-fill")),
            h("div.pj-times", h("span.pj-elapsed"), h("span.pj-remain")),
            h("div.pj-ctl", mediaButtons(d.radio, { prev: "skip-back", next: "skip-fwd" })))));
      host.append(box);
      return bindMedia(box, d.radio);
    },

    queue(host) {
      const tracks = (d.radio && d.radio.tracks) || [];
      const box = h("div.cp-list",
        bar("Omarchy Radio", { back: () => router.go("nowplaying") }),
        h("div.cp-rows", tracks.map((t, i) =>
          h("button.cp-row.pj-row", { data: { i }, onclick: () => { if (d.radio) d.radio.play(i); router.go("nowplaying"); } },
            h("span.cp-qn", String(i + 1)),
            h("div.cp-row-txt", h("b", t.title), h("small", t.artist)),
            h("span.cp-qeq", glyph("bars"))))));
      host.append(box);
      return bindMedia(box, d.radio);
    },

    phone(host) {
      host.append(h("div.cp-list",
        h("header.cp-bar.cp-bar-tabs",
          h("div.cp-tabs", ["Favorites", "Recents", "Contacts", "Keypad", "Voicemail"].map((t, i) =>
            h(i ? "span" : "span.on", t)))),
        h("div.cp-rows", FAVOURITES.map((f) =>
          h("div.cp-row",
            h("span.cp-av", f.glyph ? glyph(f.glyph) : f.initial),
            h("div.cp-row-txt", h("b", f.name), h("small", f.kind)),
            h("span.cp-info", glyph("info")))))));
    },

    messages(host) {
      host.append(h("div.cp-list",
        bar("Messages", { right: h("button.cp-bar-btn", { "aria-label": "New message" }, glyph("compose")) }),
        h("div.cp-rows",
          h("button.cp-row.cp-msg", { onclick: () => router.go("thread") },
            h("span.cp-unread"),
            h("span.cp-av", THREAD.initial),
            h("div.cp-row-txt", h("b", THREAD.from), h("small", THREAD.text)),
            h("span.cp-when", whenText(d.now() - THREAD.minsAgo * 60000))))));
    },

    thread(host) {
      host.append(h("div.cp-list",
        bar(THREAD.from, { back: () => router.go("messages") }),
        h("div.cp-thread",
          h("div.cp-stamp", `Today ${whenText(d.now() - THREAD.minsAgo * 60000)}`),
          h("div.cp-bubble", THREAD.text))));
    },

    podcasts(host) {
      host.append(h("div.cp-list",
        h("header.cp-bar.cp-bar-tabs",
          h("div.cp-tabs", h("span.on", "Up Next"), h("span", "Library"), h("span", "Browse"))),
        h("div.cp-empty", glyph("podmic"), h("b", "Nothing Up Next"),
          h("small", "Episodes of the shows you follow appear here."))));
    },

    calendar(host) {
      const now = new Date(d.now());
      host.append(h("div.cp-list",
        bar("Today"),
        h("div.cp-caldate", now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })),
        h("div.cp-rows",
          h("button.cp-row.cp-event", { onclick: () => router.go("maps") },
            h("span.cp-evbar"),
            h("div.cp-row-txt", h("b", EVENT.title), h("small", `${EVENT.when} · ${EVENT.where}`)),
            h("span.cp-chev", glyph("chev"))))));
    },

    settings(host) {
      const sw = (on) => h(`span.cp-switch${on ? ".on" : ""}`, h("i"));
      const row = (name, right) => h("div.cp-row", h("div.cp-row-txt", h("b", name)), right);
      host.append(h("div.cp-list",
        bar("Settings"),
        h("div.cp-rows.cp-group",
          row("Driving Focus", h("span.cp-val", "On")),
          row("Appearance", h("span.cp-val", "Always Dark")),
          row("Show Album Art", sw(true)),
          row("Suggestions", sw(true)),
          row("Announce Messages", sw(false)))));
    },
  };

  const router = createRouter(stage, screens, {
    fallback: "home",
    onChange(id) {
      el.dataset.screen = id;
      home.classList.toggle("on", id === "home");
      // The dock's recents, most recent first, as the real one keeps them.
      if (APPS.includes(id) && id !== "omacar") {
        const i = recents.indexOf(id);
        if (i !== 0) {
          if (i > 0) recents.splice(i, 1); else recents.pop();
          recents.unshift(id);
        }
      }
      paintRecents();
      for (const b of recentBox.children) b.classList.toggle("on", b.dataset.app === id);
    },
  });
  // The first screen before the clock: a screen that throws here leaves no
  // interval behind, and takeover() puts the bars back.
  try { router.go(arg || "home"); } catch (e) { router.stop(); throw e; }
  const stopClock = runClock(el, d.now);
  return { go: (id) => router.go(id), stop() { router.stop(); stopClock(); } };
}

// The demo's routable screen (#carplay, or #carplay/maps and so on).
export function register(D, deps) {
  D.extraViews.push({ id: "carplay", label: "CarPlay", title: "Apple CarPlay",
                      mount: carplayView(deps), fast: true });
}

export default carplayView;
