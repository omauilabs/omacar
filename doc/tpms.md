# Tire pressures (TPMS) by radio

The owner's aftermarket Tymate TM2 sensors broadcast pressure and temperature
on 433.92 MHz. An RTL-SDR dongle listens, `rtl_433` decodes, and OmaCar reads
`rtl_433`'s JSON. **Nothing is ever sent to the car or transmitted.**

Status, stated plainly: `rtl_433` has no Tymate decoder (doc/ROADMAP.md,
checked 2026-09-26). Everything here is built and tested against the field
names of the decoders it *does* have, and against a simulator. No packet from
the owner's own sensors has been heard yet. A recording (below) decides whether
they can be.

## Parts

| Part | What |
|---|---|
| `lib/tpms.py` | parser, physics, trend, tracker, service, learn, simulator |
| `omacar-tpms.service` | holds `rtl_433` open; writes `tpms.json` |
| `GET /api/tpms` | the four owned wheels, trends, alerts |
| `GET /api/tpms/heard` | the learn list (the only place strangers appear) |
| `POST /api/tpms/assign` | `{id, wheel}` or `{unassign: wheel}`: a label in your config |
| Vehicle > Care > Tires | `share/js/views/tires.js` |
| Home car card | the "Tires" callout now shows the four tires; tap opens Tires |
| `share/js/tpmsbanner.js` | banner for a rapid loss only |

Install: `sudo pacman -S rtl_433`, plug the dongle in,
`systemctl --user enable --now omacar-tpms`.

## What rtl_433 prints (verified against merbanan/rtl_433 `src/devices/tpms_*.c`)

One JSON object per packet with `-F json`. Fields vary per decoder:

- `model`, `type` (`"TPMS"`), `id` (hex string in most; an integer in a few)
- `pressure_kPa` (most) or `pressure_PSI` (Toyota, Ford, Kia, Honda, ...). Gauge.
- `temperature_C` (most) or `temperature_F` (Schrader)
- `battery_ok` (1 = good), `battery_V` (AirPuxem), `battery_pct` (AVE)
- `pressure_alert` (Truck), `flags`, `status`, `code`, `mic`
- `-M time:unix` adds `time`; `-M level` adds `rssi`, `snr`, `noise`, `freq`

The default command is
`rtl_433 -f 433.92M -F json -M time:unix -M level` (`-M level` is how the
learn list can show signal strength). A line that is not a tire (a weather
station on the same band) is ignored; a TPMS line with no pressure is ignored
(never drawn as zero); a pressure outside 0..1500 kPa is dropped as a bad decode.

### If the Tymate sensors need a flex decoder

A flex decoder (`-X "n=tymate,m=FSK_PCM,..."`, `rtl_433 -X help`) prints raw
integers from its `get=` fields, not kPa. Put its timings in
`extra_args`, and say what the integers mean:

```json
{ "extra_args": ["-X", "<your spec from the capture>"],
  "flex": { "model": "tymate", "id": "id",
            "pressure":    { "key": "p", "scale": 1.6, "offset": 0, "unit": "kPa" },
            "temperature": { "key": "t", "scale": 1,   "offset": -50 } } }
```

`model` is the flex spec's `n=` name. The scale and offset come from the
recording; the numbers above are placeholders.

### The recording (the roadmap's open item)

About an hour at 433.92 MHz: `rtl_433 -f 433.92M -A` to see the pulses,
`-S all` / `-w capture.cu8` to keep them, then fit a flex decoder. Change
pressure (and temperature) on one tire and look for the bytes that move.

## Which sensors are yours

433 MHz in a car park is full of other people's tires. Only sensor ids you
assign are ever shown as the car's.

`~/.config/omacar/tpms.json`:

```json
{ "sensors": { "1a2b3c": "FL", "1a2b3d": "FR", "1a2b3e": "RL", "1a2b3f": "RR" },
  "placard_psi": { "front": 36, "rear": 36 },
  "units": "psi" }
```

An id may be `"model:id"` when two decoders could share one. One id, one wheel.

**Learning** (`omacar tpms learn [seconds]`, or Tires > Learn sensors in the
app, **parked only**): lists every id heard with RSSI, pressure and the change
since you started. To tell which is which, **let about 3 psi out of one tire**
and watch the row whose change goes negative; tap the wheel it is on. Heard
strangers are kept 30 minutes, in `tpms-heard.json`, with no history, and
appear nowhere else.

## Numbers

- **Normalised to 20 C**: `p20 = (gauge + 101.325) * 293.15 / (T + 273.15) - 101.325`,
  the ideal-gas ratio on absolute pressure. Levels and trends use it, so a warm
  tire after a drive is not "high" and a cold morning is not a leak. With no
  temperature, the raw reading is used and the screen says it was not corrected.
- **Levels** against the placard (**cold** pressure; default 36 psi front and
  rear, set yours from the door-jamb placard; per-wheel keys like `"RR": 33`
  override): warn at 10 % under (or 25 % over), bad at 20 % under.
- **Rapid loss**: 25 % of pressure gone within 10 minutes, and the newest *two*
  readings both agree (one bad decode must not shout).
- **Slow leak**: "cold" readings are the first reading after a gap of 3 h or
  more (the car sat, the tire is at ambient). The verdict is a Theil-Sen
  (median of pairwise) slope over the last 14 days of them: at least 4 points
  across 3 days, steeper than 0.15 psi/day, and at least 0.5 psi actually lost.
  A jump of 2 psi or more between cold readings is taken as inflation and
  restarts the window. Thresholds: `leak_psi_per_day`, `leak_window_days`,
  `cold_gap_h`.

## Alerts

- **Rapid loss**: a banner across every screen (calm: "Look" / "Got it", never
  modal), a critical outbox message, and **one soft chime per event, only while
  the car is moving**. Justification: at speed this is the one tire event worth
  looking up from the road for; parked, nothing sounds, because a child sleeps
  near the tablet.
- **Slow leak**: no banner and no sound. It is on Tires, Home's callout, and
  `notify.send("tyre-leak", ..., "warn", key="tyre-leak-<wheel>-<date>")`, at
  most once per wheel per day (kept in `tpms-history.json`).
- Low pressure: a lamp, a word ("Low", "Very low") and the callout; no message.

## Source honesty

Every wheel and the screen say where the number came from: **Live · radio**,
**Simulated**, **Last seen N ago**, or **Not heard yet**. A wheel never heard is
a dash. If the service stops writing, every alert disappears and the wheels
become "last seen".

## Simulator

`omacar tpms sim` writes the real `tpms.json` with `source: "sim"`: four
assigned sensors, two strangers (`7e91f0`, `30cc42`), a month of history, and a
slow leak on the rear left. `sim --rapid` has the front right go flat 25 s in.
`sim --lines` prints the packets as rtl_433 JSON, to pipe into
`omacar tpms run --stdin`. A simulator never writes the owner's outbox.

## Files

State (`$XDG_STATE_HOME/omacar`): `tpms.json`, `tpms-heard.json`,
`tpms-history.json` (a point per wheel per 10 minutes, 45 days).
Config: `~/.config/omacar/tpms.json`.
Tests: `test/tpms_test.py`, `test/js/tpms.test.js`.
