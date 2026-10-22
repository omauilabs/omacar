# OmaCar environment nodes: firmware

Two small boards on USB, each printing **one JSON line per second**. The tablet
side is `lib/envnode.py` (see `doc/environment.md`). It does not care which
sensors are behind the board: any device that prints these lines is a node.

```
{"node":"cabin","fw":1,"t_c":22.4,"rh":41.2,"co2":812,"pm1":3.1,"pm25":4.0,"pm4":4.6,"pm10":5.2,"voc":102,"nox":1}
{"node":"outdoor","fw":1,"t_c":17.9,"rh":63.0,"hpa":1012.6,"pm1":3,"pm25":6,"pm10":9}
```

A sensor that is missing or not ready is **omitted**, never printed as zero.

## STATUS: UNVERIFIED

Nothing in this directory has been compiled or run. `arduino-cli` was not
installed where it was written and the hardware is not bought. Treat it as a
starting point: build it, open the serial monitor at 115200 and read the lines
before wiring it into a car.

What *was* checked, against Sensirion's repository
(<https://github.com/Sensirion/arduino-i2c-sen66>, `master`,
`examples/exampleUsage/exampleUsage.ino` and `src/SensirionI2cSen66.h`):
library name "Sensirion I2C SEN66" (version 1.3.1 in `library.properties`,
depends on "Sensirion Core"), header `SensirionI2cSen66.h`, class
`SensirionI2cSen66`, address `SEN66_I2C_ADDR_6B` (0x6B), and
`readMeasuredValues(float& pm1p0, float& pm2p5, float& pm4p0, float& pm10p0, float& humidity, float& temperature, float& vocIndex, float& noxIndex, uint16_t& co2)`.
The repository's README still shows an older header spelling
(`SensirionI2CSen66.h`); the header in `src/` and the example are the
authority. The BME280 calls come from Adafruit's `bme280test.ino`
(`begin(addr, &Wire)`, `readTemperature()`, `readHumidity()`,
`readPressure() / 100.0F`). The PMS5003 frame layout is from memory of the
Plantower datasheet and is flagged as such in the sketch.

## Build (Raspberry Pi Pico, RP2040)

1. Arduino IDE or `arduino-cli`, with the **arduino-pico** core
   (<https://github.com/earlephilhower/arduino-pico>), board "Raspberry Pi Pico".
2. Libraries: cabin node "Sensirion I2C SEN66" (pulls "Sensirion Core");
   outdoor node "Adafruit BME280 Library" and "Adafruit Unified Sensor".
3. Board "Adafruit QT Py RP2040" for the recommended build (below), or "Raspberry
   Pi Pico" for the bare-Pico wiring, with `BUS` set to `Wire` in the sketch.
   The outdoor node also needs "Adafruit PM25 AQI Sensor".
4. Open `cabin/cabin.ino` or `outdoor/outdoor.ino`, upload.

An ESP32-S3 (native USB) will also work with small changes (`Wire.begin(SDA, SCL)`,
a hardware UART for the PMS5003, no `ignoreFlowControl`). Prefer a board with
**native USB** (RP2040, ESP32-S3): `lib/envnode.py` only opens ports whose USB
vendor is a known board maker, and it will never open an FTDI/CH340/CP210x
adapter, which is what OBD adapters (and USB-serial bridges) look like.

## Wiring: cabin node (Pico + SEN66)

| SEN66 | Pico | Note |
|---|---|---|
| VDD | 3V3(OUT), pin 36 | **3.3 V**, not 5 V. Check the SEN66 datasheet's supply current against what the Pico's regulator can give; if it is marginal, use a separate 3.3 V regulator with a common ground. |
| GND | GND | |
| SDA | GP4 (I2C0 SDA), pin 6 | 3.3 V logic |
| SCL | GP5 (I2C0 SCL), pin 7 | 3.3 V logic |

Do not wire the SEN66's connector from this table alone: **take its pin order
from Sensirion's datasheet**. The I2C pull-ups (typically 4.7 to 10 k to 3.3 V on
SDA and SCL, one set on the bus) are specified there too. Many breakouts carry
them; adding a second set is harmless, adding none is not.

## Wiring: outdoor node (Pico + BME280 + PMS5003)

