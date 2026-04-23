/**
 * tag.ino — ESP32 UWB Tag (mobile node)
 *
 * Library  : thotro/arduino-dw1000  (install "DW1000" via Library Manager)
 * Hardware : Makerfabs ESP32 UWB (large board)
 * Connect  : Battery / standalone — no USB to Pi needed
 *
 * Calibration is handled in uwb_host.py on the Pi — no changes needed here.
 */

#include <SPI.h>
#include <DW1000Ranging.h>

#define TAG_ADDR      "7D:00:22:EA:82:60:3B:9C"
#define ANTENNA_DELAY 16384

#define SPI_SCK  18
#define SPI_MISO 19
#define SPI_MOSI 23
#define PIN_RST  27
#define PIN_IRQ  34
#define PIN_SS    4

void newRange();
void newDevice(DW1000Device *device);
void inactiveDevice(DW1000Device *device);

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("{\"event\":\"boot\",\"node\":\"tag\"}");

    SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI);
    DW1000Ranging.initCommunication(PIN_RST, PIN_SS, PIN_IRQ);
    DW1000.setAntennaDelay(ANTENNA_DELAY);
    DW1000Ranging.attachNewRange(newRange);
    DW1000Ranging.attachNewDevice(newDevice);
    DW1000Ranging.attachInactiveDevice(inactiveDevice);
    DW1000Ranging.startAsTag(TAG_ADDR, DW1000.MODE_SHORTDATA_FAST_ACCURACY);
}

void loop() {
    DW1000Ranging.loop();
}

void newRange() {
    Serial.printf("{\"anchor\":\"%X\",\"range\":%.4f,\"dbm\":%.1f}\n",
                  DW1000Ranging.getDistantDevice()->getShortAddress(),
                  DW1000Ranging.getDistantDevice()->getRange(),
                  DW1000Ranging.getDistantDevice()->getRXPower());
}

void newDevice(DW1000Device *device) {
    Serial.printf("{\"event\":\"new_anchor\",\"addr\":\"%X\"}\n",
                  device->getShortAddress());
}

void inactiveDevice(DW1000Device *device) {
    Serial.printf("{\"event\":\"lost_anchor\",\"addr\":\"%X\"}\n",
                  device->getShortAddress());
}
