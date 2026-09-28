import { eq, ok } from "./assert.js";
import { h } from "../js/core.js";

export default [
  ["the harness can import the app's own modules", () => ok(typeof h === "function", "h is exported")],
  ["eq compares by value", () => eq({ a: [1, 2] }, { a: [1, 2] })],
  ["eq fails on a difference", () => {
    let threw = false;
    try { eq(1, 2); } catch { threw = true; }
    ok(threw, "eq(1, 2) must throw");
  }],
];