| Part pin | Pico | Note |
|---|---|---|
| BME280 VIN/VCC | 3V3(OUT), pin 36 | 3.3 V |
| BME280 GND | GND | |
| BME280 SDA | GP4, pin 6 | breakout boards (Adafruit, SparkFun) include pull-ups |
| BME280 SCL | GP5, pin 7 | |
| BME280 SDO | GND | address 0x76 (to 3V3 for 0x77; change `BME_ADDR`) |
| PMS5003 VCC | **VBUS, pin 40 (5 V)** | **the fan and laser need 5 V**: about 100 mA running |
| PMS5003 GND | GND | |
| PMS5003 TX | GP1 (UART0 RX), pin 2 | PMS logic is **3.3 V**: straight to the Pico, no level shifter |
| PMS5003 RX | GP0 (UART0 TX), pin 1 | optional; the sketch never sends |
| PMS5003 SET, RESET | leave open | pulled high inside the module: always on |

Common ground on everything. The PMS5003's 5 V comes from USB VBUS, so the
cable must be a real data-and-power one.

## Where to put them

**Outdoor node**
- The BME280 reads the air, not the sun. Put it in a **ventilated radiation
  shield** (stacked-plate "Stevenson" style), on the shaded side, away from the
  roof, exhaust, the engine bay and any dark metal. A bare board in a sealed
  box reads 10 °C high in sun and says it with great confidence.
- Keep it **away from the PMS5003**: the fan and electronics warm their own air.
  Separate chambers, the thermometer upstream of the fan's outlet.
- The PMS5003 needs an air path in and out and **no water**. A downward-facing
  inlet under a hood; never a sealed box. Rain and fog make laser sensors read
  high (water droplets scatter like dust): treat PM above ~80 % RH with suspicion.
- Typical homes: a roof-rack or trailer-hitch pod, or a shaded wheel-arch
  liner top. Cable the USB into the cabin through an existing grommet.
- Pressure drifts with the altitude of the car, not just weather; the app shows
  a trend only while parked.

**Cabin node**
- A spot with real air movement, not in the footwell next to a heater duct or
  behind the tablet's own vents, and not in direct sun. Near the centre
  console or rear-view mirror stem is typical.
- The SEN66's CO2 needs a few minutes to settle after power-up (readings that
  are not valid yet are omitted), and its temperature includes some self-heating:
  trust the trend more than the decimal.

## Parts: the recommended, solder-free build

Every part plugs together with STEMMA QT / Qwiic cables. Product numbers were
checked on adafruit.com on 2026-10-17.

**Cabin node:**

| Part | Where | Notes |
|---|---|---|
| Sensirion **SEN66** | DigiKey or Mouser (Sensirion part SEN66-SIN-T) | Adafruit does not sell the sensor itself |
| Adafruit SEN6x Breakout | Adafruit **#6331** | Adds a 3.3 V regulator, level shifting and STEMMA QT; comes with neither the sensor nor the cable |
| JST GH 6-pin cable, 100 mm | Adafruit **#5754** | Joins the SEN66 to the breakout |
| Adafruit **QT Py RP2040** | Adafruit **#4900** | USB-C to the tablet; the STEMMA QT port is `Wire1` |
| STEMMA QT cable | Adafruit | Joins the breakout to the QT Py |

**Outdoor node:**

| Part | Where | Notes |
|---|---|---|
| Adafruit **BME280** (STEMMA QT) | Adafruit **#2652** | Address 0x77; the sketch also tries 0x76 |
| Adafruit **PMSA003I** air-quality breakout | Adafruit **#4632** | The I²C Plantower, with two STEMMA QT ports: no UART or level shifting |
| Adafruit **QT Py RP2040** | Adafruit **#4900** | |
| Two STEMMA QT cables | Adafruit | QT Py to BME280 to PMSA003I, daisy-chained |
| A small stacked-plate radiation shield | any weather-station supplier | For the BME280. Without one, sun on the car reads as heat |
| A small vented outdoor box | | For the PMSA003I: openings face down, and the fan inlet away from the exhaust and the engine bay |
| A long USB-C data cable, a flat door-seal pass-through or grommet, and strain relief | | The QT Py stays dry inside the box; only USB comes in |

**Shared:** a **powered USB hub**. The Surface has two ports and the OBD adapter
and cameras already use them. Powered from the ignition-switched inverter, it
also becomes a possible way to wake the tablet when the car starts (see
doc/ignition.md).

**Already in the car:** the car's own outdoor temperature over OBD. It stands
in until the outdoor node exists, and stays as a cross-check after.

The older wiring (a bare Pico, a UART PMS5003 on GP1) still works: set `BUS` to
`Wire` and `PM_UART` to 1 in the sketches.
