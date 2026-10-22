# Environment: cabin and outdoor air

The Home card **Environment** and the **Environment** screen (tap the card)
show the cabin and the outside: temperature, humidity, the air, pressure, and
the last 24 hours of each.

> **Status.** The software runs end to end against the simulator. The
> **hardware is not bought**, the **firmware has never been compiled**, and
> nothing here has been tested with a real sensor or on the tablet. See
> "What is unverified".

## Hardware

Two nodes, each a small board on USB printing one JSON line a second. Firmware,
wiring tables and placement advice are in `firmware/envnode/README.md`.

| Node | Sensors | Reports |
|---|---|---|
| cabin | Sensirion SEN66 over I2C | `t_c`, `rh`, `co2`, `pm1`, `pm25`, `pm4`, `pm10`, `voc`, `nox` |
| outdoor | Bosch BME280 + Plantower PMS5003 | `t_c`, `rh`, `hpa`, `pm1`, `pm25`, `pm10` |

The reader is generic: any device that prints `{"node":"cabin"|"outdoor","fw":N,...}`
lines with those keys is a node. A missing sensor is omitted, never zero.

The car itself may report outdoor temperature over OBD (`AMBIANT_AIR_TEMP`, the
**Outside** reading). It is used when the outdoor node has no temperature, always
labelled **From the car**, and quoted beside the sensor's reading when both exist
(the car's thermometer sits behind the bumper and reads high after a hot park).

## Install

```
omacar setup                                      # the venv, with pyserial
omacar env scan                                   # finds nodes on USB (3 s each)
systemctl --user enable --now omacar-env.service  # keeps the record
omacar env status                                 # what it knows now
```

Then put the **Environment** card on Home (Customize layout). There is also
`omacar env sim [--drive] [--co2 N] [--no-outdoor]`, a simulated pair of nodes
for seeing the screens. Its numbers say **Simulated** on the card, always, and
it keeps its history in its own file (`env-history-sim.jsonl`), never the real one.

State lives under `$XDG_STATE_HOME/omacar`: `env.json` (latest values and the
last ten minutes at 5 s) and `env-history.jsonl` (a row a minute for 24 hours).
Optional `~/.config/omacar/env-nodes.json`: `{"allow": ["vvvv:pppp"], "ports": ["/dev/ttyACM3"]}`
to admit a board whose USB vendor is not on the built-in list.

### The OBD adapter is never opened

`lib/envnode.py` probes a port only if its USB vendor is a node maker
(Raspberry Pi 2e8a, Espressif 303a, Adafruit 239a, Arduino 2341), it is not the
port the daemon resolved or published, no other process holds it open, and its
by-id name is not an OBD adapter's. FTDI, CH340 and CP210x (what OBD adapters
and USB-serial bridges are) are not on the list. It only reads; it never writes
a byte to a node. `omacar env scan` prints every port it skipped and why.
Because a Pico or ESP32-S3 shows up as `/dev/ttyACM*`, which sorts before
`ttyUSB0`, their USB ids are also on `connect.NOT_ADAPTERS`, so the daemon's
adapter finder does not offer the weather station to the ELM327 probe.

## What each number means

- **Temperature, humidity.** Dew point (Magnus formula, Sonntag 1990 constants)
  is the temperature at which the air would fog; the windscreen fogs when the glass
  is colder than the cabin's. Absolute humidity is grams of water per m³.
- **CO₂** (cabin): under 800 ppm *fresh*, 800 to 1,200 *fine*, 1,200 to 2,000
  *stuffy*, over 2,000 *high*. The band colours the headline number and the
  trail.
- **AQI** (outside): US EPA Air Quality Index for PM2.5 using the **2024 revised
  breakpoints** (EPA, "2024 AQI for Fine Particle Pollution" fact sheet): Good
  0 to 9.0 µg/m³, Moderate 9.1 to 35.4, Unhealthy for sensitive groups 35.5 to
  55.4, Unhealthy 55.5 to 125.4, Very unhealthy 125.5 to 225.4, Hazardous 225.5+.
  Computed from the **last ten minutes' mean**, not EPA's NowCast, and from a
  hobby-grade laser counter: indicative, not regulatory. Humid air makes it
  read high. The Hazardous band's top (325.4 µg/m³ is AQI 500) is from 40 CFR 58
  Appendix G, not the fact sheet.
