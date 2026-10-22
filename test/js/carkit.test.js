import { eq } from "./assert.js";
import { glance, bind, actionState, shown, GLANCE_MAX } from "../js/carkit.js";

// The car widget kit's page half (share/js/carkit.js): glance mode and the
// action states -- the plugin safety rules the page enforces.
const m = (label) => ({ type: "metric", label, value: "$.v" });
const spec = { actions: {
  park: { command: ["x"] },
  go: { command: ["x"], moving: "allowed" },
  write: { command: ["x"], car_write: true },
} };

export default [
  ["parked: the tree is shown as declared", () => {
    const t = { type: "stack", children: [m(1), m(2), m(3), m(4), m(5)] };
    eq(glance(t, false), t);
  }],
  ["moving: at most four widgets, in declaration order", () => {
    const t = { type: "stack", children: [m(1), { type: "row", children: [m(2), m(3)] }, m(4), m(5), m(6)] };
    const g = glance(t, true);
    const labels = [];
    const walk = (n) => n.children ? n.children.forEach(walk) : labels.push(n.label);
    walk(g);
    eq([labels, GLANCE_MAX], [[1, 2, 3, 4], 4]);
  }],
  ["moving: a list keeps only its first three items", () =>
    eq(glance({ type: "list", items: "$.x" }, true).max, 3)],
  ["moving: long text goes, short text stays (judged after binding)", () => {
    const long = "x".repeat(81);
    eq([glance({ type: "text", value: "$.t" }, true, { t: long }), glance({ type: "text", value: "Short" }, true).value],
       [null, "Short"]);
  }],
  ["bind reads a path, passes literals through, and is undefined for a missing one", () =>
    eq([bind("$.a.b", { a: { b: 3 } }), bind("lit", {}), bind("$.a.z", { a: {} }), bind("$.a.b.c", { a: { b: 1 } })],
       [3, "lit", undefined, undefined])],
  ["a missing value is words, never zero", () =>
    eq([shown(null), shown(undefined), shown(""), shown(NaN), shown(0), shown("12")], ["—", "—", "—", "—", "0", "12"])],
  ["parked: every action is live", () =>
    eq(["park", "go", "write"].map((a) => actionState(spec, a, false).ok), [true, true, true])],
  ["moving: parked-only greys with its reason; allowed stays live", () =>
    eq([actionState(spec, "park", true), actionState(spec, "go", true).ok],
       [{ ok: false, why: "Available when you stop" }, true])],
  ["moving: a car write is never live (rule 5)", () =>
    eq(actionState(spec, "write", true), { ok: false, why: "Never while moving" })],
  ["an undeclared action is never live", () =>
    eq(actionState(spec, "ghost", false).ok, false)],
];
