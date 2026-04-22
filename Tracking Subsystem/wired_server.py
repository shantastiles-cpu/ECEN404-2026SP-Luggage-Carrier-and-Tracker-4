import serial
import json
import numpy as np
import time
import csv
import os
import threading
from datetime import datetime

# --- CONFIGURATION ---
# LIST ALL YOUR USB PORTS HERE!
# Pi Example: ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]
# Windows Example: ["COM3", "COM4", "COM5"]
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"] 
BAUD_RATE = 115200

# File Path
LOG_FILE_NAME = 'position_log_wired_raw.csv'

# --- GEOMETRY & CALIBRATION ---
# These IDs come from the Arduino code on the Reader units
ANCHOR_CONFIG = {
    "1782": [-0.204, 0.0],
    "1783": [0.204, 0.0],
    "1784": [0.0, 0.59]
}
ANCHOR_BIAS = { "1782": 0.0, "1783": 0.0, "1784": 0.0 }

# --- Z-AXIS ---
TAG_HEIGHT = 0.7
MIN_DIST = 0.01
MAX_DIST = 50.0

# --- GROUND TRUTH ---
TRUE_X = 0.0
TRUE_Y = 1.5
# --------------------

# Global data buffer
latest_distances = {}
data_lock = threading.Lock()

def trilaterate_2d(anchors, distances):
    anchors = np.array(anchors); distances = np.array(distances)
    try:
        p1 = anchors[0]; r1 = distances[0]
        A, b = [], []
        for i in range(1, len(anchors)):
            pi = anchors[i]; ri = distances[i]
            A.append(2 * (pi - p1))
            b.append(r1**2 - ri**2 - np.dot(p1, p1) + np.dot(pi, pi))
        pos, *_ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
        return np.array([pos[0], pos[1]])
    except: return None

# --- SERIAL WORKER THREAD ---
def serial_reader(port_name):
    """Reads from one USB port and updates the global distance list"""
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        print(f"✅ Connected to {port_name}")
    except Exception as e:
        print(f"❌ Failed to open {port_name}: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line.startswith("{"): continue
                
                try:
                    data = json.loads(line)
                    addr = data.get("A")
                    dist = float(data.get("R"))
                    
                    # Store latest distance for this anchor
                    with data_lock:
                        latest_distances[addr] = {
                            "dist": dist,
                            "time": time.time()
                        }
                except: pass
        except: break

# --- MAIN LOOP ---
def main():
    print(">>> STARTING WIRED SERVER (RAW OUTPUT ONLY) <<<")
    
    # 1. Start Serial Threads
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()

    # 2. Setup Logging
    try:
        f = open(LOG_FILE_NAME, 'a', newline='')
        writer = csv.writer(f)
        writer.writerow(["timestamp", "x_raw", "y_raw", "angle", "dist_origin", "error_m"])
    except: pass
    
    print("Waiting for data from USB...")
    
    while True:
        time.sleep(0.1) # 10Hz Update Loop
        
        current_anchors = []
        current_distances = []
        now = time.time()
        
        with data_lock:
            # Check all known anchors
            for addr, info in latest_distances.items():
                # Only use data less than 0.5s old (Stale data = drift)
                if (now - info["time"]) < 0.5:
                    if addr in ANCHOR_CONFIG:
                        bias = ANCHOR_BIAS.get(addr, 0.0)
                        d_raw = info["dist"] - bias
                        
                        # Height Comp
                        if d_raw > TAG_HEIGHT:
                            d_horiz = np.sqrt(d_raw**2 - TAG_HEIGHT**2)
                            if MIN_DIST < d_horiz < MAX_DIST:
                                current_anchors.append(ANCHOR_CONFIG[addr])
                                current_distances.append(d_horiz)
                        else:
                            current_anchors.append(ANCHOR_CONFIG[addr])
                            current_distances.append(0.01)

        # RAW CALCULATION ONLY (No Filter)
        if len(current_anchors) >= 3:
            pos = trilaterate_2d(current_anchors, current_distances)
            
            if pos is not None:
                x, y = pos[0], pos[1]
                
                # Metrics
                angle = np.degrees(np.arctan2(x, y))
                dist = np.sqrt(x**2 + y**2)
                error = np.sqrt((x - TRUE_X)**2 + (y - TRUE_Y)**2)
                
                print(f"Raw Pos: ({x:.2f}, {y:.2f}) | Angle: {angle:.1f}° | Error: {error:.2f}m")
                
                try:
                    writer.writerow([datetime.now().isoformat(), x, y, angle, dist, error])
                    f.flush()
                except: pass
        else:
            # Optional: Visualize waiting status
            # print(f"Anchors visible: {len(current_anchors)}/3")
            pass

if __name__ == '__main__':
    main()