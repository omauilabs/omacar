import { eq } from "./assert.js";
import { sparkPath } from "../js/spark.js";

export default [
  ["fewer than two points draw nothing", () => eq(sparkPath([[0, 1]], 100, 20), "")],
  ["a rising line runs corner to corner", () =>
    eq(sparkPath([[0, 0], [10, 10]], 100, 20), "M0.0 20.0L100.0 0.0")],
  ["a flat line sits in the middle, not on the floor", () =>
    eq(sparkPath([[0, 5], [10, 5]], 100, 20), "M0.0 10.0L100.0 10.0")],
  ["a fixed scale clamps values outside it", () =>
    eq(sparkPath([[0, -50], [10, 500]], 100, 20, 0, 100), "M0.0 20.0L100.0 0.0")],
];
