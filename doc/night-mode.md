# Looks by the sun

Settings, "Looks by the sun". Off until you turn it on. When on, the app wears
the **day** look by day, a **twilight** look between sunset and the end of civil
twilight, and the **night** look after that. Defaults: Daylight, Night dim,
Night red. Tap a cell under the sun's arc to change which look a phase wears.

Everything is offline. Nothing calls out to a sun service.

## Rules

- A look changes **only when the sun crosses a boundary** (sunset, end of civil
  twilight, dawn, sunrise), or when you turn the setting on.
- **Your hand wins until the next boundary.** Tap the day/night toggle or the
  Look row and auto mode stands down until the sky moves on to the next phase.
- **Never during an alert.** Drowsy mode above level 0, a radar alert, or
  anything that dispatches `omacar:alert` `{source, active}` defers the change;
  it is retried every minute. (The child alert should dispatch that event; until
  it does, auto mode cannot know about it.)
- The change is a slow four-second crossfade (View Transitions). With
  `prefers-reduced-motion` it simply changes.
- Settings are parked-only (greyed while driving, never hidden). Stored in this
  browser: `omacar.sunlooks` (enabled, mapping, home city) and
  `omacar.sunlooks.state` (what auto mode last did, so a reload keeps your override).

## Where

In order: a GPS fix, if the server has a `GET /api/location` that answers
`{lat, lon}` (or `{latitude, longitude}`, or `{fix: {...}}`) and the fix is
under six hours old (checked at run time, never required; the 404 in the
console is that probe); else the **home city** picked in Settings (tap to step
through a short list, no keyboard); else the **time zone's** representative
city, labelled "approximate". An unlisted zone uses the clock's UTC offset for
longitude and an assumed latitude of 40, also labelled approximate.

## Ambient light

`GET /api/ambient` (lib/ambient.py, read-only, `omacar ambient`) reads the
Surface's IIO illuminance channel from `/sys/bus/iio/devices`. If lux stays
below 15 for 20 s while the sun says day or twilight (tunnel, garage), the night
look is worn; it is released after 20 s at 60 lux or more. Between 15 and 60
nothing changes, so a reading hovering at one value cannot flicker. No sensor, or
an unreadable one, means the sun alone decides and Settings says so. Caveat: a
sensor behind a case or a dash mount may read low in daylight; the thresholds are
constants in `share/js/sunlook.js` (`AMBIENT`), unverified on the real tablet.

## Maths and tests

`share/js/solar.js` is NOAA's solar calculator algorithm (the spreadsheet behind
gml.noaa.gov/grad/solcalc; Meeus, Astronomical Algorithms ch. 25 and 28). It
takes epoch milliseconds and degrees and never sees a time zone, so daylight
saving cannot reach it. `test/js/solar.test.js` checks dawn, sunrise, sunset and
dusk against the US Naval Observatory's API for eleven place/dates (all within
2 minutes), including DST-change days, midwinter Anchorage, Sydney, the
midnight sun and polar night. `test/js/sunlook.test.js` covers the scheduler,
hysteresis, location choice and the Settings line. `test/ambient_test.py`
covers the sensor reader against a fake sysfs.
