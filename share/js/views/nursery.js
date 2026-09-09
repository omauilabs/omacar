// The baby, from the road.
//
// WHAT THIS SCREEN IS. Long night drives, and a small child asleep at home.
// The question is "is anything happening", answered on a screen that is
// already on the dashboard so nobody picks up a phone at seventy miles an
// hour.
//
// WHERE THE NUMBERS COME FROM, AND WHY THE APP DOES NOT FETCH THEM. OmaCar
// holds no nursery credential and never talks to that service. The home
// machine does -- it already has the token in a keyring and already merges two
// services that each half-know the answer -- and it pushes one small document
// here on a timer. lib/nursery.py is the whole of that contract.
//
// THE TWO THINGS THIS SCREEN MUST NOT DO.
//
// It must not show a pushed reading as though it were now. A document that
// arrived forty minutes ago saying "asleep" is history, and presented in the
// present tense it is the lie that teaches a parent to stop looking. Past the
// staleness threshold the headline stops answering the question and says how
// long it has been instead; everything under it is labelled as the last thing
// that arrived.
//
// It must not put moving pictures at windscreen height while the car is
// moving. A video of a sleeping child is the most attention-capturing thing
// that could be on this screen. Numbers are glanceable the way a fuel gauge
// is; a feed is not. The app already knows the road speed, so the camera is
// refused with the reason on it rather than left to judgement at two in the
// morning.

import { h, clear, store } from "../core.js";

// Where the camera actually lives. It is a JavaScript web application with no
// public API and no RTSP stream, so there is no honest way to draw it inside
// this page -- the only thing that can show it is a real browser, which is
// what this link opens. Saying that plainly beats an embed that would never
// have worked.
const CAMERA = "https://mycrib.cradlewise.com";

// The same threshold every other speed gate in this app uses.
const MOVING_KPH = 3;

// HOW LONG A STOP HAS TO LAST. The speed gate reads a live sample, and a live
// sample can simply stop arriving -- the link drops at a set of lights, the
// daemon restarts, the adapter is knocked. Every one of those makes SPEED
// undefined, which reads as zero, which reads as stopped. On a gate about a
// video at windscreen height, "I lost the reading" must not mean "go ahead".
// So motion latches: the camera comes back a minute after the last movement
// seen, not the moment the number goes quiet.
const SETTLE = 60000;

function token() {
  try {
    const k = new URLSearchParams(location.search).get("k");
    return k ? "?k=" + encodeURIComponent(k) : "";
  } catch { return ""; }
}

async function fetchNursery() {
  const r = await fetch("/api/nursery" + token(), { cache: "no-store" });
  if (!r.ok) throw new Error("the nursery endpoint said " + r.status);
  return r.json();
}

const span = (m) => {
  const n = Number(m);
  if (!Number.isFinite(n)) return null;
  return n >= 60 ? `${Math.floor(n / 60)}h ${String(n % 60).padStart(2, "0")}m`
                 : `${n}m`;
};

// The app's own tile, not a shape invented here. A view that names classes
// nothing styles renders as unformatted text and nobody notices until it is on
// a tablet in a car -- which has happened to this project before.
function stat(label, value) {
  return h("div.card", h("div.stat-tile", h("div.k", label), h("div.v", value)));
}

