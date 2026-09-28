// The editor's DOM wiring, driven on a small grid built here: a real drag
// (pointerdown/move/up dispatched on real, laid-out nodes so the "which card
// is my finger over" hit-test in homeedit.js actually runs) plus a tap on a
// size button, checked through the `work` layout that reaches `save`.
//
// setPointerCapture() throws for a pointerId with no real hardware behind it
// -- there is none here -- so the grid's own copy of that method is stubbed
// out for this file. Everything downstream of it (the hit-test, moveItem,
// nextSize, the button handlers) is real.
import { eq, ok } from "./assert.js";
import { startEditing } from "../js/homeedit.js";
import { store } from "../js/core.js";

const CAT = {
  cards: {
    dial:    { label: "Speed",   sizes: { m: { land: [3, 3], port: [3, 2] }, l: { land: [4, 3], port: [6, 2] } } },
    coolant: { label: "Coolant", sizes: { s: { land: [3, 1], port: [3, 1] } } },
    volts:   { label: "12V",     sizes: { s: { land: [3, 1], port: [3, 1] } } },
  },
  default: {
    landscape: [["dial", "m"], ["coolant", "s"], ["volts", "s"]],
    portrait:  [["dial", "m"], ["coolant", "s"], ["volts", "s"]],
  },
};

function freshLayout() {
  return {
    landscape: { cards: [["dial", "m"], ["coolant", "s"], ["volts", "s"]], hidden: [] },
    portrait:  { cards: [["dial", "m"], ["coolant", "s"], ["volts", "s"]], hidden: [] },
  };
}

// #toasts has to exist before any of this runs: toast() (called on the
// driving guard and again on a successful save) appends to it by id.
function ensureToasts() {
  if (!document.getElementById("toasts")) {
    const d = document.createElement("div");
    d.id = "toasts";
    document.body.appendChild(d);
  }
}

// A minimal stand-in for home.js's place(lay): reorders the real DOM nodes to
// match lay.landscape.cards and stamps the size onto each, same contract
// homeedit.js relies on (redraw() calls it after every change).
function makePlace(grid) {
  return function place(lay) {
    const want = [];
    for (const [id, size] of lay.landscape.cards) {
      const node = grid.querySelector(`[data-card="${id}"]`);
      if (!node) continue;
      node.dataset.size = size;
      want.push(node);
    }
    for (const n of [...grid.children]) if (!want.includes(n)) n.remove();
    for (const n of want) grid.appendChild(n);
  };
}

// Three real, laid-out cards side by side, so getBoundingClientRect() (what
// the drag hit-test reads) gives three disjoint rectangles.
function makeGrid() {
  const grid = document.createElement("div");
  grid.className = "home-grid";
  grid.style.cssText = "position: absolute; left: 0; top: 0;";
  let x = 0;
  for (const [id, size] of [["dial", "m"], ["coolant", "s"], ["volts", "s"]]) {
    const node = document.createElement("div");
    node.dataset.card = id;
    node.dataset.size = size;
    node.style.cssText = `position: absolute; left: ${x}px; top: 0; width: 90px; height: 60px;`;
    x += 100;
    grid.appendChild(node);
  }
  document.body.appendChild(grid);
  // See the file header: no real pointer is down behind our synthetic events,
  // so the browser's own setPointerCapture() would throw InvalidPointerId.
  grid.setPointerCapture = () => {};
  return grid;
}

function fire(node, type, x, y) {
  node.dispatchEvent(new PointerEvent(type, {
    pointerId: 7, clientX: x, clientY: y, bubbles: true, cancelable: true,
  }));
}

export default [
  ["while the car is moving, it refuses and says so instead of opening", () => {
    ensureToasts();
    const before = document.getElementById("toasts").children.length;
    store.live = { connected: true, values: { SPEED: 50 } };
    const grid = makeGrid();
    const bar = document.createElement("div");
    let ended = false;
    const controller = startEditing({
      grid, bar, cat: CAT, layout: freshLayout(), orient: () => "landscape",
      place: makePlace(grid), save: async (w) => w, onEnd: () => { ended = true; },
    });
    store.live = null;                    // back to parked for every test below
    ok(controller === null, "startEditing returns null while driving");
    ok(!grid.classList.contains("editing"), "the grid was never put into edit mode");
    ok(!ended, "onEnd is not called — nothing was ever started");
    ok(document.getElementById("toasts").children.length > before, "a toast explained why");
    grid.remove();
  }],

  ["a real drag reorders the work layout, a tapped size cycles it, and Done saves both", async () => {
    ensureToasts();
    const grid = makeGrid();
    const bar = document.createElement("div");
    let saved = null;
    let ended = false;
    const controller = startEditing({
      grid, bar, cat: CAT, layout: freshLayout(), orient: () => "landscape",
      place: makePlace(grid),
      save: async (w) => { saved = JSON.parse(JSON.stringify(w)); return saved; },
      onEnd: () => { ended = true; },
    });
    ok(controller !== null, "parked, the editor opens");
    ok(grid.classList.contains("editing"), "the grid is marked as editing");
    ok(!bar.hidden, "the edit bar is shown");

    // ---- tap the dial's size chip: "m" -> "l" ------------------------------
    const dial = grid.querySelector('[data-card="dial"]');
    dial.querySelector(".he-size").click();
    eq(dial.querySelector(".he-size").textContent, "L");

    // ---- drag the dial across coolant and volts, by real coordinates ------
    // dial sits at x:[0,90], coolant at x:[100,190], volts at x:[200,290],
    // all y:[0,60] -- see makeGrid(). Ending the move over volts' rectangle
    // is what homeedit.js's own hit-test (getBoundingClientRect + a point
    // test) has to find on its own; nothing here tells it which card that is.
    fire(dial, "pointerdown", 10, 10);
    ok(dial.classList.contains("dragging"), "pointerdown grabs the card");
    fire(grid, "pointermove", 210, 10);
    fire(grid, "pointerup", 210, 10);
    ok(!dial.classList.contains("dragging"), "pointerup releases it");

    await controller.finish(true);

    ok(ended, "onEnd fires once editing is finished");
    ok(bar.hidden, "the edit bar hides again");
    ok(!grid.classList.contains("editing"), "the grid leaves edit mode");
    // The size cycle AND the reorder, both landing in the layout `save` saw:
    // dial ends after coolant and volts, at size "l".
    eq(saved.landscape.cards, [["coolant", "s"], ["volts", "s"], ["dial", "l"]]);
    grid.remove();
  }],
];
