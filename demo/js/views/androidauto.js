// Android Auto, as the meetup demo draws it (doc/design/2026-09-30-meetup-demo.md
// §4, Task 5 of the plan). Opened from Home's Phone integration card.
//
// Drawn fresh in the platform's general style and nothing more: a rail along
// the bottom with the launcher, three recent apps and the time; a split
// dashboard with the map on the left and the media and a suggestion stacked on
// the right; an app launcher of round icons; Maps with a green guidance card.
// The icons are colours and glyphs written in ../projection.js, the art is the
// station's name in type, and the name "Android Auto" is in the rail's status
// area and nowhere else.
//
// The launcher's OmaCar icon is the way back to the car's own screen that a
// real head unit offers: it calls deps.back().

import { h, clear } from "../../../js/core.js";
import {
  withDeps, takeover, createRouter, openScreen, glyph, runClock, onLive,
  mapPane, paintTurn, turnOf, tripOf, setText, mediaButtons, bindMedia, stationArt,
  FAVOURITES, THREAD, EVENT, whenText,
} from "../projection.js";

export { openScreen };

const APPS = ["maps", "nowplaying", "phone", "messages", "podcasts", "calendar", "settings", "omacar"];
const LABEL = {
  maps: "Maps", nowplaying: "Radio", phone: "Phone", messages: "Messages",
  podcasts: "Podcasts", calendar: "Calendar", settings: "Settings", omacar: "OmaCar",
};
const GLYPH = { maps: "pin", nowplaying: "note", phone: "handset", messages: "bubble",
                podcasts: "podcast", calendar: "calendar", settings: "gear", omacar: "omacar" };

function icon(id, cls, onclick) {
  return h(`button.${cls}`, { data: { app: id }, "aria-label": LABEL[id], onclick },
    h("span.proj-icon", glyph(GLYPH[id])),
    cls === "proj-app" ? h("span.proj-label", LABEL[id]) : null);
}

// An app's top bar: its glyph and name, and an optional button.
function appBar(id, title, right) {
  return h("header.aa-bar",
    h("span.aa-bar-ic", { data: { app: id } }, glyph(GLYPH[id])),
    h("div.aa-bar-title", title),
    right || null);
}

// The green guidance card and the trip line, filled from the live sample.
function turnCard() {
  return h("div.proj-turn.aa-turn",
    h("div.aa-turn-l", h("span.proj-turn-ic"), h("span.proj-turn-dist")),
    h("div.aa-turn-r", h("div.proj-turn-street")));
}
function paintTrip(eta, t) {
  eta.hidden = !t;
  if (!t) return;
  setText(eta.querySelector(".aa-eta-time"), t.long);
  setText(eta.querySelector(".aa-eta-rest"), `${t.miles} mi · ${t.arrive} ${t.ampm}`);
}

export function androidAutoView(deps) {
  const d = withDeps(deps);
  return (root, { arg } = {}) => takeover(root, "aa", (el) => make(el, d, arg));
}

