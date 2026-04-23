import serial
import time
import glob
import json
import threading
import sys

# --- FIND PORTS AUTOMATICALLY ---
# Checks for both USB and ACM styles (common on Pi 5)
found_ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')

print(f"--- DETECTED {len(found_ports)} USB DEVICES ---")
for p in found_ports:
    print(f" > {p}")
print("---------------------------------------")

if len(found_ports) == 0:
    print("❌ ERROR: No USB devices found! Check power/cables.")
    sys.exit()

# Storage for status
port_status = {}

def serial_watcher(port_name):
    try:
        ser = serial.Serial(port_name, 115200, timeout=1)
        port_status[port_name] = {"connected": True, "id": "WAITING", "msgs": 0, "last_raw": ""}
    except Exception as e:
        port_status[port_name] = {"connected": False, "error": str(e)}
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line: continue
                
                # Update status
                port_status[port_name]["msgs"] += 1
                port_status[port_name]["last_raw"] = line
                
                # Try to parse ID
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        my_id = data.get("A", "UNKNOWN")
                        port_status[port_name]["id"] = my_id
                    except:
                        pass
        except:
            port_status[port_name]["connected"] = False
            break

# Start Threads
for p in found_ports:
    t = threading.Thread(target=serial_watcher, args=(p,), daemon=True)
    t.start()

# Live Dashboard Loop
print("\nScanning data... (Press Ctrl+C to stop)\n")

try:
    while True:
        time.sleep(1)
        # Clear screen (Linux/Windows compatible)
        sys.stdout.write("\033[H\033[J") 
        
        print(f"--- WIRED SYSTEM DIAGNOSTIC ({time.strftime('%H:%M:%S')}) ---")
        print(f"{'PORT':<15} | {'STATUS':<10} | {'ID DETECTED':<12} | {'MSG COUNT':<10} | {'LATEST DATA'}")
        print("-" * 90)
        
        for port in sorted(found_ports):
            if port in port_status:
                s = port_status[port]
                if s.get("connected"):
                    status = "✅ OPEN"
                    uid = s.get("id", "---")
                    count = s.get("msgs", 0)
                    raw = s.get("last_raw", "")[:40] # Truncate long lines
                    print(f"{port:<15} | {status:<10} | {uid:<12} | {count:<10} | {raw}")
                else:
                    err = s.get("error", "Error")
                    print(f"{port:<15} | ❌ FAIL    | {err}")
            else:
                print(f"{port:<15} | ⏳ INIT...")
        print("-" * 90)
        
except KeyboardInterrupt:
    print("\nStopping...")