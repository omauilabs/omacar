# The 12 V while parked, and the tablet's own battery

A Honda that stands for days with a small drain on it comes back flat. The tablet follows the ignition (`doc/ignition.md`): it sleeps 30 s after the car is parked. So for most of a park nothing is awake to look, and this feature says exactly that instead of pretending otherwise.

Code: `lib/batterywatch.py`. Screen: Vehicle → Care → 12 V. Home card: "12 V parked". Tests: `test/batterywatch_test.py`, `test/js/bwatch.test.js`.

## What is measured, and when

Every voltage is the adapter's own `ATRV`: the voltage at the OBD port, which is the car's permanent 12 V. It is a question to the adapter, not to the car. Nothing is written to any ECU and no ECU is asked anything. It is the same reading `lib/dtc.py` already uses as its safety floor, and it works with the ECUs off.

| When | Reading | Kept as |
|---|---|---|
| Ignition-off: the charger goes (the ignition signal), the car has been quiet 4 s, inside the 30 s before the tablet sleeps | one `ATRV` | `off`: the park's start. Still settling after the drive, so **never used as a resting value** |
| The tablet is awake and parked (Stay on, or the ignition feature off): every 10 min | one `ATRV` | `parked` |
| Ignition-on: as soon as the charger is back, before cranking if obtainable | one `ATRV` | `on`. If the car is not yet answering it is the resting voltage after the whole park. If it already is, it is running voltage, marked so and never used |
| With each of the above | the tablet's own percentage, from `/sys/class/power_supply` | `pct` |

If the 30 s ran out before a reading could be taken (the adapter was busy, the tablet slept first), the park's start is the daemon's last `CONTROL_MODULE_VOLTAGE`, labelled `live`. Nothing is invented.

**When the tablet is asleep, nothing is measured.** A park of three days is two readings: where it started and where it ended. That pair is shown (a start dot, a dashed line across the unmeasured stretch, an end dot) and a rate is never read out of a pair alone.

### How it shares the adapter

The adapter belongs to the daemon. A reading goes through `connect.request_port()` like every one-off command: the same lock, the same hand-off, a short wait (4 s). A busy adapter means no reading this time and nothing else. It never takes the adapter from a recorder that has it, never changes an adapter setting (no `ATZ`, no `ATE`), and does not run while the car is answering.

The charger as the ignition signal comes from `lib/ignition.py`. Without that feature installed, or with the tablet on a desk charger, the charger never goes off and no park is ever recorded.

## What is projected

Only what the readings can support, with a range, and the number of readings it came from:

- **Resolution.** `ATRV` reads in tenths of a volt (ELM327 datasheet; its absolute accuracy is a few percent, so the *level* can be off by a couple of tenths while a *trend* from the same adapter is consistent). A fall of one or two steps cannot be told from rounding, so it is never turned into a rate. If a meter says the adapter reads high or low, set `offset_v` (below).
- **This park, when it has enough.** The first 2 h after shutdown are ignored (open-circuit voltage relaxes after the engine stops). At least 3 resting readings spread over at least 6 h are needed. The slope is the median of pairwise slopes (Theil-Sen), so one odd reading cannot move it; the range comes from the quartiles of those slopes. "About 8 hours (6 to 10), from 72 readings over 16 h."
  - If the fall is within rounding, it says what it **cannot** see and bounds the drain instead: "no fall the adapter can tell from rounding in 10 h; that rules out a drain faster than 0.48 V a day, so the floor is at least 18 hours away."
  - A single overnight drop (a cold night, at least 0.15 V, most of the whole fall) is named as a step and the fit uses the readings after it.
- **Across parks, when this park has not enough.** For each finished park that stood at least 12 h, how far the resting voltage had fallen at the next start, against how long it stood (3 parks, differing in length by at least a day). "Across 5 parks your 12 V loses about 0.08 V a day parked; from a rested 12.7 V that reaches 12.2 V in about 5.5 days (2.5 to 8.5)." Temperature and how well the last drive charged it are not modelled, which is why it is a range.
- **Otherwise, "not enough readings"**, with what is missing. There is never a number from one reading.
- **The tablet's battery.** While parked and awake: its fall in % an hour (readings at least an hour apart, at least 2 h across) and the time to 10 % (the ignition feature's floor), with a range. After a park: what the park cost, in the past tense, and no runtime (it is charging).

### The floor

12.2 V, the app's own number (`lib/autodisc.py` `ENGINE_OFF_FLOOR`, the write refusal in `lib/write.py`). **Critical** is 11.9 V (the owner's number: the car may not crank). The owner can accept a lower floor: `~/.config/omacar/battery-watch.json`

```json
{ "floor_v": 12.0, "critical_v": 11.9, "settle_h": 2, "sample_min": 10, "offset_v": 0.0, "alert_within_h": 24, "enabled": true }
```

The floor is held at least 0.1 V above the critical level, and the critical level between 10.5 and 12.0 V.

## Alerts

Through `lib/notify.py`, kind `battery-low`, once per park per level:

- **warn**: the latest resting reading is under the floor, or the projection (this park) puts the floor within 24 h;
- **critical**: under 11.9 V.

A running voltage, a post-drive voltage or a reading from a failed read never raises one. On the tablet, Home's card carries the same sentence ("Your 12 V was 12.1 V when you started after 3.3 days parked: under the 12.2 V floor, consider a drive or a maintainer"). While the tablet is asleep nothing can alert: the first chance is the next ignition-on. The phone side of `notify` is not built yet (see `lib/notify.py`).

## Commands

```
omacar battery-watch status      the projection from the readings so far
omacar battery-watch read        one ATRV now, through the hand-off
omacar battery-watch run         the loop (omacar-batterywatch.service)
omacar battery-watch install     enable the service at login
omacar battery-watch sim [healthy|drain|step|low] [seconds]
```

`sim` writes a separate file and the screens say **Simulated** while it runs; the real record is never touched. Scenarios: healthy (barely falls), drain (earlier parks fine, this one falling fast), step (a cold night's drop), low (the car has just started after a park that took it under the floor).

## Not yet measured on the car

- Whether the OBDLink SX's `ATRV` reads in tenths, and how far its level is from a meter, with the car off.
- Whether the adapter answers `ATRV` in the seconds after the charger goes and before the tablet sleeps, and after resume (USB may re-enumerate).
- The settling time of the real battery after a drive: 2 h is an assumption.