function make(el, d, arg) {
  const recents = ["maps", "nowplaying", "phone"];
  const stage = h("main.aa-stage");
  const recentBox = h("div.aa-recents");
  const launch = h("button.aa-launch", { "aria-label": "Apps",
    onclick: () => router.go(router.current === "launcher" ? "dashboard" : "launcher") }, glyph("dots"));
  el.append(
    stage,
    h("nav.aa-rail",
      launch,
      recentBox,
      h("div.aa-space"),
      h("button.aa-rbtn", { "aria-label": "Notifications" }, glyph("bell")),
      h("div.proj-status.aa-status",
        h("span.aa-name", "Android Auto"),
        h("span.aa-sig", glyph("signal"), glyph("battery")),
        h("span.proj-time")),
      h("button.aa-mic", { "aria-label": "Voice" }, glyph("mic"))));

  const open = (id) => (id === "omacar" ? d.back() : router.go(id));
  const paintRecents = (cur) => {
    clear(recentBox);
    for (const id of recents) {
      const b = icon(id, "aa-recent", () => open(id));
      b.classList.toggle("on", id === cur);
      recentBox.appendChild(b);
    }
  };

  // Maps, the full screen or the dashboard's pane: the map, the green card
  // and the trip, one live binding.
  function mapInto(box, pane, big) {
    const card = turnCard();
    const eta = h(`div.proj-eta.aa-eta${big ? "" : ".mini"}`,
      big ? h("button.aa-round", { "aria-label": "End", onclick: () => router.go("dashboard") }, glyph("close")) : null,
      h("div.aa-eta-txt", h("b.aa-eta-time"), h("span.aa-eta-rest")),
      big ? h("button.aa-round", { "aria-label": "Route" }, glyph("route")) : null);
    box.append(pane.pane, card, eta);
    return (s) => { paintTurn(card, turnOf(s)); paintTrip(eta, tripOf(s && s.demo, d.now())); pane.paint(); };
  }

  // ---------------------------------------------------------------- screens
  const screens = {
    dashboard(host) {
      const pane = mapPane(d, "aa");
      const mapCard = h("section.aa-card.aa-mapcard", {
        onclick: (e) => { if (!e.target.closest("button")) router.go("maps"); } });
      const paint = mapInto(mapCard, pane, false);
      const media = h("section.aa-card.aa-media", {
        onclick: (e) => { if (!e.target.closest("button, .pj-bar")) router.go("nowplaying"); } },
        h("div.aa-media-top",
          stationArt(".sm"),
          h("div.aa-media-txt",
            h("div.pj-title"), h("div.pj-artist"),
            h("div.aa-src", glyph("note"), h("span", "Omarchy Radio")))),
        h("div.pj-bar", h("div.pj-fill")),
        h("div.pj-ctl", mediaButtons(d.radio)));
      const sugg = h("section.aa-card.aa-suggest", {
        onclick: (e) => { if (!e.target.closest("button")) router.go("thread"); } },
        h("div.aa-sg-head",
          h("span.aa-av", { style: { background: THREAD.tint } }, THREAD.initial),
          h("div.aa-sg-who", h("b", THREAD.from),
            h("small", `Messages · ${whenText(d.now() - THREAD.minsAgo * 60000)}`))),
        h("p.aa-sg-text", THREAD.text),
        h("div.aa-sg-acts",
          h("button.aa-chip", glyph("play"), h("span", "Play")),
          h("button.aa-chip", { onclick: () => router.go("thread") }, glyph("reply"), h("span", "Reply"))));
      host.append(h("div.aa-dash", mapCard, h("div.aa-col", media, sugg)));
      pane.start();
      const offLive = onLive(paint);
      const offMedia = bindMedia(media, d.radio);
      return () => { offLive(); offMedia(); pane.stop(); };
    },

    launcher(host) {
      host.append(h("div.aa-launcher",
        h("div.proj-grid.aa-grid", APPS.map((id) => icon(id, "proj-app", () => open(id))))));
    },

    maps(host) {
      const pane = mapPane(d, "aa");
      const box = h("div.aa-maps");
      const paint = mapInto(box, pane, true);
      box.append(h("div.aa-mapbtns",
        h("button.aa-round", { "aria-label": "Search" }, glyph("search")),
        h("button.aa-round", { "aria-label": "Voice guidance" }, glyph("speaker"))));
      host.append(box);
      pane.start();
      const off = onLive(paint);
      return () => { off(); pane.stop(); };
    },

    nowplaying(host) {
      const box = h("div.aa-np",
        appBar("nowplaying", "Omarchy Radio",
          h("button.aa-round", { "aria-label": "Queue", onclick: () => router.go("queue") }, glyph("list"))),
        h("div.aa-np-body",
          stationArt(),
          h("div.aa-np-info",
            h("div.pj-title"), h("div.pj-artist"),
            h("div.pj-bar", h("div.pj-fill")),
            h("div.pj-times", h("span.pj-elapsed"), h("span.pj-dur")),
            h("div.pj-ctl", mediaButtons(d.radio)))));
      host.append(box);
      return bindMedia(box, d.radio);
    },

    queue(host) {
      const tracks = (d.radio && d.radio.tracks) || [];
      const box = h("div.aa-list",
        appBar("nowplaying", "Queue",
          h("button.aa-round", { "aria-label": "Now playing", onclick: () => router.go("nowplaying") }, glyph("close"))),
        h("div.aa-rows", tracks.map((t, i) =>
          h("button.aa-row.pj-row", { data: { i }, onclick: () => { if (d.radio) d.radio.play(i); router.go("nowplaying"); } },
            h("span.aa-qn", String(i + 1)),
            h("div.aa-row-txt", h("b", t.title), h("small", t.artist)),
            h("span.aa-qeq", glyph("bars"))))));
      host.append(box);
      return bindMedia(box, d.radio);
    },

    phone(host) {
      host.append(h("div.aa-list",
        appBar("phone", "Phone", h("button.aa-round", { "aria-label": "Search" }, glyph("search"))),
        h("div.aa-tabs", ["Recents", "Contacts", "Favorites", "Dialpad"].map((t) =>
          h(t === "Favorites" ? "span.on" : "span", t))),
        h("div.aa-faves", FAVOURITES.map((f) =>
          h("div.aa-fave",
            h("span.aa-av.lg", { style: { background: f.tint } }, f.glyph ? glyph(f.glyph) : f.initial),
            h("b", f.name),
            h("small", f.kind[0].toUpperCase() + f.kind.slice(1)))))));
    },

    messages(host) {
      host.append(h("div.aa-list",
        appBar("messages", "Messages"),
        h("div.aa-rows",
          h("button.aa-row.aa-msg", { onclick: () => router.go("thread") },
            h("span.aa-av", { style: { background: THREAD.tint } }, THREAD.initial),
            h("div.aa-row-txt", h("b", THREAD.from), h("small", THREAD.text)),
            h("span.aa-when", whenText(d.now() - THREAD.minsAgo * 60000)),
            h("span.aa-round.sm", glyph("play")),
            h("span.aa-round.sm", glyph("reply"))))));
    },

    thread(host) {
      host.append(h("div.aa-list",
        appBar("messages", THREAD.from,
          h("button.aa-round", { "aria-label": "Back", onclick: () => router.go("messages") }, glyph("close"))),
        h("div.aa-thread",
          h("div.aa-stamp", `Today · ${whenText(d.now() - THREAD.minsAgo * 60000)}`),
          h("div.aa-bubble", THREAD.text),
          h("div.aa-sg-acts",
            h("button.aa-chip", glyph("play"), h("span", "Play")),
            h("button.aa-chip", glyph("reply"), h("span", "Reply"))))));
    },

    podcasts(host) {
      host.append(h("div.aa-list",
        appBar("podcasts", "Podcasts"),
        h("div.aa-empty", glyph("podcast"), h("b", "Your queue is empty"),
          h("small", "New episodes of the shows you follow appear here."))));
    },

    calendar(host) {
      const now = new Date(d.now());
      host.append(h("div.aa-list",
        appBar("calendar", now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })),
        h("div.aa-rows",
          h("button.aa-row.aa-event", { onclick: () => router.go("maps") },
            h("span.aa-evbar"),
            h("div.aa-row-txt", h("b", EVENT.title), h("small", `${EVENT.when} · ${EVENT.where}`)),
            h("span.aa-chip.go", glyph("arrow"), h("span", "Navigate"))))));
    },

    settings(host) {
      const sw = (on) => h(`span.aa-switch${on ? ".on" : ""}`, h("i"));
      const row = (name, sub, right) => h("div.aa-row",
        h("div.aa-row-txt", h("b", name), sub ? h("small", sub) : null), right);
      host.append(h("div.aa-list",
        appBar("settings", "Settings"),
        h("div.aa-rows",
          row("Start automatically", "When the phone connects", sw(true)),
          row("Dark map", "Always", null),
          row("Show message notifications", null, sw(true)),
          row("Weather on the rail", null, sw(false)))));
    },
  };

  const router = createRouter(stage, screens, {
    fallback: "dashboard",
    onChange(id) {
      el.dataset.screen = id;
      clear(launch);
      launch.appendChild(glyph(id === "launcher" ? "dash" : "dots"));
      launch.setAttribute("aria-label", id === "launcher" ? "Dashboard" : "Apps");
      // Recents, most recent first.
      if (APPS.includes(id) && id !== "omacar") {
        const i = recents.indexOf(id);
        if (i !== 0) {
          if (i > 0) recents.splice(i, 1); else recents.pop();
          recents.unshift(id);
        }
      }
      paintRecents(id);
    },
  });
  const stopClock = runClock(el, d.now);
  router.go(arg || "dashboard");
  return { go: (id) => router.go(id), stop() { router.stop(); stopClock(); } };
}

// The demo's routable screen (#androidauto, or #androidauto/maps and so on).
export function register(D, deps) {
  D.extraViews.push({ id: "androidauto", label: "Android Auto", title: "Android Auto",
                      mount: androidAutoView(deps), fast: true });
}

export default androidAutoView;
