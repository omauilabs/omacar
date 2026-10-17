import { eq, ok } from "./assert.js";
import { sunTimes, phaseAt, elevationAt, SUN_DEG } from "../js/solar.js";

// Reference: the U.S. Naval Observatory's Astronomical Applications API
// (aa.usno.navy.mil/api/rstt/oneday), fetched 2026-10-17, local clock times to
// the minute. USNO is an independent source from the algorithm under test
// (NOAA's solar calculator, https://gml.noaa.gov/grad/solcalc/), so agreement
// is a real check rather than a mirror. `tz` is the offset in force that day,
// which is why the daylight-saving change days are in the list: Chicago 10 Mar
// 2024 and Los Angeles 3 Nov 2024 each sit on the far side of the change from
// the day before it.
const REF = [
  ["New York", 2024, 6, 21, 40.7128, -74.0060, -4, "04:52", "05:25", "20:31", "21:04"],
  ["New York", 2024, 12, 21, 40.7128, -74.0060, -5, "06:46", "07:17", "16:32", "17:03"],
  ["Chicago, day before DST", 2024, 3, 9, 41.8781, -87.6298, -6, "05:44", "06:11", "17:51", "18:19"],
  ["Chicago, DST begins", 2024, 3, 10, 41.8781, -87.6298, -5, "06:42", "07:10", "18:52", "19:20"],
  ["Los Angeles, day before DST ends", 2024, 11, 2, 34.0522, -118.2437, -7, "06:48", "07:14", "17:59", "18:24"],
  ["Los Angeles, DST ends", 2024, 11, 3, 34.0522, -118.2437, -8, "05:49", "06:15", "16:58", "17:24"],
  ["Denver", 2024, 9, 22, 39.7392, -104.9903, -6, "06:21", "06:48", "18:56", "19:23"],
  ["Honolulu", 2024, 6, 21, 21.3069, -157.8583, -10, "05:26", "05:51", "19:16", "19:41"],
  ["Anchorage midwinter", 2024, 12, 21, 61.2181, -149.9003, -9, "09:12", "10:15", "15:42", "16:44"],
  ["Sydney", 2024, 9, 22, -33.8688, 151.2093, 10, "05:20", "05:45", "17:52", "18:16"],
  ["Miami, DST begins", 2025, 3, 9, 25.7617, -80.1918, -4, "07:13", "07:36", "19:27", "19:50"],
];
const hm = (s) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };
// Minutes into the local day at the zone's offset (no Date, no host zone).
const local = (ms, tz) => (((Math.round(ms / 60000 + tz * 60)) % 1440) + 1440) % 1440;

export default [
  ...REF.map(([name, y, m, d, lat, lon, tz, ...want]) => [
    `${name} ${y}-${m}-${d}: dawn, sunrise, sunset, dusk within 2 min of USNO`, () => {
      const t = sunTimes(y, m, d, lat, lon);
      const got = [t.dawn, t.sunrise, t.sunset, t.dusk].map((x) => local(x, tz));
      got.forEach((g, i) => ok(Math.abs(g - hm(want[i])) <= 2,
        `${["dawn", "sunrise", "sunset", "dusk"][i]}: got ${g}, USNO ${hm(want[i])}`));
    }]),
  ["the clocks changing never reaches the maths: the day after the DST change is an ordinary one-day step", () => {
    // Chicago 9 -> 10 Mar 2024: sunrise moves by ~1 min of solar time, not the
    // hour the wall clock jumps. This is what 'never hears of DST' means.
    const a = sunTimes(2024, 3, 9, 41.8781, -87.6298).sunrise;
    const b = sunTimes(2024, 3, 10, 41.8781, -87.6298).sunrise;
    const stepMin = (b - a) / 60000 - 1440;
    ok(Math.abs(stepMin) < 3, `sunrise moved ${stepMin} min against 24 h`);
  }],
  ["the midnight sun: Fairbanks 21 Jun has sunrise but no civil dusk or dawn", () => {
    const t = sunTimes(2024, 6, 21, 64.8378, -147.7164);
    eq([t.dawn, t.dusk], [null, null]);
    ok(t.sunrise !== null, "sunrise exists");
    eq(local(t.sunrise, -8), hm("02:58"), "USNO sunrise 02:58");
  }],
  ["polar night: Tromso in December has no sunrise at all", () => {
    const t = sunTimes(2024, 12, 21, 69.6492, 18.9553);
    eq([t.sunrise, t.sunset], [null, null]);
  }],
  ["the sun is at the horizon at sunrise and sunset (-0.833 degrees)", () => {
    const t = sunTimes(2024, 6, 21, 40.7128, -74.0060);
    for (const x of [t.sunrise, t.sunset]) {
      ok(Math.abs(elevationAt(x, 40.7128, -74.0060) - SUN_DEG) < 0.1, "at the edge");
    }
  }],
  ["phaseAt walks day, twilight, night across an evening", () => {
    const [la, lo] = [40.7128, -74.0060];
    const t = sunTimes(2024, 6, 21, la, lo);
    const p = (ms) => phaseAt(ms, la, lo);
    eq([p(t.noon), p(t.sunset - 60000), p(t.sunset + 60000), p(t.dusk - 60000), p(t.dusk + 60000)],
       ["day", "day", "twilight", "twilight", "night"]);
  }],
  ["phaseAt knows the polar night is night and the midnight sun is not", () => {
    eq(phaseAt(Date.UTC(2024, 11, 21, 12), 69.6492, 18.9553), "twilight");   // Tromso: the noon glow is civil twilight
    eq(phaseAt(Date.UTC(2024, 5, 21, 10), 64.8378, -147.7164), "twilight");   // Fairbanks 02:00, the sun never gets below -6
    eq(phaseAt(Date.UTC(2024, 5, 21, 20), 64.8378, -147.7164), "day");
    eq(phaseAt(Date.UTC(2024, 5, 21, 11, 30), 78.2232, 15.6267), "day");       // Svalbard, midnight sun
  }],
];
