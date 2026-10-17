import { eq, ok } from "./assert.js";
import { sunTimes } from "../js/solar.js";
import {
  step, freshState, createAmbientGate, resolveLocation, summaryLine, nextBoundary, cleanMapping,
  DEFAULT_MAPPING, nextCity, CITIES, TZ_CITY, AMBIENT, phaseNow,
} from "../js/sunlook.js";

const M = DEFAULT_MAPPING;
const base = { enabled: true, ambientDark: false, alert: false, mapping: M };
const run = (st, over) => step(st, { ...base, ...over });

export default [
  // ---- the scheduler ----
  ["off does nothing and forgets everything", () => {
    const r = step({ applied: "red", override: { phase: "day" }, deferred: true },
                   { ...base, enabled: false, phase: "day", current: "normal" });
    eq([r.apply, r.state], [null, freshState()]);
  }],
  ["turning it on puts the phase's look on at once", () => {
    const r = run(freshState(), { phase: "day", current: "normal" });
    eq([r.apply, r.state.applied], ["day", "day"]);
  }],
  ["nothing changes inside a phase, minute after minute", () => {
    let st = freshState(), cur = "normal", applies = 0;
    for (let i = 0; i < 90; i++) {
      const r = run(st, { phase: "day", current: cur });
      st = r.state; if (r.apply) { applies++; cur = r.apply; }
    }
    eq(applies, 1);
  }],
  ["the boundary changes the look: day, twilight, night in turn", () => {
    let st = freshState(), cur = "normal", seen = [];
    for (const ph of ["day", "day", "twilight", "twilight", "night", "night"]) {
      const r = run(st, { phase: ph, current: cur });
      st = r.state; if (r.apply) { cur = r.apply; seen.push(cur); }
    }
    eq(seen, ["day", "dim", "red"]);
  }],
  ["the owner's own mapping is honoured", () => {
    const r = run(freshState(), { phase: "night", current: "normal", mapping: cleanMapping({ night: "green" }) });
    eq(r.apply, "green");
  }],
  ["a manual change holds until the next boundary, then auto resumes", () => {
    let st = run(freshState(), { phase: "day", current: "normal" }).state;     // we put "day" on
    // the owner taps the toggle: the worn look becomes "normal"
    let r = run(st, { phase: "day", current: "normal" });
    eq(r.apply, null, "no fight with a hand");
    st = r.state;
    for (let i = 0; i < 30; i++) { r = run(st, { phase: "day", current: "normal" }); st = r.state; eq(r.apply, null); }
    r = run(st, { phase: "twilight", current: "normal" });
    eq(r.apply, "dim", "the next boundary resumes");
  }],
  ["a manual change is not undone by the tunnel either", () => {
    let st = run(freshState(), { phase: "day", current: "normal" }).state;
    st = run(st, { phase: "day", current: "dim" }).state;                      // a hand
    eq(run(st, { phase: "day", current: "dim", ambientDark: true }).apply, null);
  }],
  ["an alert defers the change; the next step after it ends makes it", () => {
    let st = run(freshState(), { phase: "day", current: "normal" }).state;     // wears "day"
    let r = run(st, { phase: "twilight", current: "day", alert: true });
    eq([r.apply, r.state.deferred], [null, true]);
    st = r.state;
    r = run(st, { phase: "twilight", current: "day", alert: true });
    eq(r.apply, null, "still deferred");
    st = r.state;
    r = run(st, { phase: "twilight", current: "day", alert: false });
    eq([r.apply, r.state.deferred], ["dim", false]);
  }],
  ["a deferred change that the hand makes moot is not applied", () => {
    let st = run(freshState(), { phase: "day", current: "normal" }).state;
    st = run(st, { phase: "twilight", current: "day", alert: true }).state;
    const r = run(st, { phase: "twilight", current: "red", alert: false });    // owner chose red meanwhile
    eq(r.apply, null);
  }],
  ["a dark tunnel by day wears the night look; leaving it returns the day look", () => {
    let st = run(freshState(), { phase: "day", current: "normal" }).state;
    let r = run(st, { phase: "day", current: "day", ambientDark: true });
    eq(r.apply, "red"); st = r.state;
    r = run(st, { phase: "day", current: "red", ambientDark: false });
    eq(r.apply, "day");
  }],
  ["a dark tunnel at night changes nothing", () => {
    const st = run(freshState(), { phase: "night", current: "normal" }).state;
    eq(run(st, { phase: "night", current: "red", ambientDark: true }).apply, null);
  }],
  ["the tunnel change waits for an alert too", () => {
    const st = run(freshState(), { phase: "day", current: "normal" }).state;
    eq(run(st, { phase: "day", current: "day", ambientDark: true, alert: true }).apply, null);
  }],

  // ---- hysteresis ----
  ["dark must last 20 s before it counts", () => {
    const g = createAmbientGate();
    const a = [0, 5000, 10000, 15000, 19999].map((t) => g.feed(2, t));
    const b = g.feed(2, 20000);
    eq([a.some(Boolean), b], [false, true]);
  }],
  ["a flash of light inside the 20 s starts the count again", () => {
    const g = createAmbientGate();
    g.feed(2, 0); g.feed(2, 15000);
    g.feed(200, 16000);                                  // a streetlight
    g.feed(2, 17000);
    eq(g.feed(2, 30000), false, "only 13 s dark since the flash");
    eq(g.feed(2, 37000), true);
  }],
  ["once dark it needs 20 s of light to let go", () => {
    const g = createAmbientGate();
    g.feed(2, 0); g.feed(2, 20000);
    eq(g.dark, true);
    eq(g.feed(500, 21000), true);
    eq(g.feed(500, 40999), true);
    eq(g.feed(500, 41000), false);
  }],
  ["a reading in the band between the thresholds holds the state and counts nothing", () => {
    const g = createAmbientGate();
    g.feed(2, 0); g.feed(2, 20000);                      // dark
    eq(g.feed(30, 21000), true);                         // dim, not light
    eq(g.feed(30, 90000), true, "the band never releases it");
    ok(30 >= AMBIENT.darkLux && 30 < AMBIENT.lightLux, "30 lux really is inside the band");
  }],
  ["a hovering reading cannot flicker the output", () => {
    const g = createAmbientGate();
    let changes = 0, last = false;
    for (let t = 0; t < 600000; t += 1000) {
      const v = g.feed(t % 2000 ? 14 : 61, t);           // alternates dark-side / light-side every second
      if (v !== last) { changes++; last = v; }
    }
    eq(changes, 0);
  }],
  ["no sensor (null) is sun-only: never dark, and it releases a held dark", () => {
    const g = createAmbientGate();
    g.feed(2, 0); g.feed(2, 20000);
    eq(g.feed(null, 21000), false);
    eq(createAmbientGate().feed(undefined, 0), false);
  }],

  // ---- where ----
  ["a GPS fix beats a chosen home, which beats the time zone", () => {
    const gps = { lat: 47.6, lon: -122.3, ageS: 30 };
    eq(resolveLocation({ gps, homeId: "nyc", tz: "America/Chicago" }).source, "gps");
    eq(resolveLocation({ homeId: "nyc", tz: "America/Chicago" }).source, "home");
    const t = resolveLocation({ tz: "America/Chicago" });
    eq([t.source, t.approx], ["timezone", true]);
  }],
  ["a stale or nonsense GPS fix is not trusted", () => {
    eq(resolveLocation({ gps: { lat: 47, lon: -122, ageS: 99999 }, homeId: "nyc" }).source, "home");
    eq(resolveLocation({ gps: { lat: 999, lon: 0, ageS: 1 }, homeId: "nyc" }).source, "home");
    eq(resolveLocation({ gps: { lat: NaN, lon: 0 }, homeId: "nyc" }).source, "home");
  }],
  ["every US time zone is in the table", () => {
    for (const z of ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
      "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu", "America/Detroit",
      "America/Indiana/Indianapolis", "America/Boise", "America/Juneau", "America/Adak"]) {
      ok(TZ_CITY[z], z);
    }
  }],
  ["an unlisted zone falls back to the clock's offset, and says it is approximate", () => {
    const r = resolveLocation({ tz: "Asia/Nowhere", offsetMin: -360 });
    eq([r.source, r.approx, r.lon], ["offset", true, -90]);
  }],
  ["no source at all is null, not a guess at a place", () => eq(resolveLocation({}), null)],
  ["home cities step round and come back", () => {
    let id = CITIES[0].id;
    for (let i = 0; i < CITIES.length; i++) id = nextCity(id);
    eq(id, CITIES[0].id);
    eq(nextCity(""), CITIES[0].id);
  }],

  // ---- the Settings line and the next boundary ----
  ["the line reads 'Sunset 6:41 · Night look from 7:08' by day", () => {
    const loc = { lat: 40.7128, lon: -74.0060 };
    const t = sunTimes(2024, 10, 17, loc.lat, loc.lon);
    const noon = t.noon;
    const fmt = (ms) => String(Math.round((ms - t.sunset) / 60000));   // minutes after sunset, a zone-free print
    // "Sunset 0 · Night look from N", N the minutes from sunset to dusk
    const line = summaryLine(noon, loc, fmt);
    eq(line, `Sunset 0 · Night look from ${fmt(t.dusk)}`);
  }],
  ["after dusk the line looks to the morning", () => {
    const loc = { lat: 40.7128, lon: -74.0060 };
    const t = sunTimes(2024, 10, 17, loc.lat, loc.lon);
    const line = summaryLine(t.dusk + 3600000, loc, () => "T");
    eq(line, "Sunrise T · Day look from T");
  }],
  ["nextBoundary is the earliest of dawn, sunrise, sunset, dusk after now", () => {
    const loc = { lat: 40.7128, lon: -74.0060 };
    const t = sunTimes(2024, 6, 21, loc.lat, loc.lon);
    const b = nextBoundary(t.noon, loc, [2024, 6, 21]);
    eq([b.kind, b.at === t.sunset, b.to], ["sunset", true, "twilight"]);
    eq(nextBoundary(t.dusk + 1, loc, [2024, 6, 21]).kind, "dawn");
  }],
  ["phaseNow at the real solar noon is day", () => {
    const loc = { lat: 33.7490, lon: -84.3880 };
    eq(phaseNow(sunTimes(2024, 8, 1, loc.lat, loc.lon).noon, loc), "day");
  }],
  ["cleanMapping repairs junk to the defaults", () => {
    eq(cleanMapping({ day: "matrix", night: "green" }), { day: "day", twilight: "dim", night: "green" });
    eq(cleanMapping(null), DEFAULT_MAPPING);
  }],
];
