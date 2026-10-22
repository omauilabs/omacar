# Radar detector (Valentine One)

OmaCar shows a Valentine One's alerts on Home, as a banner over every screen
while one is active, and in full under Navigation, Radar. It listens over
Bluetooth LE and can ask the detector for exactly one thing: Mute. The app
makes no sound (the V1 beeps for itself) and records no location: the rolling
log is band, frequency, time, direction at the peak and peak strength.

## Setup

1. `omacar v1 setup` installs `bleak`, the Bluetooth library, in OmaCar's venv.
2. Power the V1. If you have an original V1 it needs a **V1connection LE**
   adapter; a phone holding the adapter's connection (the Valentine app) will
   keep the tablet out, so close it.
3. `omacar v1 scan` lists adapters advertising the V1connection LE service. To
   pin one: `echo '{"address": "AA:BB:..."}' > ~/.config/omacar/v1.json`.
   KESPL (below) does not bond; if BlueZ refuses the connection, pair once with
   `bluetoothctl` and tell us, because that is not verified.
4. `systemctl --user enable --now omacar-v1.service` (the unit is
   `share/systemd/omacar-v1.service`), or try it in a terminal: `omacar v1 run`.
5. Put the **Radar** card on Home (Customize layout). `omacar v1 status` prints
   what the app is being told.

`omacar v1 sim [seconds] [mix|ka|k|laser|loop|idle]` is a simulated detector,
for seeing the screens. Every screen says **Simulated** while it runs, and it
writes the same file as the real link, so do not run it beside the service.

## What is shown

- **Card** (Home, 4x2 or 6x2). Idle: where the link comes from ("V1 ·
  Bluetooth", "Simulated", "Not connected"), alerts today, the last alert's
  age. Alert: the priority bogey as band and frequency (Ka 34.7), the V1's own
  front/side/rear arrow, eight signal bars, how many other bogeys, and Mute.
- **Banner**: a strip across the top of the stage (not the top bar, not the
  dock) while any alert is active, for 3 s after the last. Laser is red, Ka
  amber, K/X/Ku/Photo blue, and the band's name is always written. It never
  takes focus, lets taps fall through everywhere but its Mute key, and steps
  aside on the Radar screen and on Home while Home has a radar card.
- **Radar screen**: the live alert table, a compass (the car with four lamps),
  the V1's band lamps, the last 50 encounters, and the link's facts.

Mute is allowed while driving: it is one tap, like the detector's own button.
It is not offered to a read-only (token) cockpit.

An alert that is no longer being reported is never kept on screen: the server
drops alerts from a file older than 10 s, and the link clears them when the
V1 sends an empty table or its face goes dark for 1.5 s.

## The law

Radar detectors are legal in private passenger vehicles in California. They
are illegal in Virginia and the District of Columbia, and in commercial
vehicles over 10,000 lb under federal rules. This is information, not legal
advice; check where you drive.

## What is unverified

Nothing here has been run against a real Valentine One or V1connection LE.

- **Which hardware talks.** The protocol source (KESPL, MIT) only scans for the
  V1connection LE service (`92A0AFF4-9E05-11E2-AA59-F23C91AEC05E`). Whether a
  V1 Gen2 advertises that service itself, or needs the adapter, is not shown by
  anything read. `omacar v1 scan` will say.
- **Pairing.** KESPL connects without bonding. BlueZ may differ.
- **Ku band bit.** KESPL's code and its own bit comments disagree (0x16 vs bit
  4, 0x10). lib/esp.py reads bit 4.
- **Signal strength in bars.** The alert table carries raw front and rear
  levels and KESPL has no table to turn them into the V1's eight bars. The
  priority alert's bars come from the display data's own bar graph (exact);
  every other alert uses an even split, an approximation.
- **Whether alert tables stream continuously** while alerting, or only on
  change. Encounters end 3 s after the last table; if a V1 sends on change
  only, long alerts would be logged more than once.
- **Origin id.** Requests are sent as the V1connection (0xE6) and answers
  addressed to it or to everyone are accepted, as KESPL does. A built-in Gen2
  link may behave differently.
- **Encounter grouping** (same band, within 30 MHz) is this app's heuristic.
- The Gen2 test (firmware 4.1018 or later) is read from a comment in KESPL; its
  constant was not read.

## Credits

Protocol constants and layouts: KESPL (MIT, (c) 2025 Develpr Of Things),
credited in `lib/esp.py` and `ATTRIBUTION.md`. Frame format cross-checked
against tz1/bluev (GPL-3, nothing taken). Valentine One and V1connection are
Valentine Research's names; OmaCar is not affiliated with them.
