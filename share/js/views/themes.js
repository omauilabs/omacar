// Themes — building your own, rather than picking from ours.
//
// The app has always worn whatever Omarchy was wearing, which is right on a
// desktop and not enough in a car. A workshop and a windshield at noon want
// different palettes, and the person who has to read this thing through the
// glare is the one who should get to decide.
//
// NINE COLOURS, NOT TWENTY-NINE.
//
// A theme here is a mode and eight colours. Everything the app actually paints
// with -- four surface steps, four ink weights, five semantic colours and
// their washes, the badge inks, the in-car brights -- is DERIVED from those on
// the server by the same function an Omarchy theme goes through.
//
// That is the whole design, and it is why this editor cannot produce something
// unreadable. The derivation is where the contrast floors live: where `faint`
// gets walked until it clears 5:1 on the surface it will sit on, where a
// terminal-yellow gets pulled until a warning survives sunlight, where
// --bright earns its glare margin. Let somebody set those twenty-nine tokens
// by hand and the first thing they build is a beautiful palette they cannot
// read at seventy miles an hour.
//
// So the preview is not rendered from the colours you picked. It is rendered
// from what the server derives, which is exactly what the app will wear.

import { h, clear, api, toast } from "../core.js";
// Type lives on this screen rather than a view of its own. Colour and font are
// the same question asked twice -- "can I read this through a windscreen" --
// and splitting them across two destinations means picking a font, walking to
// the other screen to see it against the palette, and walking back.
import { fontsPanel } from "./appearance.js";

// Which of the eight to show first, and what to call them. The theme file uses
// terminal names; a person choosing colours is not thinking about ANSI.
const FIELDS = [
  ["background", "Background", "the surface cards sit on"],
  ["foreground", "Text", "everything is written in this, or a step down from it"],
  ["accent", "Accent", "links, focus, the arc on a gauge"],
  ["green", "Good", "no faults, ready, charging"],
  ["yellow", "Warning", "close to a limit"],
  ["red", "Bad", "past a limit, a stored fault"],
  ["blue", "Information", "neutral state pills and notes"],
  ["magenta", "Advisor", "the AI, kept distinct from the rest"],
];

const SWATCH = ["ground", "panel", "raise", "ink", "accent", "ok", "warn", "bad", "ai"];

function slug(name) {
  const s = String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 38);
  return s || "theme-" + Math.random().toString(36).slice(2, 7);
}

function swatches(palette) {
  const strip = h("div.th-strip");
  for (const k of SWATCH) {
    strip.appendChild(h("i", { style: { background: palette[k] || "transparent" },
                               title: "--" + k }));
  }
  return strip;
}

