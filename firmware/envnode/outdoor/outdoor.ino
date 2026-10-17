// OmaCar outdoor node: Bosch BME280 (T, RH, pressure) + a Plantower particulate
// sensor (PM1.0 / PM2.5 / PM10) -> one JSON line per second on USB serial.
//
//   {"node":"outdoor","fw":2,"t_c":17.9,"rh":63.0,"hpa":1012.6,"pm1":3,"pm25":6,"pm10":9}
//
// A sensor that is missing or not answering is OMITTED, never printed as zero.
//
// THE RECOMMENDED BUILD IS SOLDER-FREE (firmware/envnode/README.md): an Adafruit
// QT Py RP2040, an Adafruit BME280 and an Adafruit PMSA003I, all daisy-chained
// on STEMMA QT cables. On the QT Py RP2040 that connector is I2C1, which the
// arduino-pico core calls Wire1 (Adafruit's pinout page: "In Arduino, it is
// Wire1"). A bare Raspberry Pi Pico with the sensors on GP4/GP5 uses Wire:
// change BUS below.
//
// The particulate sensor is read through Adafruit's PM25AQI library, which
// speaks both the I2C PMSA003I and the UART PMS5003 (set PM_UART to 1 for the
// latter, on Serial1, GP1 = RX). API checked against Adafruit_PM25AQI
// src/Adafruit_PM25AQI.h on 2026-10-17: begin_I2C(TwoWire*), begin_UART(Stream*),
// read(PM25_AQI_Data*), fields pm10_env / pm25_env / pm100_env -- the
// "environmental" (atmospheric) numbers, which are what the AQI is defined on.
//
// Libraries: "Adafruit BME280 Library", "Adafruit Unified Sensor",
// "Adafruit PM25 AQI Sensor". Board: arduino-pico, "Adafruit QT Py RP2040".
//
// UNVERIFIED: not yet compiled or run on hardware.

#include <Adafruit_BME280.h>
#include <Adafruit_PM25AQI.h>
#include <Adafruit_Sensor.h>
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#define BUS Wire1          // QT Py RP2040's STEMMA QT port; Wire on a bare Pico
#define PM_UART 0          // 1: a PMS5003 on Serial1 instead of an I2C PMSA003I

static const int FW = 2;

Adafruit_BME280 bme;
Adafruit_PM25AQI aqi;
bool haveBme = false, haveAqi = false;

static void kv(const char* key, float v, float lo, float hi, int decimals) {
  if (!isfinite(v) || v < lo || v > hi) return;
  Serial.print(",\"");
  Serial.print(key);
  Serial.print("\":");
  Serial.print(v, decimals);
}

void setup() {
  Serial.begin(115200);
  Serial.ignoreFlowControl(true);               // keep running with the tablet asleep
  BUS.begin();
  // Adafruit's breakout answers at 0x77; most others at 0x76.
  haveBme = bme.begin(0x77, &BUS) || bme.begin(0x76, &BUS);
  delay(3000);                                   // the PM sensor's fan needs a moment
#if PM_UART
  Serial1.setRX(1);
  Serial1.begin(9600);
  haveAqi = aqi.begin_UART(&Serial1);
#else
  haveAqi = aqi.begin_I2C(&BUS);
#endif
}

void loop() {
  static uint32_t next = 0;
  if ((int32_t)(millis() - next) < 0) return;
  next = millis() + 1000;

  Serial.print("{\"node\":\"outdoor\",\"fw\":");
  Serial.print(FW);
  if (haveBme) {
    kv("t_c", bme.readTemperature(), -40, 85, 2);
    kv("rh", bme.readHumidity(), 0, 100, 1);
    kv("hpa", bme.readPressure() / 100.0F, 300, 1100, 2);
  }
  PM25_AQI_Data d;
  // A failed read (fan stopped, cable off, torn frame) prints nothing for PM.
  if (haveAqi && aqi.read(&d)) {
    kv("pm1", d.pm10_env, 0, 1000, 0);
    kv("pm25", d.pm25_env, 0, 1000, 0);
    kv("pm10", d.pm100_env, 0, 1000, 0);
  }
  Serial.println("}");
}