export default function nursery(el) {
  clear(el);
  const wrap = h("div.wrap");
  el.appendChild(wrap);

  let doc = null;
  let stop = null;
  let movedAt = 0;

  const draw = () => {
    clear(wrap);

    if (!doc) {
      wrap.appendChild(h("div.card", h("div.title", "Nothing has been sent here"),
        h("p.muted", "This screen shows a document another machine pushes. "
          + "Nothing here reaches out to a nursery service or holds an "
          + "account for one.")));
      return;
    }
    if (!doc.configured) {
      wrap.appendChild(h("div.card",
        h("div.title", "Nothing has been sent here yet"),
        h("p.lede", doc.why || ""),
        h("p.muted", "Run that at home, on a timer. This tablet only ever "
          + "reads the file it leaves behind — no credential is copied and "
          + "nothing new listens on a port.")));
      return;
    }

    const who = doc.name || "The baby";
    const known = !!doc.known;

    // ---- the headline, which stops claiming to know -----------------------
    const head = h("div.card" + (known ? "" : ".tint-warn"));
    head.appendChild(h("div.title", who + (doc.age ? " · " + doc.age : "")));
    if (known) {
      const bits = [doc.state || "unknown"];
      const held = span(doc.for_mins);
      if (held) bits.push(held);
      if (doc.in_crib !== undefined && doc.in_crib !== null) {
        bits.push(doc.in_crib ? "in the crib" : "not in the crib");
      }
      head.appendChild(h("p.lede", bits.join(" · ")));
    } else {
      head.appendChild(h("p.lede", "Nothing has arrived for "
        + span(Math.round(doc.sent_ago / 60))));
      head.appendChild(h("p.muted",
        "What follows is the last that did, not what is happening now."));
    }
    head.appendChild(h("p.muted", "sent " + span(Math.round(doc.sent_ago / 60))
      + " ago" + (doc.from ? " from " + doc.from : "")));
    wrap.appendChild(head);

    // ---- the numbers ------------------------------------------------------
    const row = h("div.grid.g3");
    const cells = [
      ["last night", span(doc.last_night_min)],
      ["asleep today", span(doc.sleep_min)],
      ["in bed", span(doc.in_bed_min)],
      ["naps", doc.naps],
      ["feeds", doc.feeds],
      ["diapers", doc.diapers],
    ];
    let any = false;
    for (const [k, v] of cells) {
      if (v === null || v === undefined || v === "") continue;
      any = true;
      row.appendChild(stat(k, String(v)));
    }
    if (any) {
      // The heading carries the clock with it. A card holding nothing but a
      // word reads as a stray bar above the numbers rather than a title for
      // them.
      const head2 = h("div.card", h("div.title",
        known ? "Today" : "Today, as of the last document"));
      if (doc.rise || doc.bedtime) {
        head2.appendChild(h("p.muted", "rise " + (doc.rise || "\u2014")
          + " \u00b7 bed " + (doc.bedtime || "\u2014")));
      }
      wrap.appendChild(head2);
      wrap.appendChild(row);
    }

    // ---- the camera, and the reason it is not on --------------------------
    const speed = (store.values && store.values.SPEED) || 0;
    if (speed > MOVING_KPH) movedAt = Date.now();
    const moving = movedAt > 0 && (Date.now() - movedAt) < SETTLE;
    const cam = h("div.card", h("div.title", "Camera"));
    if (!doc.camera) {
      cam.appendChild(h("p.muted", "The crib is not reporting a camera."));
    } else if (moving) {
      cam.appendChild(h("p.lede", "Not while the car is moving."));
      cam.appendChild(h("p.muted",
        "A video of a sleeping child is the most attention-capturing thing "
        + "this screen could show, and it is at windscreen height. The "
        + "numbers above are safe to glance at; this is not. It comes back "
        + "a minute after the car has actually stopped \u2014 a reading that "
        + "merely went quiet is not a stop."));
    } else {
      const open = h("button.btn", "Open the camera");
      open.addEventListener("click", () => {
        window.open(CAMERA, "_blank", "noopener");
      });
      cam.appendChild(open);
      cam.appendChild(h("p.muted",
        "It opens in a browser, because that is the only honest way to show "
        + "it: the crib's camera is a web application with no public "
        + "interface and no stream this app could draw."));
    }
    wrap.appendChild(cam);

    // ---- what is answering, which is the alert that was asked for ---------
    const src = doc.sources || {};
    const names = Object.keys(src);
    if (names.length) {
      const card = h("div.card", h("div.title", "Services"));
      const list = h("div.rows");
      for (const n of names) {
        const okay = !!src[n].ok;
        list.appendChild(h("div.row",
          h("span.dot" + (okay ? ".ok" : ".bad")),
          h("span", n),
          h("span.muted", okay ? "answering" : (src[n].error || "not answering"))));
      }
      card.appendChild(list);
      // A nursery that has gone quiet because a token expired looks exactly
      // like a nursery where nothing is happening. That distinction is the
      // whole reason this block is on the screen.
      card.appendChild(h("p.muted",
        "A service that stopped answering looks the same as a quiet night. "
        + "This is how to tell them apart."));
      wrap.appendChild(card);
    }

    const light = doc.nightlight || {};
    if (Object.keys(light).length) {
      wrap.appendChild(h("div.card", h("div.title", "Nightlight"),
        h("p.lede", light.on ? "on" : "off")));
    }
  };

  const load = () => fetchNursery().then((d) => { doc = d; draw(); })
                                   .catch(() => { doc = null; draw(); });
  load();
  draw();

  // The car's own clock, so the camera block answers to the road rather than
  // to a timer of its own.
  stop = store.on("live", draw);
  const poll = setInterval(load, 60000);
  // The latch has to be able to expire on its own. If the gate only redrew on
  // a live sample, a car that stopped and lost its link would keep the camera
  // refused forever -- the mirror image of the failure above, and just as
  // wrong.
  const tick = setInterval(draw, 5000);
  return () => { clearInterval(poll); clearInterval(tick); if (stop) stop(); };
}
