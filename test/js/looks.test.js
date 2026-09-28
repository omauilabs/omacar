import { eq, ok } from "./assert.js";
import { LOOKS, lookById, applyLook } from "../js/looks.js";

export default [
  ["the default look is OmaCar's own", () => eq(lookById("normal").label, "OmaCar")],
  ["Omarchy's theme is a look you choose", () => ok(LOOKS.some((l) => l.id === "omarchy"), "omarchy look exists")],
  ["choosing a look says so", () => {
    let heard = null;
    const f = (e) => { heard = e.detail; };
    document.addEventListener("omacar:look", f);
    applyLook("day");
    document.removeEventListener("omacar:look", f);
    applyLook("normal");
    eq(heard, "day");
  }],
];
