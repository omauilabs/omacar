// The trip journal: Vehicle → Care → Journal.
//
// One card per drive, days as sections, newest first. What each card says is
// written by rules in lib/journal.py from what was recorded; this view only
// lays it out. Tap a drive for its detail: the numbers, every note, and which
// sources were listening. Filtering is by chip and typing is parked-only, so
// nothing here asks for a keyboard while the car moves.

import { h, store, U, withToken, dist, speed, econ, mins } from "../core.js";
import { groupDays, dayTotal, chipsFor, filterEntries, sourceLamp, canEdit,
         QUICK_NOTES, addQuick, span, dayLabel } from "../journal.js";

const get = async (path) => {
  const r = await fetch(withToken(path), { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
};
const post = async (path, body) => {
  const r = await fetch(withToken(path), { method: "POST", body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || String(r.status));
  return j;
};

const LAMP = { warn: "warn", bad: "bad", ok: "ok", info: "info", "": "" };

export default function journalView(root) {
  let pub = null, chip = "all", openId = null, failed = false;

  const lampT = h("span"), lamp = h("span.fr-lamp", { "aria-hidden": "true" });
  const src = h("span.fr-src", lamp, lampT);
  const sub = h("div.jr-sub");
  const chips = h("div.jr-chips", { role: "group", "aria-label": "Filter drives" });
  const list = h("div.jr-list");
  const detail = h("section.jr-detail");
  const back = h("button.jr-back", { type: "button", onclick: () => { openId = null; paint(); } }, "‹ All drives");
  const node = h("div.jr",
    h("section.jr-main",
      h("div.jr-head", h("h1.jr-title", "Journal"), src),
      sub, chips, list),
    detail);
  root.appendChild(node);

  const imperial = () => U.imperial;
  const selected = () => (pub && pub.entries.find((e) => e.id === openId)) || null;

  function paintChips(entries) {
    const locked = !!store.lockedAsMoving;
    chips.replaceChildren(...chipsFor(entries).map((c) =>
      h("button.jr-chip", {
        type: "button", disabled: locked && c.id !== chip, "aria-pressed": String(c.id === chip),
        onclick: () => { chip = c.id; paint(); },
      }, c.label, h("span.jr-chip-n", String(c.n)))));
    sub.textContent = locked ? "Filters are parked-only while the car moves." : "";
  }

  function badges(e) {
    return h("span.jr-badges", ...(e.badges || []).map((b) =>
      h("span.jr-badge", { "data-tone": LAMP[b.tone] || "" }, h("i"), b.label)));
  }

  function card(e) {
    const worst = (e.badges || []).some((b) => b.tone === "bad") ? "bad"
      : (e.badges || []).some((b) => b.tone === "warn") ? "warn" : "";
    return h("button.jr-card", {
      type: "button", "data-tone": worst, "aria-current": e.id === openId ? "true" : null,
      onclick: () => { openId = e.id; paint(); },
    },
      h("span.jr-time", span(e).split(" – ")[0], h("small", e.open ? "now" : span(e).split(" – ")[1])),
      h("span.jr-body",
        h("span.jr-line", e.line),
        e.note ? h("span.jr-note", e.note) : null,
        badges(e)),
      h("span.jr-go", "›"));
  }

  function paintList() {
    if (failed) { list.replaceChildren(h("div.empty", "The journal is unavailable.")); return; }
    if (!pub) { list.replaceChildren(h("div.skel"), h("div.skel")); return; }
    if (!pub.present) {
      list.replaceChildren(h("div.jr-empty",
        h("b", "No drives in the journal yet."),
        h("span", "Each drive the car records becomes an entry here. To see how it looks: omacar journal sim")));
      return;
    }
    const shown = filterEntries(pub.entries, chip);
    if (!shown.length) { list.replaceChildren(h("div.jr-empty", h("b", "No drives match."))); return; }
    list.replaceChildren(...groupDays(shown, pub.now).flatMap((g) => [
      h("div.jr-day", h("span", g.label), h("span.jr-day-t", dayTotal(g, imperial()))),
      ...g.entries.map(card),
    ]));
  }

  function stat(k, v) { return h("div.jr-stat", h("span.jr-k", k), h("span.jr-v.display-num", v)); }

  function paintDetail() {
    const e = selected();
    if (!e) {
      detail.replaceChildren(h("div.jr-pick", pub && pub.present ? "Pick a drive for its detail." : ""), settingsRow());
      return;
    }
    const edit = canEdit(!!store.lockedAsMoving);
    const area = h("textarea.jr-area", {
      rows: "2", maxlength: "280", placeholder: edit.ok ? "A few words about this drive" : "Parked only",
      "aria-label": "Note on this drive", disabled: !edit.ok,
    });
    area.value = e.note || "";
    const save = h("button.jr-save", { type: "button", disabled: !edit.ok, onclick: async () => {
      save.disabled = true;
      try { await post("/api/journal/note", { t0: e.t0, text: area.value }); await load(); }
      catch (err) { save.textContent = "Not saved"; setTimeout(() => { save.textContent = "Save note"; save.disabled = false; }, 1800); }
    } }, "Save note");
    const quick = h("div.jr-quick", ...QUICK_NOTES.map((w) =>
      h("button.jr-qk", { type: "button", disabled: !edit.ok,
        onclick: () => { area.value = addQuick(area.value, w); } }, w)));

    detail.replaceChildren(
      back,
      h("div.jr-d-head",
        h("div.jr-d-day", `${dayLabel(e.t0, pub.now)} · ${span(e)}`),
        e.simulated ? h("span.jr-sim", "Simulated") : ""),
      h("div.jr-d-line", e.line),
      h("div.jr-stats",
        stat("Distance", e.km !== null ? dist(e.km) : "—"),
        stat("Duration", e.duration_s ? mins(e.duration_s) : "—"),
        stat("Average", e.avg_kph ? speed(e.avg_kph) : "—"),
        stat("Economy", e.lphk ? econ(e.lphk) : "—")),
      h("div.jr-k2", "What was noticed"),
      (e.notes && e.notes.length)
        ? h("ul.jr-notes", ...e.notes.map((n) => h("li", { "data-tone": LAMP[n.tone] || "" }, h("i"), n.text)))
        : h("div.jr-none", e.checked && e.checked.length ? "Nothing worth a note." : "No other sources were running."),
      h("div.jr-k2", "Who was listening"),
      h("ul.jr-checked", ...(e.checked || []).map((c) =>
        h("li", { "data-state": c.state }, h("i"), h("b", c.name), h("span", c.text)))),
      e.places ? h("div.jr-places", `${e.places.start || "?"} → ${e.places.end || "?"}`) : "",
      h("div.jr-k2.jr-k2-row", "Your note", h("span.jr-lock", edit.ok ? "" : edit.why)),
      quick,
      h("div.jr-note-row", area, save),
      settingsRow());
  }

  // The opt-in for place names. Off, and unavailable until there is a GPS.
  function settingsRow() {
    const s = (pub && pub.settings) || { places: false, places_available: false };
    const locked = !!store.lockedAsMoving;
    const btn = h("button.jr-switch", {
      type: "button", role: "switch", "aria-checked": String(!!s.places),
      disabled: !s.places_available || locked,
      "aria-label": "Place names",
      onclick: async () => {
        try { await post("/api/journal/settings", { places: !s.places }); await load(); } catch { /* refused */ }
      },
    }, h("i"));
    return h("div.jr-set", h("span.jr-set-k", "Place names"),
      h("span.jr-set-v", s.places_available ? (locked ? "Parked only" : "Start and end of each drive")
        : "Off · needs a GPS, none fitted"), btn);
  }

  function paint() {
    const lampS = sourceLamp(pub);
    lampT.textContent = lampS.text;
    src.dataset.tone = lampS.tone;
    node.dataset.open = openId !== null && selected() ? "1" : "";
    if (pub && pub.present) paintChips(pub.entries); else { chips.replaceChildren(); sub.textContent = ""; }
    paintList();
    paintDetail();
  }

  async function load() {
    try {
      pub = await get(`/api/journal?imperial=${imperial() ? 1 : 0}`);
      failed = false;
      if (openId === null && pub.present && window.matchMedia("(orientation: landscape)").matches) openId = pub.entries[0].id;
    } catch { failed = !pub; }
    paint();
  }

  // Keep the lock honest: the same latch every editor reads.
  let was = !!store.lockedAsMoving;
  const relock = () => { const now = !!store.lockedAsMoving; if (now !== was) { was = now; paint(); } };
  store.addEventListener("live", relock);
  store.addEventListener("car", relock);
  paint();
  load();
  const timer = setInterval(() => {
    // Never rebuild the detail under a thumb that is typing.
    if (!(document.activeElement && document.activeElement.classList.contains("jr-area"))) load();
  }, 60000);
  return () => {
    clearInterval(timer);
    store.removeEventListener("live", relock);
    store.removeEventListener("car", relock);
  };
}
