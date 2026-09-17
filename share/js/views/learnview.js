// Learn — one screen, two directions.
//
// The app learns the car, and the app teaches you. Those sound like different
// features and are really the same one: the reason a scan tool is intimidating
// is that it shows you a car you do not understand using words you do not
// know, and fixing only half of that does not help. Discovering that your car
// has six modules is useless if "module" means nothing; knowing what a module
// is does not tell you which ones YOUR car has.
//
// So they share a screen. What we found on the left, what it means on the
// right, and the same vocabulary in both.

import { h, store, api, since, ago, toast } from "../core.js";
import { learn, TOPICS } from "../learn.js";
import { onboard, showOnboarding } from "../onboard.js";

export default function learnView(root) {
  let known = null;
  let busy = false;

  const wrap = h("div.learnview");
  root.appendChild(wrap);

  async function load() {
    try {
      known = (await api.learned()).car;
    } catch {
      known = null;
    }
    draw();
  }

  async function runLearn(deep) {
    if (busy) return;
    busy = true;
    draw();
    try {
      const r = await api.learn(deep);
      known = r.car;
      toast(`Learned ${known.modules} module${known.modules === 1 ? "" : "s"}.`);
    } catch (e) {
      // The common failures are all mundane and all worth naming precisely:
      // no adapter, the gauge daemon holding the port, a flat battery.
      toast("Could not learn: " + (e.message || e), "bad");
    } finally {
      busy = false;
      draw();
    }
  }

  function carHalf() {
    const k = known || {};
    const has = (k.modules || 0) > 0;

    const stats = h("div.learn-stats",
      stat(k.modules || 0, "modules found"),
      stat(k.uds_modules || 0, "speak UDS"),
      stat(k.catalogue_size || 0, "fault codes known"),
      stat(k.passes || 0, "passes"));

    return h("section.card",
      h("div.eyebrow", "Part one"),
      h("div.title", "What OmaCar knows about this car"),
      h("p.lede", has
        ? `Last learned ${k.learned_at ? ago(k.learned_at) : "recently"}. `
          + "Run it again any time — it adds what it finds rather than starting over."
        : "Nothing yet. Plug in the adapter, turn the ignition to position II, "
          + "and let it look around. Everything it does here only reads."),
      stats,
      has ? h("div.learn-modules", ...(k.detail || []).map(moduleRow)) : null,
      h("div.row.wrapline", { style: { gap: "8px", marginTop: "14px" } },
        h("button.btn.primary", { disabled: busy, onclick: () => runLearn(false) },
          busy ? "Learning…" : has ? "Learn again" : "Learn this car"),
        h("button.btn", { disabled: busy, onclick: () => runLearn(true) },
          "Deep pass"),
        h("span.sub", "A deep pass also probes for manufacturer data. Slower.")));
  }

  function stat(n, label) {
    return h("div.learn-stat",
      h("div.learn-stat-n", String(n)),
      h("div.learn-stat-k", label));
  }

  function moduleRow(m) {
    const idents = Object.entries(m.ident || {});
    return h("div.learn-mod",
      h("div.row.wrapline",
        h("div",
          h("span.mono", m.header),
          h("span.learn-mod-label", m.label || "")),
        h("div.right.row.wrapline", { style: { gap: "6px" } },
          ...(m.services || []).map((s) => h("span.pill", s)),
          m.catalogue ? h("span.pill.ok", `${m.catalogue} codes`) : null)),
      idents.length
        ? h("div.learn-mod-ident", ...idents.map(([k2, v]) =>
            h("div", h("span.learn-mod-k", k2), h("span.mono", String(v)))))
        : null);
  }

  function appHalf() {
    return h("section.card",
      h("div.eyebrow", "Part two"),
      h("div.title", "What everything in this app means"),
      h("p.lede", "Turn this on and every screen explains itself where it stands. "
        + "It hides nothing — it only adds. Deciding what you are allowed to "
        + "understand about your own car is the vendor's move, not ours."),
      h("div.row.wrapline", { style: { gap: "8px", marginTop: "12px" } },
        h("button.btn" + (learn.on ? ".primary" : ""), {
          onclick: () => {
            learn.toggle();
            toast(learn.on ? "Explanations on, across the app." : "Explanations off.");
            draw();
          },
        }, learn.on ? "Explanations are on" : "Turn on explanations"),
        h("button.btn", {
          onclick: () => showOnboarding(document.getElementById("modal-host"),
                                        { onClose: () => {} }),
        }, onboard.done ? "Replay the intro" : "Show the intro")),

      h("div.glossary",
        ...Object.entries(TOPICS).map(([, t]) =>
          h("details.gloss",
            h("summary", t.title),
            h("p.learn-body", t.body)))));
  }

  function draw() {
    while (wrap.firstChild) wrap.removeChild(wrap.firstChild);
    wrap.appendChild(carHalf());
    wrap.appendChild(appHalf());
  }

  draw();
  load();
}
