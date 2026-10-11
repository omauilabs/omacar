// Fonts — the picker, and the three lines that apply one.
//
// WHY THIS IS A PICKER AND NOT A DECISION.
//
// The note back from the meetup was "more polished text — Inter or something".
// Setting Inter is a two-line change and it is the wrong answer, because the
// three places this tool gets read want different things: a laptop on a bench
// at arm's length, a phone held sideways under a bonnet, and a tablet bolted to
// a dashboard in sunlight. Nobody knows which font survives all three until
// they have looked at all three. So: a small set of honest stacks, and the one
// that wins is whichever one is still legible on the third screen.
//
// WHAT A CARD SHOWS, AND WHY IT IS NOT AN ALPHABET.
//
// A pangram in 24px proves a font has letters in it. It says nothing at all
// about whether a drive-mode readout is still readable at sixty, which is the
// only question this tool actually has about type. So every card renders the
// same four things the app really renders — a section heading, a sentence of
// its own prose, a speed at the size drive mode shows one, and a line of the
// small print — each at the size and weight app.css gives it. What you are
// comparing is the app, not a specimen sheet.
//
// AND WHAT IT SHOWS UNDERNEATH.
//
// The family each slot will REALLY render as, resolved on the server against
// fc-list. A picker that offers Inter on a machine without Inter, renders
// something else, and says nothing is a picker you stop believing after the
// first time you catch it. When a head family is missing the card says so and
// gives the pacman line that fixes it.
//
// THIS MODULE ALSO APPLIES THE CHOICE AT BOOT.
//
// Importing it applies whatever this browser last saw, synchronously, before
// anything paints — then asks the server and reconciles. The server is the
// truth, exactly as it is for themes and the drive layout: the font settled on
// at the kitchen table should be the font the dashboard is wearing, and
// localStorage alone would have made it per-browser on a tool deliberately
// used from three of them. The copy in localStorage exists only so the first
// paint is not a flash of the wrong font.

import { h, clear, toast, readOnly } from "../core.js";

const SLOTS = ["sans", "display", "mono"];
const CACHE = "omacar.fonts";
const SHEET = "omacar-fonts";

// core.js resolves this identically and does not export it. Six duplicated
// lines rather than an edit to core.js, which another pair of hands is in:
// without the token a cockpit display opened with ?k= gets a 401 from every
// call here and the picker looks broken rather than read-only.
function token() {
  const k = new URLSearchParams(location.search).get("k");
  if (k) return k;
  try { return sessionStorage.getItem("omacar.k") || ""; } catch { return ""; }
}

