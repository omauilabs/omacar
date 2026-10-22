import { eq } from "./assert.js";
import { arrivalView, pickupView, fmtDist, fmtEta, fmtTemp, sourceOf } from "../js/arrival.js";

// What the arrival card and the pickup line say (share/js/arrival.js). The
// source of a position is never blank, the simulator is never mistaken for
// GPS, and nothing is drawn from a position that is not known.
const clock = (t) => `T${t}`;
const base = (over = {}) => Object.assign({
  location: { source: "gpsd", fix: true, here: { lat: 1, lon: 2 } },
  places: { home: { lat: 1, lon: 2, radius_m: 150 }, pickups: [{ name: "Target Marina", lat: 3, lon: 4 }] },
  arrival: { phase: "away", dist_m: 12000, eta_s: 1080, dismissed: false },
  actions: { garage: { available: false, why: "Garage controller not installed" },
             fridge: { available: true, eco_c: 5, set_c: 3, why: null },
             house: { available: false, why: "No House command set" } },
  pickup: null, last_pickup: null, watching: true,
}, over);

export default [
  ["distances in miles and feet, or km and m", () => eq(
    [fmtDist(100, true), fmtDist(1609.344 * 1.4, true), fmtDist(900, false), fmtDist(12400, false), fmtDist(null)],
    ["330 ft", "1.4 mi", "900 m", "12 km", "—"])],
  ["ETA reads in minutes, then hours", () => eq([fmtEta(20), fmtEta(240), fmtEta(5400), fmtEta(null)],
    ["under a minute", "4 min", "1 h 30 min", ""])],
  ["temperatures follow the unit", () => eq([fmtTemp(3, true), fmtTemp(3, false), fmtTemp(null)], ["37 °F", "3 °C", "—"])],
  ["the source is always written", () => eq(
    [sourceOf({ source: "sim", fix: true }).text, sourceOf({ source: "gpsd", fix: true }).text,
     sourceOf({ source: "gpsd", fix: false }).text, sourceOf({ source: "none" }).text, sourceOf(null).text],
    ["Simulated", "GPS", "GPS, no fix", "No GPS yet", "No GPS yet"])],
  ["the simulator is warn-toned, never ok", () => eq(sourceOf({ source: "sim", fix: true }).tone, "warn")],
  ["before the first read it shows nothing it does not know", () => {
    const v = arrivalView(null);
    eq([v.state, v.show, v.keys, v.pickup], ["unknown", false, [], null]);
  }],
  ["no home saved says where to set it, and shows no card", () => {
    const v = arrivalView(base({ places: { home: null, pickups: [] }, arrival: { phase: "no_home" } }));
    eq([v.state, v.show, v.line], ["no-home", false, "Set your home in Navigation, Places"]);
  }],
  ["no GPS says so, with no distance", () => {
    const v = arrivalView(base({ location: { source: "none", fix: false }, arrival: { phase: "no_fix" } }));
    eq([v.state, v.line, v.source, v.show], ["no-fix", "No GPS receiver yet", "No GPS yet", false]);
  }],
  ["gpsd up but no fix yet says that", () => {
    const v = arrivalView(base({ location: { source: "gpsd", fix: false }, arrival: { phase: "no_fix" } }));
    eq(v.line, "Waiting for a GPS fix");
  }],
  ["away: the distance and ETA to home, no keys offered", () => {
    const v = arrivalView(base(), { imperial: false });
    eq([v.state, v.headline, v.line, v.show], ["away", "Home is", "12 km · 18 min", false]);
  }],
  ["nearly home: the card shows, with the distance and ETA", () => {
    const v = arrivalView(base({ arrival: { phase: "approaching", dist_m: 1400, eta_s: 150, dismissed: false } }), { imperial: false });
    eq([v.state, v.headline, v.line, v.show], ["near", "Nearly home", "1.4 km · about 3 min", true]);
  }],
  ["Not now hides it for the trip", () => {
    const v = arrivalView(base({ arrival: { phase: "approaching", dist_m: 1400, eta_s: 150, dismissed: true } }));
    eq([v.state, v.show], ["near", false]);
  }],
  ["the garage key is disabled and says why; the fridge key shows its eco value", () => {
    const v = arrivalView(base({ arrival: { phase: "approaching", dist_m: 900, eta_s: 90, dismissed: false } }));
    const g = v.keys.find((k) => k.id === "garage"), f = v.keys.find((k) => k.id === "fridge"), o = v.keys.find((k) => k.id === "house");
    eq([g.ok, g.why, f.ok, f.sub, o.ok, o.why], [false, "Garage controller not installed", true, "41 °F", false, "No House command set"]);
  }],
  ["the garage key lights when the controller exists", () => {
    const st = base({ arrival: { phase: "approaching", dist_m: 900, eta_s: 90 } });
    st.actions.garage = { available: true, why: null };
    eq(arrivalView(st).keys[0].ok, true);
  }],
  ["a fridge that is not connected disables its key with the reason", () => {
    const st = base({ arrival: { phase: "approaching", dist_m: 900, eta_s: 90 } });
    st.actions.fridge = { available: false, eco_c: 5, why: "Fridge not connected" };
    const k = arrivalView(st).keys[1];
    eq([k.ok, k.why], [false, "Fridge not connected"]);
  }],
  ["a stopped service is said, not hidden", () => {
    const v = arrivalView(base({ watching: false }));
    eq(v.watchNote, "The arrival service is not running (omacar-arrival)");
  }],
  ["no pickup and nothing done: no pickup line", () => eq(pickupView(base()), null)],
  ["cooling uses the owner's copy: place, temperature, time", () => {
    const v = pickupView(base({ pickup: { name: "Target Marina", mode: "explicit", cooling: true, target_c: 3, base_c: 5,
      by: 4200, eta_s: 600, starts_in_s: 0, arrived: false } }), { imperial: false, clock });
    eq([v.state, v.line, v.sub], ["cooling", "Cooling for your pickup at Target Marina — 3 °C by T4200",
      "Back to 5 °C after the pickup"]);
  }],
  ["before the cooling starts it says when", () => {
    const v = pickupView(base({ pickup: { name: "Target Marina", mode: "explicit", cooling: false, target_c: 1, base_c: null,
      by: 9000, eta_s: 1800, starts_in_s: 600, arrived: false } }), { imperial: false, clock });
    eq([v.state, v.line, v.sub], ["heading", "Heading to Target Marina — cooling starts in 10 min", "1 °C by T9000"]);
  }],
  ["with no GPS and no ETA it asks for one instead of guessing", () => {
    const v = pickupView(base({ pickup: { name: "Target Marina", mode: "explicit", cooling: false, eta_s: null, by: null, arrived: false } }));
    eq([v.state, v.needsEta], ["no-eta", true]);
  }],
  ["an automatic pickup says it started on its own", () => {
    const v = pickupView(base({ pickup: { name: "Target Marina", mode: "auto", cooling: false, target_c: 1, by: 9000,
      eta_s: 800, starts_in_s: 0, arrived: false } }), { clock });
    eq([v.auto, v.sub.startsWith("Started on its own") || v.sub.includes("by T9000")], [true, true]);
  }],
  ["at the place: when the fridge goes back", () => {
    const v = pickupView(base({ pickup: { name: "Target Marina", mode: "explicit", cooling: true, arrived: true, base_c: 3 } }), { imperial: false });
    eq([v.state, v.line, v.sub], ["arrived", "At Target Marina", "Fridge goes back to 3 °C when you leave"]);
  }],
  ["afterwards it remembers what it did", () => {
    const v = pickupView(base({ last_pickup: { name: "Target Marina", restored_to: 3, why: "left" } }), { imperial: false });
    eq([v.state, v.line], ["done", "Pickup at Target Marina done, fridge back to 3 °C"]);
  }],
];
