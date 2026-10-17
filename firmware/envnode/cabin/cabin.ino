// OmaCar cabin node: Sensirion SEN66 -> one JSON line per second on USB serial.
//
//   {"node":"cabin","fw":1,"t_c":22.4,"rh":41.2,"co2":812,"pm1":3.1,
//    "pm25":4.0,"pm4":4.6,"pm10":5.2,"voc":102,"nox":1}
//
// A value the sensor cannot give yet (CO2 and NOx are not valid for the first
// seconds after power-up) is OMITTED, never printed as zero. lib/envnode.py on
// the tablet drops anything outside the quantity's physical range too.
//
// Target: Raspberry Pi Pico (RP2040) with the arduino-pico core
// (https://github.com/earlephilhower/arduino-pico). I2C0 on its default pins,
// SDA GP4 and SCL GP5. Library: "Sensirion I2C SEN66" (Library Manager), which
// pulls in "Sensirion Core".
//
// UNVERIFIED: this was written without a compiler or the hardware. Build it
// and read the output before trusting it. See firmware/envnode/README.md.

#include <Arduino.h>
#include <SensirionI2cSen66.h>
#include <Wire.h>
#include <math.h>

static const int FW = 1;

SensirionI2cSen66 sensor;

// Print ,"key":value only when value is a real number inside [lo, hi].
static void kv(const char* key, float v, float lo, float hi, int decimals) {
  if (!isfinite(v) || v < lo || v > hi) return;
  Serial.print(",\"");
  Serial.print(key);
  Serial.print("\":");
  Serial.print(v, decimals);
}

void setup() {
  Serial.begin(115200);
  // Do not wait for a host: the node must run with the tablet asleep.
  Serial.ignoreFlowControl(true);
  Wire.begin();
  sensor.begin(Wire, SEN66_I2C_ADDR_6B);
  sensor.deviceReset();
  delay(1200);                                   // the reset takes ~1.1 s (Sensirion's example waits 1200 ms)
  sensor.startContinuousMeasurement();
  delay(1100);
}

void loop() {
  static uint32_t next = 0;
  if ((int32_t)(millis() - next) < 0) return;
  next = millis() + 1000;

  float pm1, pm25, pm4, pm10, rh, t, voc, nox;
  uint16_t co2 = 0;
  int16_t err = sensor.readMeasuredValues(pm1, pm25, pm4, pm10, rh, t, voc, nox, co2);
  if (err != 0) return;                          // say nothing rather than something wrong

  Serial.print("{\"node\":\"cabin\",\"fw\":");
  Serial.print(FW);
  kv("t_c", t, -40, 85, 2);
  kv("rh", rh, 0, 100, 1);
  if (co2 >= 300 && co2 <= 10000) { Serial.print(",\"co2\":"); Serial.print(co2); }
  kv("pm1", pm1, 0, 1000, 1);
  kv("pm25", pm25, 0, 1000, 1);
  kv("pm4", pm4, 0, 1000, 1);
  kv("pm10", pm10, 0, 1000, 1);
  kv("voc", voc, 1, 500, 0);
  kv("nox", nox, 1, 500, 0);
  Serial.println("}");
}
