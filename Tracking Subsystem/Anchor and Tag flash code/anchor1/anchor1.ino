/**
 * anchor1.ino — ESP32 UWB Anchor 1

 *
 * Library  : thotro/arduino-dw1000  (install "DW1000" via Library Manager)
 * Hardware : Makerfabs ESP32 UWB (large board)
 * Connect  : USB → Raspberry Pi 5 (any port — Pi auto-detects)
 *
 * Calibration is handled in uwb_host.py on the Pi — no changes needed here.
 */

#include <SPI.h>
#include <DW1000Ranging.h>

#define ANCHOR_ADDR   "01:00:5B:D5:A9:9A:E2:9C"
#define ANTENNA_DELAY 16550

#define SPI_SCK  18
#define SPI_MISO 19
#define SPI_MOSI 23
#define PIN_RST  27
#define PIN_IRQ  34
#define PIN_SS    4

void newRange();
void newBlink(DW1000Device *device);
void inactiveDevice(DW1000Device *device);

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("{\"event\":\"boot\",\"anchor\":\"1\"}");

    SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI);
    DW1000Ranging.initCommunication(PIN_RST, PIN_SS, PIN_IRQ);
    DW1000.setAntennaDelay(ANTENNA_DELAY);
    DW1000Ranging.attachNewRange(newRange);
    DW1000Ranging.attachBlinkDevice(newBlink);
    DW1000Ranging.attachInactiveDevice(inactiveDevice);
    DW1000Ranging.startAsAnchor(ANCHOR_ADDR, DW1000.MODE_SHORTDATA_FAST_ACCURACY, false);
}

void loop() {
    DW1000Ranging.loop();
}

void newRange() {
    Serial.printf("{\"anchor\":\"%X\",\"range\":%.4f,\"dbm\":%.1f}\n",
        DW1000Ranging.getDistantDevice()->getShortAddress(),
        DW1000Ranging.getDistantDevice()->getRange(),
        DW1000Ranging.getDistantDevice()->getRXPower()
    );
}

void newBlink(DW1000Device *device) {
    Serial.printf("{\"event\":\"blink\",\"addr\":\"%X\"}\n",
                  device->getShortAddress());
}

void inactiveDevice(DW1000Device *device) {
    Serial.printf("{\"event\":\"inactive\",\"addr\":\"%X\"}\n",
                  device->getShortAddress());
}
