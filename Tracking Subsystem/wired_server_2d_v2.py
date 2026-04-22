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
# UPDATE THESE PORTS!
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1"] 
BAUD_RATE = 115200

# File Path
LOG_FILE_NAME = 'position_log_2anchor_filtered.csv'

# --- 2 ANCHOR GEOMETRY ---
ANCHOR_CONFIG = {
    "1781": [-0.204, 0.0],  # Left Anchor
    "1782": [ 0.204, 0.0]   # Right Anchor
}

# Calibration
ANCHOR_BIAS = { "1781": 0.0, "1782": 0.0 }
TAG_HEIGHT = 0.0 # Set to 0.7 for walking

# --- FILTER SETTINGS (NEW) ---
# 1. Median Filter: Takes the middle value of the last N frames.
#    Removes spikes completely. Higher number = smoother but more lag.
FILTER_WINDOW = 5 

# 2. Room Bounds: Ignore any result outside this box (meters).
MAX_X_WIDTH = 5.0   # Ignore if x is > 5m or < -5m
MAX_Y_DEPTH = 10.0  # Ignore if y is > 10m

# --- GLOBAL DATA ---
latest_distances = {}
data_lock = threading.Lock()
pos_history = deque(maxlen=FILTER_WINDOW)

def get_circle_intersection(p1, r1, p2, r2):
    """
    Finds intersection of two circles.
    Returns the point with Positive Y (In front of wall).
    """
    d = np.linalg.norm(p2 - p1)
    
    if d > r1 + r2: return None # Circles too far apart
    if d < abs(r1 - r2): return None # One circle inside other
    if d == 0: return None
    
    a = (r1**2 - r2**2 + d**2) / (2*d)
    h = np.sqrt(max(0, r1**2 - a**2))
    
    x2 = p1[0] + a * (p2[0] - p1[0]) / d
    y2 = p1[1] + a * (p2[1] - p1[1]) / d
    
    # Two possible solutions
    x3_a = x2 + h * (p2[1] - p1[1]) / d
    y3_a = y2 - h * (p2[0] - p1[0]) / d

    x3_b = x2 - h * (p2[1] - p1[1]) / d
    y3_b = y2 + h * (p2[0] - p1[0]) / d
    
    # Pick Positive Y (In front of wall)
    if y3_a > y3_b:
        return np.array([x3_a, y3_a])
    else:
        return np.array([x3_b, y3_b])

# --- SERIAL THREAD ---
def serial_reader(port_name):
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        print(f"✅ Connected to {port_name}")
    except: 
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
                    with data_lock:
                        latest_distances[addr] = {
                            "dist": dist,
                            "time": time.time()
                        }
                except: pass
        except: break

# --- MAIN LOOP ---
def main():
    print(">>> STARTING 2-ANCHOR TRACKING (FILTERED) <<<")
    
    # Setup Logging
    try:
        f = open(LOG_FILE_NAME, 'a', newline='')
        writer = csv.writer(f)
        writer.writerow(["timestamp", "x_filt", "y_filt", "angle", "x_raw", "y_raw"])
        print(f"✅ Logging to {LOG_FILE_NAME}")
    except: 
        print("❌ Could not open log file")
    
    # Start Threads
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()

    print("Waiting for data...")
    
    while True:
        time.sleep(0.05) # 20Hz
        
        now = time.time()
        with data_lock:
            # Look for our 2 configured anchors
            ids = list(ANCHOR_CONFIG.keys())
            id1, id2 = ids[0], ids[1]
            
            if id1 in latest_distances and id2 in latest_distances:
                info1 = latest_distances[id1]
                info2 = latest_distances[id2]
                
                # Check Timeout (0.5s)
                if (now - info1["time"] < 0.5) and (now - info2["time"] < 0.5):
                    
                    p1 = np.array(ANCHOR_CONFIG[id1])
                    p2 = np.array(ANCHOR_CONFIG[id2])
                    
                    r1_raw = info1["dist"] - ANCHOR_BIAS.get(id1, 0)
                    r2_raw = info2["dist"] - ANCHOR_BIAS.get(id2, 0)
                    
                    # Height Math
                    r1 = np.sqrt(max(0.01, r1_raw**2 - TAG_HEIGHT**2))
                    r2 = np.sqrt(max(0.01, r2_raw**2 - TAG_HEIGHT**2))
                    
                    # Calculate Intersection (RAW)
                    raw_pos = get_circle_intersection(p1, r1, p2, r2)
                    
                    if raw_pos is not None:
                        # 1. BOUNDING BOX CHECK (Reject crazy values)
                        if abs(raw_pos[0]) > MAX_X_WIDTH or raw_pos[1] > MAX_Y_DEPTH:
                            # print(f"Outlier Ignored: ({raw_pos[0]:.2f}, {raw_pos[1]:.2f})")
                            continue 

                        # 2. MEDIAN FILTER
                        pos_history.append(raw_pos)
                        
                        # Extract separate X and Y lists from history
                        x_hist = [p[0] for p in pos_history]
                        y_hist = [p[1] for p in pos_history]
                        
                        # Calculate Median
                        x_filt = np.median(x_hist)
                        y_filt = np.median(y_hist)
                        
                        # 3. OUTPUT
                        angle_deg = np.degrees(np.arctan2(x_filt, y_filt))
                        
                        print(f"Pos: ({x_filt:.2f}, {y_filt:.2f}) | Angle: {angle_deg:.1f}°")
                        
                        try:
                            writer.writerow([datetime.now().isoformat(), x_filt, y_filt, angle_deg, raw_pos[0], raw_pos[1]])
                            f.flush()
                        except: pass
                    else:
                        pass # Bad geometry (circles don't touch)

if __name__ == '__main__':
    main()