- **Pressure trend.** The change over 3 hours, in hPa: rising or falling by 1 hPa
  or more, else steady. Pressure also tracks altitude (about 12 hPa per 100 m), so
  if the car moved at all in that window the trend reads **"trend unavailable
  while driving"**. With no car link it assumes parked and says so.

## The stale-air prompt

When **cabin CO₂ stays over 1,500 ppm for five minutes while the car is
moving**, the app says, once:

> Cabin air is stale (CO₂ 1,640 ppm). Switch to fresh air or crack a window.

- **Mechanism.** The gentlest the app has: a toast (`share/js/envprompt.js`),
  14 s at the corner, no modal, no banner, **no sound**. (The drowsy banner and
  cards are for a failing driver; a notify-send via `watch.py` was refused because
  the notification daemon decides whether it chimes. A child sleeps near the tablet.)
- **The clock** is `lib/envnode.py`'s `Record.tick_prompt`: it starts when CO₂ is over
  1,500 with the car over 3 km/h, restarts if CO₂ falls under 1,400 or if the conditions
  are absent for more than 30 s (a red light does not reset it). No car link: never.
- **Once per 20 minutes** (remembered in `localStorage`, so a reload does not repeat
  it; two screens open means each may say it once).
- **Never during a drowsy alert.** At any drowsy level above 0, with the banner
  showing, or running its test, it waits (still due) and speaks once the alert clears.

## What is unverified

- The firmware has **not been compiled** and no real SEN66, BME280 or PMS5003 has
  been run. The SEN66 calls were checked against Sensirion's repository, the PMS5003
  frame layout was not checked against a datasheet.
- Serial auto-detection has been tested against fake `/dev` and `/sys` trees and a
  fake port, **not on the tablet**. Opening an ESP32 over USB can reset it (DTR).
- The simulator's wiggles are not a sensor's noise; the charts have never seen a real day.
- The calm prompt has been seen in a browser against the simulator, not in a moving car.
- The CO₂ thresholds and the 5 minute rule are the owner's; the 1,400 clear line and
  the 30 s grace are choices to check.

## A ready-made cabin monitor instead of the cabin node

The cabin side can come from a monitor you buy rather than build. OmaCar
hears it over Bluetooth, with no pairing, no app link and no cloud
(`lib/envble.py`):

| Monitor | Gives the cabin | Notes |
|---|---|---|
| Qingping Air Monitor Lite, **CGDN1** | temperature, humidity, PM2.5, PM10, CO₂ | USB-powered; buy the original CGDN1, because support for the 2026 "Lite 2" is unconfirmed |
| Aranet4 | CO₂, temperature, humidity (its pressure is not used for the cabin) | Runs on AA batteries; turn on **Smart Home integrations** in the Aranet Home app (firmware 1.2.0+) |

1. `omacar fridge setup` (installs bleak, the Bluetooth library) if it is not
   already installed.
2. `omacar env scan` lists the monitors it hears, nearest first.
3. `omacar env use AA:BB:CC:DD:EE:FF` pins yours.
4. `systemctl --user restart omacar-env`.

**Only the pinned monitor is believed.** Every one in range broadcasts the
same way, including the car in the next bay. Readings go through the same
range checks as a USB node's, and the card's lamp names the device.

**Heat:** a parked car in the sun far exceeds what these are built for. Mount
the monitor low and out of direct sun, not on the dash.

The decoders are ported from qingping-ble and Aranet4-Python (both MIT). The
Qingping decoder is tested against qingping-ble's own captured Air Monitor
Lite advertisement. The Aranet4 decoder is tested against a frame built to
Aranet4-Python's layout, not against a real device.

