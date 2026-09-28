import { eq, ok } from "./assert.js";
import { makeSignalTile } from "../js/sigtile.js";
import { reset } from "../js/trail.js";

export default [
  ["a live tile draws its number and names its source", () => {
    reset();
    const t = makeSignalTile("coolant", { label: "Coolant" });
    t.paint({ simulated: true }, { connected: true, values: { COOLANT_TEMP: 90 }, supported: ["COOLANT_TEMP"] });
    eq([t.node.dataset.state, t.node.dataset.src], ["live", "sim"]);
    ok(/\d/.test(t.node.querySelector(".sig-v").textContent), "digits drawn");
  }],
  ["a tile the car cannot fill draws words, not a zero", () => {
    const t = makeSignalTile("charge", { label: "Hybrid pack" });
    t.paint({}, { connected: true, values: {}, supported: ["COOLANT_TEMP"] });
    eq([t.node.dataset.state, t.node.querySelector(".sig-v").textContent,
        t.node.querySelector(".sig-note").textContent, t.node.dataset.src],
       ["absent", "", "Not on this car", undefined]);
  }],
  ["with no car it waits", () => {
    const t = makeSignalTile("fuel");
    t.paint(null, { connected: false });
    eq(t.node.querySelector(".sig-note").textContent, "Waiting for the car");
  }],
  ["it carries its own label", () =>
    eq(makeSignalTile("charge", { label: "Hybrid pack" }).node.querySelector(".sig-k").textContent, "Hybrid pack")],
];
