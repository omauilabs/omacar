import { eq } from "./assert.js";
import { parkingView } from "../js/parking.js";

// What the parking card says (share/js/parking.js). The rule that matters most:
// nothing that can be on screen while the car moves offers a button.
export default [
  ["no loop, no card", () => eq(parkingView({ phase: "off", running: false }), null)],
  ["the car on, no card", () => eq(parkingView({ phase: "on", running: true }), null)],
  ["counting shows the seconds and both taps", () => {
    const v = parkingView({ phase: "counting", running: true, left: 27, grace: 30 });
    eq([v.title, v.sub, v.ring, v.actions],
       ["Car is off", "OmaCar sleeps in 27 s to save the battery.", { left: 27, grace: 30 }, ["stay", "now"]]);
  }],
  ["a charger lost while moving warns and offers nothing", () => {
    const v = parkingView({ phase: "held", held: "moving", running: true });
    eq([v.tone, v.actions, v.ring], ["warn", [], null]);
  }],
  ["the car still answering offers nothing either", () =>
    eq(parkingView({ phase: "held", held: "car answering", running: true }).actions, [])],
  ["staying on offers only Sleep now", () =>
    eq(parkingView({ phase: "held", held: "stay on", running: true, battery: 54 }).actions, ["now"])],
  ["a refused sleep says logind's reason", () =>
    eq(parkingView({ phase: "held", held: "cannot sleep: suspend.target is masked", running: true }).sub,
       "suspend.target is masked")],
  ["a child alert holds with no Sleep now on offer", () => {
    const v = parkingView({ phase: "held", held: "child alert", running: true });
    eq([v.tone, v.kind, v.actions], ["bad", "child", []]);
  }],
  ["an unreadable charger is not a card", () =>
    eq(parkingView({ phase: "on", held: "cannot read the charger", running: true }), null)],
];