async function req(path, opts) {
  const k = token();
  const url = k ? path + (path.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(k) : path;
  const r = await fetch(url, Object.assign({ cache: "no-store" }, opts || {}));
  if (!r.ok) {
    let msg = String(r.status);
    try { msg = (await r.json()).error || msg; } catch { /* body was not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

// ---- applying one ---------------------------------------------------------
//
// A stylesheet and not element.style.setProperty on :root, for the reason
// main.js records at length about the theme: an inline custom property on the
// root element outranks every stylesheet including the look overrides, so the
// moment anything wants to vary type per look — and a night palette wanting a
// heavier face is not a strange idea — an inline value would silently win and
// the bug would present as "the look is broken".
//
// Appended to <head>, so it lands after css/fonts.css and its :root wins on
// document order alone. No specificity games, nothing to keep in step.

export function applyStack(css) {
  if (!css) return;
  let el = document.getElementById(SHEET);
  if (!el) {
    el = document.createElement("style");
    el.id = SHEET;
    document.head.appendChild(el);
  }
  const body = SLOTS
    .filter((s) => typeof css[s] === "string" && css[s])
    .map((s) => `  --${s}: ${css[s]};`)
    .join("\n");
  const next = `:root {\n${body}\n}\n`;
  if (el.textContent !== next) el.textContent = next;
}

function remember(css, id) {
  try { localStorage.setItem(CACHE, JSON.stringify({ id, css })); }
  catch { /* private mode, and the server still knows */ }
}

function recall() {
  try {
    const raw = JSON.parse(localStorage.getItem(CACHE) || "null");
    return raw && raw.css ? raw.css : null;
  } catch { return null; }
}

// The whole boot path, and it is deliberately this short. Applied first from
// the local copy so there is no flash, then corrected from the server.
applyStack(recall());

export async function syncFonts() {
  const cat = await req("/api/fonts");
  applyStack(cat.css);
  remember(cat.css, cat.active);
  return cat;
}

syncFonts().catch(() => { /* offline server; the cached stack is still on */ });

// ---- the picker -----------------------------------------------------------

function slotLine(label, family, absent) {
  return h("div.fnt-slot" + (absent ? ".is-absent" : ""),
    h("b", label),
    h("span", family || "whatever the browser calls " + (label === "mono" ? "monospace" : "sans-serif")));
}

// One card. The specimen is scoped to its own subtree with --fnt-* rather than
// :root, for the same reason the theme preview is: looking at a font is not the
// same as wearing one, and applying it to :root would restyle the button you
// are about to press.
function specimen(css) {
  const box = h("div.fnt-spec");
  for (const s of SLOTS) box.style.setProperty("--fnt-" + s, css[s]);
  box.appendChild(h("div.fnt-spec-h", "Next service"));
  box.appendChild(h("p.fnt-spec-p",
    "Small print sits a step down from the body ink, and has to survive a "
    + "windshield at noon."));
  box.appendChild(h("div.fnt-spec-row",
    h("div.fnt-spec-v", "88"),
    h("div.fnt-spec-u", "km/h"),
    h("div.fnt-spec-v", "14.2"),
    h("div.fnt-spec-u", "volts")));
  box.appendChild(h("div.fnt-spec-m", "0123456789  ·  P0420  ·  21:04:38"));
  return box;
}

export function fontsPanel() {
  let cat = null;
  let custom = null;   // the draft in the three fields, while it is being typed
  const wrap = h("div.fnt");

  async function act(body, ok) {
    try {
      cat = await req("/api/fonts", { method: "POST", body: JSON.stringify(body) });
      applyStack(cat.css);
      remember(cat.css, cat.active);
      if (ok) toast(ok);
      paint();
    } catch (e) {
      toast("Could not save the font: " + (e.message || e), "bad");
    }
  }

  // A read-only cockpit cannot write the machine's choice, and saying so is
  // better than a 403 from a button that looked like it would work. It can
  // still try one on THIS screen, which is often the screen you were trying to
  // read it on in the first place.
  function tryHere(stack) {
    applyStack(stack.css);
    remember(stack.css, stack.id);
    toast("Showing " + stack.name + " on this display only.");
  }

  function card(stack) {
    const active = cat.active === stack.id;
    const absent = stack.missing.length > 0;
    const el = h("div.fnt-card" + (active ? ".is-active" : "") + (absent ? ".is-absent" : ""));

    // No "wearing this" pill here: the button below already says it, the
    // border already shows it, and the theme cards beside these say it once.
    el.appendChild(h("div.fnt-head", h("div.fnt-name", stack.name)));
    el.appendChild(h("p.fnt-note", stack.note));
    el.appendChild(specimen(stack.css));

    const slots = h("div.fnt-slots");
    for (const s of SLOTS) {
      slots.appendChild(slotLine(s, stack.resolved[s],
        stack.missing.includes(stack.families[s][0])));
    }
    el.appendChild(slots);

    if (absent) {
      el.appendChild(h("p.fnt-warn",
        (stack.missing.length === 1 ? stack.missing[0] + " is" : stack.missing.join(", ") + " are")
        + " not installed, so this card is showing the fallback above, not the "
        + "font it is named after."));
      if (stack.install) el.appendChild(h("div.fnt-install", stack.install));
    }

    el.appendChild(h("div.fnt-row",
      readOnly
        ? h("button.th-btn", { onclick: () => tryHere(stack) }, "Try it here")
        : h("button.th-btn" + (active ? "" : ".primary"), {
            disabled: active,
            onclick: () => act({ action: "select", id: stack.id },
                              active ? null : "Wearing " + stack.name + "."),
          }, active ? "Wearing this" : "Wear it")));
    return el;
  }

  // Your own. Three comma-separated chains, which is exactly what a CSS
  // font-family is, because anybody who has got as far as wanting this already
  // knows that shape and inventing a different one would only be in the way.
  function customCard() {
    const stack = custom || (cat.custom
      ? { name: cat.custom.name, sans: cat.custom.sans, display: cat.custom.display, mono: cat.custom.mono }
      : { name: "Something else", sans: [], display: [], mono: [] });
    custom = stack;

    const el = h("div.fnt-card" + (cat.active === "custom" ? ".is-active" : ""));
    el.appendChild(h("div.fnt-head", h("div.fnt-name", "Something else")));
    el.appendChild(h("p.fnt-note",
      "Any family fontconfig can see. `fc-list : family` at a terminal lists "
      + "them. Most wanted first; end each with a generic — sans-serif or "
      + "monospace — so a name that turns out not to be installed still leaves "
      + "something to render with."));

    const fields = h("div.fnt-fields");
    const notes = {
      sans: "every word in the app",
      display: "headings and the large readouts",
      mono: "small numbers, hex, clocks, gauge scales",
    };
    for (const s of SLOTS) {
      fields.appendChild(h("label.fnt-field",
        h("div.fnt-field-k", s),
        h("input.fnt-input", {
          type: "text", spellcheck: "false",
          value: (stack[s] || []).join(", "),
          placeholder: s === "mono" ? "Adwaita Mono, monospace" : "Adwaita Sans, sans-serif",
          oninput: (e) => {
            stack[s] = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
          },
        }),
        h("div.fnt-field-n", notes[s])));
    }
    el.appendChild(fields);

    el.appendChild(h("div.fnt-row",
      h("button.th-btn.primary", {
        disabled: readOnly,
        onclick: () => act({ action: "custom", stack }, "Saved, and wearing it."),
      }, "Wear this"),
      h("button.th-btn", { onclick: () => { custom = null; paint(); } }, "Reset")));
    return el;
  }

  function paint() {
    clear(wrap);
    if (!cat) return;

    wrap.appendChild(h("div.th-head",
      h("div",
        h("h2.th-title", "Type"),
        h("p.th-lede", "Three slots — the words, the headings and the large "
          + "readouts, and the small figures. Every stack below is already on "
          + "this machine or says plainly that it is not; nothing here is "
          + "fetched, because a font that needs a signal is a font that is "
          + "missing in the one car park where it mattered."))));

    if (!cat.checked) {
      wrap.appendChild(h("p.fnt-warn",
        "fc-list could not be run here, so nothing below has been checked "
        + "against what is actually installed. The fallbacks still apply."));
    }
    if (readOnly) {
      wrap.appendChild(h("p.fnt-note",
        "This display is read-only, so it cannot change what the machine "
        + "wears. “Try it here” restyles this screen and nothing else."));
    }

    const grid = h("div.fnt-grid");
    for (const s of cat.stacks) grid.appendChild(card(s));
    grid.appendChild(customCard());
    wrap.appendChild(grid);
  }

  syncFonts().then((c) => { cat = c; paint(); }).catch((e) => {
    wrap.appendChild(h("p.fnt-warn", "Could not read the fonts: " + (e.message || e)));
  });

  return wrap;
}

// The router mounts views; this one is mounted inside the themes view, which is
// where somebody looking for "how this thing looks" already goes. Kept as an
// exported mount rather than folded into themes.js so that the file you open to
// change the type is the file called fonts, and so the boot-time apply above
// can be imported on its own by anything that needs it before a view exists.
export default function appearance(root) {
  root.appendChild(fontsPanel());
  return () => {};
}