// #themes/new opens the editor straight away. The router already splits the
// hash on "/" and hands the tail to a view, so this costs one line and saves
// somebody who wants a theme from landing on a list first.
export default function themes(root, { arg } = {}) {
  let store = null;      // what the server holds
  let editing = null;    // { id, isNew, body } while the editor is open
  let preview = null;    // the derived palette for `editing`
  let timer = 0;
  // Built once and moved, not rebuilt. paint() runs on every theme action and
  // a fresh panel each time would re-fetch the font catalogue and shell out to
  // fc-list for the privilege of drawing the same six cards again.
  let typePanel = null;

  const wrap = h("div.themes");
  root.appendChild(wrap);

  async function load() {
    try {
      store = await api.themes();
    } catch (e) {
      toast("Could not read the themes: " + (e.message || e), "bad");
      store = { active: "omarchy", desktop: "omarchy", themes: [], seed: {} };
    }
    paint();
  }

  async function act(body, ok) {
    try {
      store = await api.themesDo(body);
      if (ok) toast(ok);
      paint();
    } catch (e) {
      toast("Could not save: " + (e.message || e), "bad");
    }
  }

  // ---- the editor -------------------------------------------------------
  //
  // Debounced, because a colour input fires continuously while it is dragged
  // and every frame of that would otherwise be a round trip.
  function schedulePreview() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!editing) return;
      try {
        const r = await api.themesDo({ action: "preview", theme: editing.body });
        preview = r.palette;
        paintPreview();
      } catch { /* the old preview is still on screen and still true-ish */ }
    }, 120);
  }

  let previewHost = null;

  function paintPreview() {
    if (!previewHost || !preview) return;
    // Scoped to the preview element, so looking at a theme is not the same as
    // wearing one. Applying it to :root would repaint the editor you are
    // working in, including the controls you are reaching for.
    for (const [k, v] of Object.entries(preview)) {
      if (k === "mode") continue;
      previewHost.style.setProperty("--" + k, v);
    }
    previewHost.dataset.mode = preview.mode || "dark";
  }

  function editorPanel() {
    const body = editing.body;
    const panel = h("div.th-editor");

    panel.appendChild(h("div.th-k", editing.isNew ? "New theme" : "Editing"));

    const name = h("input.th-name", {
      type: "text", value: body.name || "", maxlength: 48,
      placeholder: "What to call it",
      oninput: (e) => { body.name = e.target.value; },
    });
    panel.appendChild(name);

    const modes = h("div.drive-pick");
    for (const m of ["dark", "light"]) {
      modes.appendChild(h("button", {
        "aria-pressed": body.mode === m ? "true" : "false",
        onclick: () => { body.mode = m; paint(); schedulePreview(); },
      }, m === "dark" ? "Dark" : "Light"));
    }
    panel.appendChild(h("div.th-k", "Mode"));
    panel.appendChild(modes);

    panel.appendChild(h("div.th-k", "Colors"));
    const grid = h("div.th-fields");
    for (const [key, label, note] of FIELDS) {
      const hex = h("input.th-hex", {
        type: "text", value: body[key], maxlength: 7, spellcheck: "false",
        oninput: (e) => {
          const v = e.target.value.trim();
          if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)) {
            body[key] = v.toLowerCase();
            dot.value = v.length === 4
              ? "#" + v.slice(1).split("").map((c) => c + c).join("") : v;
            schedulePreview();
          }
        },
      });
      const dot = h("input.th-dot", {
        type: "color", value: body[key],
        oninput: (e) => { body[key] = e.target.value; hex.value = e.target.value; schedulePreview(); },
      });
      grid.appendChild(h("label.th-field",
        h("div.th-field-k", label),
        h("div.th-field-row", dot, hex),
        h("div.th-field-n", note)));
    }
    panel.appendChild(grid);

    panel.appendChild(h("div.th-actions",
      h("button.th-btn.primary", {
        onclick: async () => {
          const id = editing.isNew ? slug(body.name) : editing.id;
          await act({ action: "save", id, theme: body }, "Saved.");
          editing = null;
          paint();
        },
      }, "Save"),
      h("button.th-btn", {
        onclick: () => { editing = null; paint(); },
      }, "Cancel")));

    return panel;
  }

  // A real fragment of the app rather than a row of colour chips: the point of
  // the preview is whether the TEXT is readable on the SURFACES, and no strip
  // of swatches has ever answered that.
  function previewPanel() {
    previewHost = h("div.th-preview");
    previewHost.appendChild(h("div.th-preview-in",
      h("div.th-pv-title", editing.body.name || "Untitled"),
      h("div.th-pv-sub", "not connected — plug in the adapter"),
      h("div.th-pv-row",
        h("div.th-pv-card", h("div.th-pv-k", "COOLANT"), h("div.th-pv-v", "89"),
          h("div.th-pv-n", "at temperature")),
        h("div.th-pv-card", h("div.th-pv-k", "BATTERY"), h("div.th-pv-v", "14.2"),
          h("div.th-pv-n", "charging"))),
      h("div.th-pv-pills",
        h("span.th-pill.ok", "no faults"),
        h("span.th-pill.warn", "oil 13%"),
        h("span.th-pill.bad", "2 active"),
        h("span.th-pill.ai", "advisor"),
        h("span.th-pill.info", "simulated")),
      h("div.th-pv-note", "Small print sits at --faint, and has to survive "
        + "a windshield at noon.")));
    paintPreview();
    return previewHost;
  }

  // ---- the list ---------------------------------------------------------
  function card(id, name, palette, opts) {
    const active = store.active === id;
    const el = h("div.th-card" + (active ? ".is-active" : ""));
    el.appendChild(swatches(palette || {}));
    el.appendChild(h("div.th-card-name", name));
    const row = h("div.th-card-row");
    row.appendChild(h("button.th-btn" + (active ? "" : ".primary"), {
      disabled: active,
      onclick: () => act({ action: "select", id }, active ? null : "Wearing " + name + "."),
    }, active ? "Wearing this" : "Wear it"));
    if (opts && opts.editable) {
      row.appendChild(h("button.th-btn", {
        onclick: () => {
          editing = { id, isNew: false, body: { ...opts.body } };
          preview = palette;
          paint();
        },
      }, "Edit"));
      row.appendChild(h("button.th-btn", {
        onclick: () => {
          const body = { ...opts.body, name: opts.body.name + " copy" };
          editing = { id: slug(body.name), isNew: true, body };
          preview = palette;
          paint();
          schedulePreview();
        },
      }, "Duplicate"));
      row.appendChild(h("button.th-btn.danger", {
        onclick: () => act({ action: "delete", id }, "Deleted."),
      }, "Delete"));
    }
    el.appendChild(row);
    return el;
  }

  function paint() {
    clear(wrap);
    if (!store) return;

    wrap.appendChild(h("div.th-head",
      h("div",
        h("h2.th-title", "Themes"),
        h("p.th-lede", "Eight colors and a mode. Everything else — the "
          + "surface steps, the ink weights, the warning colors — is worked "
          + "out from them, so whatever you build stays readable in sunlight.")),
      h("button.th-btn.primary", {
        onclick: () => {
          const body = { ...store.seed, name: "My theme" };
          editing = { id: slug(body.name), isNew: true, body };
          preview = null;
          paint();
          schedulePreview();
        },
      }, "New theme")));

    if (editing) {
      wrap.appendChild(h("div.th-split", editorPanel(), previewPanel()));
      return;
    }

    const grid = h("div.th-grid");
    grid.appendChild(card(store.desktop, "Follow Omarchy", store.desktop_palette, null));
    for (const t of store.themes) {
      const { id, palette, ...body } = t;
      grid.appendChild(card(id, body.name, palette, { editable: true, body }));
    }
    wrap.appendChild(grid);

    if (!store.themes.length) {
      wrap.appendChild(h("p.th-empty",
        "No themes of your own yet. “New theme” starts from a working palette "
        + "rather than a blank form — editing something is a better first move "
        + "than inventing something."));
    }

    if (!typePanel) typePanel = fontsPanel();
    wrap.appendChild(typePanel);
  }

  load().then(() => {
    if (arg === "new" && !editing) {
      const body = { ...store.seed, name: "My theme" };
      editing = { id: slug(body.name), isNew: true, body };
      paint();
      schedulePreview();
    }
  });
  return () => { clearTimeout(timer); };
}
