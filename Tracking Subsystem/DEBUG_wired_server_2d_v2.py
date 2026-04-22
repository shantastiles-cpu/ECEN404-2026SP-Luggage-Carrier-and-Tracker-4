import serial
import json
import numpy as np
import time
import csv
import threading
import sys
from datetime import datetime
from collections import deque

# --- CONFIGURATION ---
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1"] 
BAUD_RATE = 115200

# File Path
LOG_FILE_NAME = 'position_log_2anchor_filtered.csv'

# --- 2 ANCHOR GEOMETRY ---
ANCHOR_CONFIG = {
    "1781": [-0.204, 0.0],
    "1782": [ 0.204, 0.0]
}

ANCHOR_BIAS = { "1781": 0.0, "1782": 0.0 }
TAG_HEIGHT = 0.0

# --- FILTER SETTINGS ---
FILTER_WINDOW = 5 
MAX_X_WIDTH = 5.0
MAX_Y_DEPTH = 10.0

# --- GLOBAL DATA ---
latest_distances = {}
data_lock = threading.Lock()
pos_history = deque(maxlen=FILTER_WINDOW)

def get_circle_intersection(p1, r1, p2, r2):
    d = np.linalg.norm(p2 - p1)
    if d > r1 + r2: return None
    if d < abs(r1 - r2): return None
    if d == 0: return None
    
    a = (r1**2 - r2**2 + d**2) / (2*d)
    h = np.sqrt(max(0, r1**2 - a**2))
    
    x2 = p1[0] + a * (p2[0] - p1[0]) / d
    y2 = p1[1] + a * (p2[1] - p1[1]) / d
    
    x3_a = x2 + h * (p2[1] - p1[1]) / d
    y3_a = y2 - h * (p2[0] - p1[0]) / d
    x3_b = x2 - h * (p2[1] - p1[1]) / d
    y3_b = y2 + h * (p2[0] - p1[0]) / d
    
    if y3_a > y3_b:
        return np.array([x3_a, y3_a])
    else:
        return np.array([x3_b, y3_b])


# --- SERIAL THREAD ---
def serial_reader(port_name):
    # ✅ FIX 3: Catch and report connection failures per port
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        print(f"✅ Connected to {port_name}")
    except Exception as e:
        print(f"❌ FAILED to connect to {port_name}: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line.startswith("{"): continue
                try:
                    data = json.loads(line)

                    addr = data.get("A")
                    dist = data.get("R")

                    # ✅ FIX 2: Catch missing fields in the JSON packet
                    if addr is None or dist is None:
                        print(f"[{port_name}] ⚠️  Malformed packet (missing A or R): {data}")
                        continue

                    dist = float(dist)

                    # ✅ FIX 2: Catch unknown anchor IDs (firmware ID mismatch)
                    if addr not in ANCHOR_CONFIG:
                        print(f"[{port_name}] ⚠️  Unknown anchor ID '{addr}' — not in ANCHOR_CONFIG. Check firmware!")
                        continue

                    # ✅ FIX 1: Per-port print so you can confirm two distinct IDs are coming in
                    print(f"[{port_name}] Anchor: {addr}, Dist: {dist:.3f}m")

                    with data_lock:
                        latest_distances[addr] = {
                            "dist": dist,
                            "time": time.time()
                        }

                except Exception as e:
                    print(f"[{port_name}] ⚠️  JSON parse error: {e} | Raw line: {line}")

        except Exception as e:
            print(f"[{port_name}] ❌ Serial read error: {e}")
            break


# --- MAIN LOOP ---
def main():
    print(">>> STARTING 2-ANCHOR TRACKING (FILTERED) <<<")
    
    try:
        f = open(LOG_FILE_NAME, 'a', newline='')
        writer = csv.writer(f)
        writer.writerow(["timestamp", "x_filt", "y_filt", "angle", "x_raw", "y_raw"])
        print(f"✅ Logging to {LOG_FILE_NAME}")
    except Exception as e:
        print(f"❌ Could not open log file: {e}")
        writer = None
    
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()

    print("Waiting for data...")

    last_diagnostic_time = time.time()

    while True:
        time.sleep(0.05)
        
        now = time.time()

        # ✅ FIX 1 & 3: Every 3 seconds, print a live snapshot of what anchors are active
        # This tells you immediately if one anchor is silent or both are the same ID
        if now - last_diagnostic_time >= 3.0:
            with data_lock:
                if latest_distances:
                    print("\n--- Anchor Snapshot ---")
                    for anchor_id, info in latest_distances.items():
                        age = now - info["time"]
                        status = "✅ LIVE" if age < 0.5 else f"⚠️  STALE ({age:.1f}s ago)"
                        print(f"  Anchor {anchor_id}: {info['dist']:.3f}m | {status}")
                    # ✅ FIX 1: Warn explicitly if both anchors report the same distance
                    ids = list(latest_distances.keys())
                    if len(ids) == 2:
                        d1 = latest_distances[ids[0]]["dist"]
                        d2 = latest_distances[ids[1]]["dist"]
                        if abs(d1 - d2) < 0.01:
                            print("  ⚠️  WARNING: Both anchors report nearly identical distances!")
                            print("      This will lock x=0.00. Check ports or anchor firmware IDs.")
                    elif len(ids) < 2:
                        print("  ⚠️  WARNING: Only seeing 1 anchor. Check second port/anchor.")
                    print("-----------------------\n")
            last_diagnostic_time = now

        with data_lock:
            ids = list(ANCHOR_CONFIG.keys())
            id1, id2 = ids[0], ids[1]
            
            if id1 in latest_distances and id2 in latest_distances:
                info1 = latest_distances[id1]
                info2 = latest_distances[id2]
                
                if (now - info1["time"] < 0.5) and (now - info2["time"] < 0.5):
                    
                    p1 = np.array(ANCHOR_CONFIG[id1])
                    p2 = np.array(ANCHOR_CONFIG[id2])
                    
                    r1_raw = info1["dist"] - ANCHOR_BIAS.get(id1, 0)
                    r2_raw = info2["dist"] - ANCHOR_BIAS.get(id2, 0)
                    
                    r1 = np.sqrt(max(0.01, r1_raw**2 - TAG_HEIGHT**2))
                    r2 = np.sqrt(max(0.01, r2_raw**2 - TAG_HEIGHT**2))
                    
                    raw_pos = get_circle_intersection(p1, r1, p2, r2)
                    
                    if raw_pos is not None:
                        if abs(raw_pos[0]) > MAX_X_WIDTH or raw_pos[1] > MAX_Y_DEPTH:
                            print(f"  Outlier ignored: ({raw_pos[0]:.2f}, {raw_pos[1]:.2f})")
                            continue

                        pos_history.append(raw_pos)
                        
                        x_hist = [p[0] for p in pos_history]
                        y_hist = [p[1] for p in pos_history]
                        
                        x_filt = np.median(x_hist)
                        y_filt = np.median(y_hist)
                        
                        angle_deg = np.degrees(np.arctan2(x_filt, y_filt))
                        
                        print(f"Pos: ({x_filt:.2f}, {y_filt:.2f}) | Angle: {angle_deg:.1f}°")
                        
                        if writer:
                            try:
                                writer.writerow([datetime.now().isoformat(), x_filt, y_filt, angle_deg, raw_pos[0], raw_pos[1]])
                                f.flush()
                            except Exception as e:
                                print(f"❌ Log write error: {e}")

if __name__ == '__main__':
    main()