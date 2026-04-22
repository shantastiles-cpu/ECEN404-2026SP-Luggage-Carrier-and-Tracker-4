import serial
import json
import time
import threading

# --- CONFIGURATION ---
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]
BAUD_RATE = 115200

# Expected anchors
EXPECTED_ANCHORS = ["1781", "1782", "1783"]

# Global data
port_data = {}
data_lock = threading.Lock()

def serial_reader(port_name):
    """Monitor a single port"""
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        print(f"✅ Successfully opened {port_name}")
    except Exception as e:
        print(f"❌ FAILED to open {port_name}: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        addr = str(data.get("A", "")).strip()
                        dist = float(data.get("R", 0))
                        
                        with data_lock:
                            if port_name not in port_data:
                                port_data[port_name] = {
                                    "anchor_id": addr,
                                    "last_distance": dist,
                                    "message_count": 0,
                                    "last_update": time.time()
                                }
                            
                            port_data[port_name]["last_distance"] = dist
                            port_data[port_name]["message_count"] += 1
                            port_data[port_name]["last_update"] = time.time()
                            
                    except:
                        pass
        except:
            break

def main():
    print("="*70)
    print("ESP32 UWB ANCHOR DIAGNOSTIC TOOL")
    print("="*70)
    print(f"\nExpected anchors: {EXPECTED_ANCHORS}")
    print(f"Checking ports: {SERIAL_PORTS}\n")
    
    # Start threads
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()
    
    print("Monitoring for 10 seconds...\n")
    
    # Monitor for 10 seconds
    for i in range(100):
        time.sleep(0.1)
        
        # Print status every second
        if i % 10 == 0:
            print(f"\n--- Status at {i/10:.0f} seconds ---")
            
            with data_lock:
                if not port_data:
                    print("⚠️  NO DATA RECEIVED FROM ANY PORT!")
                else:
                    for port in SERIAL_PORTS:
                        if port in port_data:
                            info = port_data[port]
                            age = time.time() - info["last_update"]
                            print(f"{port}:")
                            print(f"  Anchor ID: {info['anchor_id']}")
                            print(f"  Last distance: {info['last_distance']:.2f}m")
                            print(f"  Messages: {info['message_count']}")
                            print(f"  Last update: {age:.1f}s ago")
                        else:
                            print(f"{port}: ❌ NO DATA")
    
    # Final summary
    print("\n" + "="*70)
    print("FINAL DIAGNOSTIC SUMMARY")
    print("="*70)
    
    found_anchors = set()
    missing_ports = []
    
    with data_lock:
        for port in SERIAL_PORTS:
            if port in port_data:
                anchor_id = port_data[port]["anchor_id"]
                found_anchors.add(anchor_id)
                print(f"✅ {port} → Anchor {anchor_id}")
            else:
                missing_ports.append(port)
                print(f"❌ {port} → NO DATA RECEIVED")
    
    print("\nAnchor Status:")
    for expected in EXPECTED_ANCHORS:
        if expected in found_anchors:
            print(f"  ✅ Anchor {expected} - FOUND")
        else:
            print(f"  ❌ Anchor {expected} - MISSING")
    
    if missing_ports:
        print(f"\n⚠️  PROBLEM: No data from ports: {missing_ports}")
        print("\nTroubleshooting steps:")
        print("1. Check if the USB cable is properly connected")
        print("2. Verify the ESP32 module is powered on")
        print("3. Check if the module is configured as an anchor (not tag)")
        print("4. Try unplugging and replugging the USB connection")
        print("5. Run 'ls -l /dev/ttyUSB*' to verify the port exists")
    
    if len(found_anchors) < 3:
        print(f"\n⚠️  WARNING: Only {len(found_anchors)}/3 anchors detected!")
        print("You need all 3 anchors for trilateration to work.")
    else:
        print("\n✅ All 3 anchors detected successfully!")

if __name__ == '__main__':
    main()
