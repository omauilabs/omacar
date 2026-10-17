// OmaCar outdoor node: Bosch BME280 (T, RH, pressure) + Plantower PMS5003
// (PM1.0 / PM2.5 / PM10) -> one JSON line per second on USB serial.
//
//   {"node":"outdoor","fw":1,"t_c":17.9,"rh":63.0,"hpa":1012.6,"pm1":3.0,"pm25":6.1,"pm10":9.4}
//
// A sensor that is missing or not answering is OMITTED, never printed as zero.
//
// Target: Raspberry Pi Pico (RP2040), arduino-pico core. BME280 on I2C0
// (SDA GP4, SCL GP5, address 0x76 with SDO to GND, 0x77 with SDO to 3V3).
// PMS5003 on UART0 (Serial1): Pico TX GP0 -> PMS RX, Pico RX GP1 <- PMS TX.
// The PMS5003 is POWERED from 5 V (Pico VBUS) but its logic is 3.3 V, so its
// TX can go straight to the Pico RX and no level shifter is needed.
// Library: "Adafruit BME280 Library" (+ "Adafruit Unified Sensor").
//
// UNVERIFIED: written without a compiler or the hardware. The PMS5003 frame
// layout below is from memory of Plantower's datasheet: check it against the
// datasheet for the exact module you buy before trusting the numbers.

#include <Adafruit_BME280.h>
#include <Adafruit_Sensor.h>
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

static const int FW = 1;
static const uint8_t BME_ADDR = 0x76;

Adafruit_BME280 bme;
bool haveBme = false;

// PMS5003 active-mode frame: 32 bytes. 0x42 0x4D, length (28), then 13 big-endian
// words, then the checksum (sum of the first 30 bytes). Words 3..5 (bytes 10..15)
// are PM1.0 / PM2.5 / PM10 "atmospheric environment" in ug/m3, which is what the
// AQI is defined on (the "CF=1" words before them are a factory-calibration number).
struct Pms { bool ok; uint16_t pm1, pm25, pm10; uint32_t at; };
static Pms pms = {false, 0, 0, 0, 0};

static void pmsPoll() {
  static uint8_t buf[32];
  static uint8_t n = 0;
  while (Serial1.available()) {
    uint8_t b = Serial1.read();
    if (n == 0 && b != 0x42) continue;
    if (n == 1 && b != 0x4D) { n = (b == 0x42) ? 1 : 0; continue; }
    buf[n++] = b;
    if (n < 32) continue;
    n = 0;
    uint16_t sum = 0;
    for (int i = 0; i < 30; i++) sum += buf[i];
    if (sum != (uint16_t)((buf[30] << 8) | buf[31])) continue;   // torn frame: wait for the next
    pms.pm1 = (buf[10] << 8) | buf[11];
    pms.pm25 = (buf[12] << 8) | buf[13];
    pms.pm10 = (buf[14] << 8) | buf[15];
    pms.ok = true;
    pms.at = millis();
  }
}

static void kv(const char* key, float v, float lo, float hi, int decimals) {
  if (!isfinite(v) || v < lo || v > hi) return;
  Serial.print(",\"");
  Serial.print(key);
  Serial.print("\":");
  Serial.print(v, decimals);
}

void setup() {
  Serial.begin(115200);
  Serial.ignoreFlowControl(true);               // run with the tablet asleep
  Wire.begin();
  haveBme = bme.begin(BME_ADDR, &Wire);
  Serial1.setRX(1);
  Serial1.setTX(0);
  Serial1.begin(9600);                          // PMS5003's fixed rate
}

void loop() {
  pmsPoll();
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
  // A PMS reading older than 5 s (fan stopped, cable off) is not a reading.
  if (pms.ok && millis() - pms.at < 5000) {
    kv("pm1", pms.pm1, 0, 1000, 0);
    kv("pm25", pms.pm25, 0, 1000, 0);
    kv("pm10", pms.pm10, 0, 1000, 0);
  }
  Serial.println("}");
}
