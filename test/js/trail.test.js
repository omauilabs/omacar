import { eq } from "./assert.js";
import { record, trail, reset } from "../js/trail.js";

const s = (t, values, connected = true) => ({ t, connected, values });

export default [
  ["a sample adds one point per numeric value", () => {
    reset();
    record(s(100, { COOLANT_TEMP: 80, VIN: "x" }));
    eq(trail("COOLANT_TEMP"), [[100000, 80]]);
    eq(trail("VIN"), []);
  }],
  ["the same sample polled twice is one point", () => {
    reset();
    record(s(100, { RPM: 900 }));
    record(s(100, { RPM: 900 }));
    eq(trail("RPM").length, 1);
  }],
  ["a disconnected sample adds nothing", () => {
    reset();
    record(s(100, { RPM: 900 }, false));
    eq(trail("RPM"), []);
  }],
  ["points older than five minutes fall off", () => {
    reset();
    record(s(100, { RPM: 1 }));
    record(s(100 + 301, { RPM: 2 }));
    eq(trail("RPM"), [[401000, 2]]);
  }],
  ["trail hands back a copy", () => {
    reset();
    record(s(1, { RPM: 1 }));
    trail("RPM").push([0, 0]);
    eq(trail("RPM").length, 1);
  }],
];
