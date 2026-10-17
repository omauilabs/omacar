// Privacy mode — for photographing the screen.
//
// A VIN identifies one vehicle and, through any number of public and
// commercial databases, its owner and its history. A registration plate does
// the same more directly. Both are on screen constantly and by design: the
// vehicle bar never lets the identity out of sight, precisely so nobody works
// on the wrong car.
//
// That is right in a workshop and wrong on the internet. Somebody
// photographing this app for a forum post, a bug report or social media is
// about to publish a permanent identifier for their own car, and the app is
// the reason it is in frame.
//
// WHY THIS REPLACES TEXT RATHER THAN BLURRING IT.
//
// A CSS blur is a rendering effect: the real string stays in the DOM, in the
// accessibility tree, and in any copy of the page. That is fine against a
// photograph and useless against a screen recording that catches a reflow, a
// "copy as text", or anybody who opens the inspector. Replacing the value
// means what is not shown is genuinely not there to be shown.
//
// WHY THE MASK IS OBVIOUS.
//
// The failure that matters is believing you are private when you are not, so
// the indicator is a visible pill in the vehicle bar rather than a subtle
// state. The second failure is believing you are exposed when you are not,
// which is why the masked form still looks like a field with a value rather
// than an empty space.

import { valet } from "./valet.js";

const KEY = "omacar.privacy";

export const privacy = {
  // VALET IMPLIES PRIVACY. Somebody handed the keys is somebody who can
  // photograph the screen; the owner's own setting is untouched underneath and
  // comes back when valet goes off.
  get on() {
    if (valet.on) return true;
    try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
  },
  set on(v) {
    try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* private mode */ }
  },
  toggle() { this.on = !this.on; return this.on; },
};

// Keep the FIRST THREE characters of a VIN and nothing else.
//
// Those three are the World Manufacturer Identifier: they say "Honda, built in
// Japan" and are shared by hundreds of thousands of cars. Everything from the
// fourth character on narrows towards one vehicle, and the last six are its
// serial number. Showing the WMI keeps the field recognisable as a VIN without
// carrying anything that points at a particular car.
export function vin(value) {
  if (!privacy.on || !value) return value || "";
  const v = String(value);
  if (v.length < 6) return "•".repeat(v.length);
  return v.slice(0, 3) + "•".repeat(v.length - 3);
}

// The vehicle bar's VIN, which is on screen every second the app is open.
//
// Privacy mode is opt-in and nobody remembers to arm it before the screenshot
// they did not plan to take. A full seventeen-character VIN parked in the top
// bar is therefore published by default, which is the wrong default for a
// value whose whole purpose here is "am I looking at the right car".
//
// So the bar always masks the middle. The WMI still says Honda, the last four
// still tell two cars in one household apart, and the eight characters in
// between -- the ones that carry the serial and index a VIN lookup -- are not
// on screen. Privacy mode still collapses this to the WMI alone, and the full
// value is still one click away on the Now tab and in the garage.
export function vinShort(value) {
  if (privacy.on) return vin(value);
  const v = String(value || "");
  if (v.length < 8) return v;
  return v.slice(0, 3) + "\u2022".repeat(v.length - 7) + v.slice(-4);
}

export function plate(value) {
  if (!privacy.on || !value) return value || "";
  return "•".repeat(Math.max(4, String(value).length));
}

// A first name and an initial. Enough to tell two family cars apart at a
// glance, which is the whole reason the field exists, without publishing who
// owns the car in the photograph.
export function person(value) {
  if (!privacy.on || !value) return value || "";
  const parts = String(value).trim().split(/\s+/);
  if (parts.length === 1) return parts[0];
  return parts[0] + " " + parts[parts.length - 1][0] + ".";
}

// The odometer is not an identifier, but it is unusually revealing in
// combination: mileage plus model plus a photograph timestamp narrows a car a
// long way. Rounded rather than hidden, because a rounded figure is still
// useful to look at and a hidden one is not.
export function odo(km) {
  if (!privacy.on || km == null) return km;
  return Math.round(km / 1000) * 1000;
}
