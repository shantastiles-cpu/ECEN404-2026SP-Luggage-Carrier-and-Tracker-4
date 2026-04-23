
import time
import RPi.GPIO as GPIO
from hx711 import HX711

REFERENCE_UNIT = 87  # your calibration value

try:
    GPIO.setwarnings(False)

    hx = HX711(5, 6)  # DT=GPIO5, SCK=GPIO6
    hx.set_reading_format("MSB", "MSB")
    hx.set_reference_unit(REFERENCE_UNIT)
    hx.reset()

    print("Remove all weight. Taring in 2 seconds...")
    time.sleep(2)
    hx.tare()
    print("Tare done. Reading grams (Ctrl+C to stop)")

    while True:
        val = hx.get_weight(5)
        print(f"{val:8.1f} g")
        hx.power_down()
        hx.power_up()
        time.sleep(0.2)

except KeyboardInterrupt:
    print("\nStopped.")

finally:
    try:
        GPIO.cleanup()
    except:
        pass
PY
