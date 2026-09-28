// Home's edit mode: drag a card to reorder, tap its size to cycle it, remove
// it, add one back. Nothing is saved until Done; Cancel puts everything back.
//
// ONLY WHILE PARKED. An editor on a moving car is an editor used while
// driving, the rule drive mode's own editor has always kept. If the car starts
// moving mid-edit, the edit is cancelled and says why.
//
// Cards reflow through CSS grid's own placement (grid-auto-flow: dense), so
// there is no free positioning: a layout can never overlap or push a card off
// the screen. Dragging only changes the ORDER.

import { h, clear, icon, store, toast } from "./core.js";
import { ICONS } from "./icons.js";
import { moveItem, nextSize } from "./homecards.js";

export function startEditing({ grid, bar, cat, layout, orient, place, save, onEnd }) {
  if (store.state === "driving") {
    toast("Available when you stop");
    return null;
  }
  const o = orient();
  const work = JSON.parse(JSON.stringify(layout));
  let alive = true;
  let drag = null;

  grid.classList.add("editing");
  const done = h("button.btn.btn-primary", { type: "button" }, "Done");
  const cancel = h("button.btn", { type: "button" }, "Cancel");
  const reset = h("button.btn", { type: "button" }, "Reset");
  const add = h("button.btn", { type: "button" }, icon(ICONS.plus, 16), " Add card");
  clear(bar);
  bar.append(h("span.he-note", "Drag to move · tap a size to change it"), add, reset, cancel, done);
  bar.hidden = false;

  function decorate() {
    for (const node of grid.children) {
      if (node.querySelector(":scope > .he-ctl")) continue;
      const id = node.dataset.card;
      const sizes = Object.keys(cat.cards[id].sizes);
      const size = h("button.he-size", { type: "button", hidden: sizes.length < 2,
        "aria-label": "Change size",
        onclick: (e) => {
          e.stopPropagation();
          const row = work[o].cards.find((x) => x[0] === id);
          row[1] = nextSize(cat, id, row[1]);
          redraw();
        } }, (node.dataset.size || "").toUpperCase());
      const rm = h("button.he-rm", { type: "button", "aria-label": "Remove " + cat.cards[id].label,
        onclick: (e) => {
          e.stopPropagation();
          work[o].cards = work[o].cards.filter((x) => x[0] !== id);
          if (cat.default[o].some((x) => x[0] === id)) {
            work[o].hidden = [...new Set([...work[o].hidden, id])];
          }
          redraw();
        } }, icon(ICONS.x, 16));
      node.appendChild(h("div.he-ctl", size, rm));
    }
  }
  function strip() { for (const c of grid.querySelectorAll(".he-ctl")) c.remove(); }
  function redraw() { strip(); place(work); decorate(); }

  // Pointer capture goes on the GRID, not the card: place() moves cards with
  // appendChild, and moving a node releases any capture it holds.
  function onDown(e) {
    const node = e.target.closest(".home-grid > [data-card]");
    if (!node || e.target.closest(".he-ctl")) return;
    e.preventDefault();
    grid.setPointerCapture(e.pointerId);
    drag = { node, id: node.dataset.card, x: e.clientX, y: e.clientY };
    node.classList.add("dragging");
  }
  function onMove(e) {
    if (!drag) return;
    drag.node.style.transform = `translate(${e.clientX - drag.x}px, ${e.clientY - drag.y}px)`;
    const under = [...grid.children].find((n) => {
      if (n === drag.node) return false;
      const r = n.getBoundingClientRect();
      return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    });
    if (!under) return;
    const list = work[o].cards;
    const from = list.findIndex((x) => x[0] === drag.id);
    const to = list.findIndex((x) => x[0] === under.dataset.card);
    if (from < 0 || to < 0 || from === to) return;
    work[o].cards = moveItem(list, from, to);
    // The dragged card's grid slot moved, so the finger's offset is measured
    // again from where the card now sits: it stays under the finger.
    drag.node.style.transform = "";
    const before = drag.node.getBoundingClientRect();
    redraw();
    const after = drag.node.getBoundingClientRect();
    drag.x += after.left - before.left;
    drag.y += after.top - before.top;
    drag.node.style.transform = `translate(${e.clientX - drag.x}px, ${e.clientY - drag.y}px)`;
  }
  function onUp() {
    if (!drag) return;
    drag.node.classList.remove("dragging");
    drag.node.style.transform = "";
    drag = null;
  }
  // In edit mode a tap on a card is part of editing, never a navigation.
  function swallow(e) {
    if (!e.target.closest(".he-ctl")) { e.stopPropagation(); e.preventDefault(); }
  }

  grid.addEventListener("pointerdown", onDown);
  grid.addEventListener("pointermove", onMove);
  grid.addEventListener("pointerup", onUp);
  grid.addEventListener("pointercancel", onUp);
  grid.addEventListener("click", swallow, true);
  const offLive = store.on("live", () => {
    if (store.state === "driving") {
      toast("Editing stopped: the car is moving");
      finish(false);
    }
  });

  add.onclick = () => {
    const placed = new Set(work[o].cards.map((x) => x[0]));
    const avail = Object.keys(cat.cards).filter((id) => !placed.has(id));
    if (!avail.length) { toast("Every card is already on Home"); return; }
    const host = document.getElementById("modal-host");
    const close = () => { host.hidden = true; clear(host); host.onclick = null; };
    const rows = avail.map((id) => h("button.sheet-row", { type: "button", onclick: () => {
      work[o].cards.push([id, Object.keys(cat.cards[id].sizes)[0]]);
      work[o].hidden = work[o].hidden.filter((x) => x !== id);
      close();
      redraw();
    } }, h("span.sheet-l", h("span.sheet-lab", cat.cards[id].label)), h("span.sheet-v", "Add")));
    clear(host);
    host.appendChild(h("div.sheet", { role: "dialog", "aria-modal": "true", "aria-label": "Add a card" },
      h("div.sheet-head", h("div.title", "Add a card"),
        h("button.btn.right", { type: "button", onclick: close }, "Close")),
      h("div.sheet-rows", rows)));
    host.hidden = false;
    host.onclick = (e) => { if (e.target === host) close(); };
  };
  reset.onclick = () => {
    work[o] = { cards: cat.default[o].map((x) => x.slice()), hidden: [] };
    redraw();
  };
  cancel.onclick = () => finish(false);
  done.onclick = () => finish(true);

  async function finish(keep) {
    if (!alive) return;
    alive = false;
    onUp();
    grid.removeEventListener("pointerdown", onDown);
    grid.removeEventListener("pointermove", onMove);
    grid.removeEventListener("pointerup", onUp);
    grid.removeEventListener("pointercancel", onUp);
    grid.removeEventListener("click", swallow, true);
    offLive();
    grid.classList.remove("editing");
    strip();
    bar.hidden = true;
    clear(bar);
    if (keep) {
      try { place(await save(work)); toast("Layout saved"); }
      catch (err) { toast("Could not save the layout: " + ((err && err.message) || err), "bad"); place(layout); }
    } else {
      place(layout);
    }
    if (onEnd) onEnd();
  }

  decorate();
  return { finish };
}
