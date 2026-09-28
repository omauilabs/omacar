import { eq } from "./assert.js";
import { spanOf, moveItem, nextSize, defaultLayout } from "../js/homecards.js";

const cat = {
  cards: {
    dial: { label: "Speed", sizes: { m: { land: [3, 3], port: [3, 2] }, l: { land: [4, 3], port: [6, 2] } } },
    coolant: { label: "Coolant", reading: "coolant", sizes: { s: { land: [3, 1], port: [3, 1] } } },
  },
  default: { landscape: [["dial", "m"], ["coolant", "s"]], portrait: [["coolant", "s"], ["dial", "m"]] },
};

export default [
  ["a size spans what the catalogue says, per orientation", () =>
    eq([spanOf(cat, "dial", "l", "landscape"), spanOf(cat, "dial", "l", "portrait")], [[4, 3], [6, 2]])],
  ["an unknown size falls back to the card's first", () => eq(spanOf(cat, "dial", "zz", "landscape"), [3, 3])],
  ["moving an item shifts the rest", () => eq(moveItem(["a", "b", "c", "d"], 0, 2), ["b", "c", "a", "d"])],
  ["moving never loses or duplicates", () => eq(moveItem(["a", "b", "c"], 2, 0), ["c", "a", "b"])],
  ["sizes cycle", () => eq([nextSize(cat, "dial", "m"), nextSize(cat, "dial", "l")], ["l", "m"])],
  ["a card with one size stays at it", () => eq(nextSize(cat, "coolant", "s"), "s")],
  ["the default layout is a copy, with nothing hidden", () => {
    const d = defaultLayout(cat);
    d.landscape.cards[0][1] = "l";
    eq([cat.default.landscape[0][1], d.portrait.hidden], ["m", []]);
  }],
];
