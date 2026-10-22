// Where the sun is, with no network and no library.
//
// This is NOAA's solar calculator, the algorithm published in the spreadsheet
// behind https://gml.noaa.gov/grad/solcalc/ (the "General Solar Position
// Calculations" note, itself Jean Meeus, "Astronomical Algorithms", ch. 25 and
// 28, low-accuracy form). Good to about a minute for sunrise and sunset between
// the polar circles, which is far inside what a change of look needs. Past the
// polar circles an event that does not happen that day comes back null, and
// elevationAt() still answers, so callers that want "is it dark" can ask the
// sun's height instead of a table of times.
//
// PURE. Every function takes the instant as epoch milliseconds (UTC) and the
// place as degrees, north and EAST positive. No Date.now(), no time zone: a
// daylight-saving change cannot reach it, because it never hears about one.
// The time zone only enters when something is printed, by the caller.
//
// Thresholds (NOAA's): sunrise and sunset are the sun's centre 0.833 degrees
// below the horizon (refraction plus the sun's radius); civil twilight is the
// centre 6 degrees below.

export const SUN_DEG = -0.833;       // sunrise / sunset
export const CIVIL_DEG = -6;         // civil twilight

const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;
const DAY_MS = 86400000;
const mod = (a, n) => ((a % n) + n) % n;

// The sun's declination (degrees) and the equation of time (minutes) at an
// instant. NOAA's spreadsheet columns, in order.
export function sunAt(ms) {
  const jd = ms / DAY_MS + 2440587.5;
  const T = (jd - 2451545) / 36525;
  const L0 = mod(280.46646 + T * (36000.76983 + T * 0.0003032), 360);
  const M = 357.52911 + T * (35999.05029 - 0.0001537 * T);
  const e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T);
  const Mr = M * RAD;
  const C = Math.sin(Mr) * (1.914602 - T * (0.004817 + 0.000014 * T))
          + Math.sin(2 * Mr) * (0.019993 - 0.000101 * T)
          + Math.sin(3 * Mr) * 0.000289;
  const trueLong = L0 + C;
  const omega = 125.04 - 1934.136 * T;
  const lambda = trueLong - 0.00569 - 0.00478 * Math.sin(omega * RAD);
  const eps0 = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60;
  const eps = eps0 + 0.00256 * Math.cos(omega * RAD);
  const decl = Math.asin(Math.sin(eps * RAD) * Math.sin(lambda * RAD)) * DEG;
  const y = Math.tan((eps / 2) * RAD) ** 2;
  const L0r = L0 * RAD;
  const eqTime = 4 * DEG * (y * Math.sin(2 * L0r) - 2 * e * Math.sin(Mr)
    + 4 * e * y * Math.sin(Mr) * Math.cos(2 * L0r)
    - 0.5 * y * y * Math.sin(4 * L0r) - 1.25 * e * e * Math.sin(2 * Mr));
  return { decl, eqTime };
}

// The sun's height above the horizon in degrees, geometric (no refraction), so
// it compares directly with SUN_DEG and CIVIL_DEG.
export function elevationAt(ms, lat, lon) {
  const { decl, eqTime } = sunAt(ms);
  const minutes = mod(ms / 60000, 1440);                 // UTC minutes into the day
  const tst = mod(minutes + eqTime + 4 * lon, 1440);     // true solar time
  const ha = tst / 4 - 180;
  const cosZ = Math.sin(lat * RAD) * Math.sin(decl * RAD)
             + Math.cos(lat * RAD) * Math.cos(decl * RAD) * Math.cos(ha * RAD);
  return 90 - Math.acos(Math.max(-1, Math.min(1, cosZ))) * DEG;
}

// "day" | "twilight" | "night" for the sun's height now. Twilight is the band
// between sunset and the end of civil twilight (and the same at dawn).
export function phaseAt(ms, lat, lon) {
  const el = elevationAt(ms, lat, lon);
  if (el >= SUN_DEG) return "day";
  if (el >= CIVIL_DEG) return "twilight";
  return "night";
}

// Sunrise, sunset and the two civil-twilight edges for the calendar day
// (year, month 1-12, day) AT THE PLACE, as epoch ms. Any one is null when the
// sun does not reach that height that day (polar summer or winter). The day is
// pinned to solar noon at the longitude, so it is the local day in any zone.
export function sunTimes(year, month, day, lat, lon) {
  const base = Date.UTC(year, month - 1, day);
  let noon = base + (720 - 4 * lon) * 60000;
  for (let i = 0; i < 2; i++) noon = base + (720 - 4 * lon - sunAt(noon).eqTime) * 60000;
  const edge = (deg, sign) => {
    let t = noon;
    for (let i = 0; i < 4; i++) {
      const { decl } = sunAt(t);
      const c = (Math.sin(deg * RAD) - Math.sin(lat * RAD) * Math.sin(decl * RAD))
              / (Math.cos(lat * RAD) * Math.cos(decl * RAD));
      if (!(c >= -1 && c <= 1)) return null;
      t = noon + sign * Math.acos(c) * DEG * 4 * 60000;
    }
    return t;
  };
  return {
    dawn: edge(CIVIL_DEG, -1), sunrise: edge(SUN_DEG, -1), noon,
    sunset: edge(SUN_DEG, 1), dusk: edge(CIVIL_DEG, 1),
  };
}
