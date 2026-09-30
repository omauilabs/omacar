// The demo's top bar (doc/design/2026-09-30-meetup-demo.md §4, "Labels"): the
// OmaCar logo where the live bar has the word, a "DEMO" pill on the right where
// the mockups put "CONCEPT · DEMO DATA", and a long press on the logo for the
// presenter's menu.
//
//   createBar({ onMenu, later, cancel }) -> afterBar(vbar)
//
// main.js calls afterBar after every paint of the bar (the door's afterBar),
// so it is idempotent: the logo is put in once, the pill is put back only if a
// rebuilt bar lost it, and nothing that is already right moves. The live
// provenance badge (.tb-src: SIMULATED, STARTING, LIVE · OBD-II) is hidden by
// demo.css, on this page only: the pill says what it would.

import { h } from "../../js/core.js";

export const LOGO = "/demo-media/logo.png";
export const LONG_PRESS_MS = 700;

export function createBar({ onMenu = () => {}, later = (fn, ms) => setTimeout(fn, ms),
                            cancel = (id) => clearTimeout(id) } = {}) {
  let pill = null;

  function dressMark(mark) {
    if (mark.dataset.demo === "1") return;
    mark.dataset.demo = "1";
    const img = h("img.tb-logo", { src: LOGO, alt: "OmaCar", draggable: "false" });
    // No logo on this machine: the word stays, as the live bar has it.
    img.addEventListener("error", () => { mark.classList.remove("has-logo"); mark.textContent = "OmaCar"; },
                         { once: true });
    mark.replaceChildren(img);
    mark.classList.add("has-logo");
    mark.title = "Hold for the demo menu";

    // THE PRESS. Pointer events, so a finger, a pen and the Type Cover's
    // touchpad all count; the timer goes on any way the press can end.
    let timer = null;
    const end = () => { if (timer !== null) { cancel(timer); timer = null; } };
    mark.addEventListener("pointerdown", () => {
      end();
      timer = later(() => { timer = null; onMenu(); }, LONG_PRESS_MS);
    });
    for (const type of ["pointerup", "pointercancel", "pointerleave"]) mark.addEventListener(type, end);
    // A long touch is a context menu on a touch screen, and a drag on a mouse.
    mark.addEventListener("contextmenu", (e) => e.preventDefault());
    mark.addEventListener("dragstart", (e) => e.preventDefault());
  }

  return function afterBar(vbar) {
    if (!vbar) return;
    const mark = vbar.querySelector(".tb-mark");
    if (mark) dressMark(mark);
    const right = vbar.querySelector(".tb-right");
    if (!right) return;
    if (!pill) pill = h("span.demo-pill", { title: "A demo: the car, its drive and its data are made up" }, "DEMO");
    // Just before the bar's buttons, after the words.
    const before = right.querySelector(".vbar-btn");
    if (pill.parentNode === right && (before ? pill.nextElementSibling === before : !pill.nextElementSibling)) return;
    right.insertBefore(pill, before || null);
  };
}
