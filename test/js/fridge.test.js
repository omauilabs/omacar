import { eq } from "./assert.js";
import { fridgeView, fmt } from "../js/fridgecard.js";

// What the fridge card says (share/js/fridgecard.js). Where the number came
// from is never blank, and the simulator is never mistaken for the fridge.
const live = (over = {}) => Object.assign({
  present: true, connected: true, source: "ble", name: "CFX3 45", power: true,
  battery_v: 13.2, supply: "DC", alerts: [], trail: [[0, 3.2], [15, 3.4]],
  zones: [{ temp: 3.4, set: 3.0, door: false, power: true, range: [-22, 10] }],
}, over);

export default [
  ["°F rounds, °C keeps a decimal", () => eq([fmt(3.4, true), fmt(3.4, false), fmt(-18, false)], ["38", "3.4", "-18"])],
  ["no fridge yet says how to find one", () => {
    const v = fridgeView({ present: false });
    eq([v.state, v.canSet, v.canPower, v.line], ["absent", false, false, "No fridge connected yet: omacar fridge scan"]);
  }],
  ["over Bluetooth it says so, in the ok tone", () => {
    const v = fridgeView(live());
    eq([v.source, v.sourceTone, v.line, v.canSet, v.meta], ["Bluetooth", "ok", "Holding", true, "13.2 V · DC"]);
  }],
  ["the simulator always says Simulated", () => {
    const v = fridgeView(live({ source: "sim", name: "CFX3 45 (simulated)" }));
    eq([v.source, v.sourceTone, v.title], ["Simulated", "warn", "CFX3 45"]);
  }],
  ["a stale reading keeps its number and says how old", () => {
    const v = fridgeView(live({ connected: false, age: 300 }));
    eq([v.state, v.temp, v.source, v.canSet, v.canPower], ["stale", "38", "Last seen 5 min ago", false, false]);
  }],
  ["moving locks the set point, never power", () => {
    const v = fridgeView(live(), { locked: true });
    eq([v.canSet, v.canPower, v.lockedNote], [false, true, "Set point changes when you stop"]);
  }],
  ["off says Off and cannot be set", () => {
    const v = fridgeView(live({ power: false }));
    eq([v.line, v.on, v.canSet], ["Off", false, false]);
  }],
  ["warm and pulling down reads as cooling", () =>
    eq(fridgeView(live({ zones: [{ temp: 9, set: 3, door: false, power: true }] })).line, "Cooling down")],
  ["a door left open wins over the temperature words", () =>
    eq(fridgeView(live({ zones: [{ temp: 3, set: 3, door: true, power: true }] })).line, "Door open")],
  ["a pending set point shows until the fridge agrees", () => {
    const v = fridgeView(live(), { pending: 4, imperial: false });
    eq([v.set, v.pending], ["4", true]);
  }],
];
