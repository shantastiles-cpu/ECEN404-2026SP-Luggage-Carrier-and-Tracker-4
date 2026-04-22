import serial
import time
import glob

print(">>> SCANNING ALL USB PORTS... <<<")

# Find all potential ports
ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')

if not ports:
    print(" No USB devices found! Check cables/power.")
    exit()

print(f"Found {len(ports)} devices: {ports}")

# Try to open all of them
open_ports = []
for p in ports:
    try:
        s = serial.Serial(p, 115200, timeout=0.1)
        open_ports.append(s)
        print(f" Opened {p}")
    except Exception as e:
        print(f" Could not open {p}: {e}")

print("\nListening for data (Press Ctrl+C to stop)...")

while True:
    for ser in open_ports:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if len(line) > 0:
                    # Print WHICH port sent the data
                    print(f"[{ser.port}] -> {line}")
        except: pass
    time.sleep(0.01)
