import serial
import json
import numpy as np
import time
import csv
import threading
import sys
from datetime import datetime
from collections import deque

# ==========================================
#               CONFIGURATION
# ==========================================

# 1. USB PORTS (Update if needed)
#    Pi 5 often uses /dev/ttyACM0, etc.
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1"] 
BAUD_RATE = 115200

# 2. ANCHOR GEOMETRY (X, Y)
#    We assume anchors are on the wall (Y=0)
ANCHOR_CONFIG = {
    "1781": [-0.204, 0.0],  # Left Anchor
    "1782": [ 0.204, 0.0]   # Right Anchor
}

# 3. CALIBRATION (Meters)
#    Use values from calibrate_anchors.py
ANCHOR_BIAS = { "1781": 1.0, "1782": 1.0 }

# 4. SETTINGS
TAG_HEIGHT = 0.0        # 0.0 for desk test, ~0.7 for walking
TIMEOUT_SEC = 1.0       # Time before declaring signal lost
FILTER_WINDOW = 5       # Median filter size (smoothing)
MAX_JUMP_METERS = 3.0   # Reject jumps larger than this (anti-teleport)
MAX_GAP_TOLERANCE = 0.5 # Allow circles to miss by 50cm, then reject

# ==========================================

# Global Data
latest_distances = {}
data_lock = threading.Lock()
pos_history = deque(maxlen=FILTER_WINDOW)
last_valid_pos = None

def get_circle_intersection_v2(p1, r1, p2, r2):
    """
    Robust Intersection Solver.
    Returns: (Position_Array, Status_String)
    """
    # Distance between anchors
    d = np.linalg.norm(p2 - p1)
    
    # --- CASE 1: Circles represent a GAP (Too far apart) ---
    if d > r1 + r2:
        gap = d - (r1 + r2)
        # If the gap is huge, it's a glitch (or user is gone)
        if gap > MAX_GAP_TOLERANCE:
            return None, f"GAP_TOO_BIG ({gap:.2f}m)"
        
        # If gap is small, snap to the midpoint of the gap
        # This fixes "Sputtering" when signals fluctuate slightly
        ratio = r1 / (r1 + r2)
        x = p1[0] + (p2[0] - p1[0]) * ratio
        y = p1[1] + (p2[1] - p1[1]) * ratio
        return np.array([x, y]), "SOFT_SNAP"

    # --- CASE 2: One circle INSIDE the other ---
    if d < abs(r1 - r2):
        # This usually happens if user is very close to one anchor
        # We pick the edge of the inner circle
        if r1 > r2: ratio = r1 / d 
        else:       ratio = r2 / d
        
        # This is mathematically risky, often better to reject
        return None, "INSIDE_ERROR"

    # --- CASE 3: Normal Intersection ---
    if d == 0: return None, "COINCIDENT"
    
    a = (r1**2 - r2**2 + d**2) / (2*d)
    h = np.sqrt(max(0, r1**2 - a**2))
    
    x2 = p1[0] + a * (p2[0] - p1[0]) / d
    y2 = p1[1] + a * (p2[1] - p1[1]) / d
    
    # Two possible points (Mirror images)
    x3_a = x2 + h * (p2[1] - p1[1]) / d
    y3_a = y2 - h * (p2[0] - p1[0]) / d

    x3_b = x2 - h * (p2[1] - p1[1]) / d
    y3_b = y2 + h * (p2[0] - p1[0]) / d
    
    # CONSTRAINT: User is "In Front" (Positive Y)
    if y3_a > y3_b: return np.array([x3_a, y3_a]), "OK"
    else:           return np.array([x3_b, y3_b]), "OK"

def serial_reader(port_name):
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        print(f"✅ Connected to {port_name}")
    except: return

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
                        latest_distances[addr] = { "dist": dist, "time": time.time() }
                except: pass
        except: break

def main():
    global last_valid_pos
    print(">>> WIRED SERVER V2 (ROBUST 2-ANCHOR) <<<")
    
    # Log File
    try:
        f = open('position_log_v2.csv', 'a', newline='')
        writer = csv.writer(f)
        writer.writerow(["timestamp", "x", "y", "angle", "status", "r1", "r2"])
    except: pass
    
    # Start Threads
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()
        
    print("Waiting for data...")

    while True:
        time.sleep(0.05) # 20Hz Update Rate
        now = time.time()
        
        id1, id2 = "1781", "1782"
        valid_inputs = False
        r1_final, r2_final = 0, 0
        
        # 1. READ DATA
        with data_lock:
            if id1 in latest_distances and id2 in latest_distances:
                info1 = latest_distances[id1]
                info2 = latest_distances[id2]
                
                # Check Timeout
                if (now - info1["time"] < TIMEOUT_SEC) and (now - info2["time"] < TIMEOUT_SEC):
                    
                    # Apply Bias
                    r1_raw = info1["dist"] - ANCHOR_BIAS.get(id1, 0)
                    r2_raw = info2["dist"] - ANCHOR_BIAS.get(id2, 0)
                    
                    # Height Compensation (Prevent Negative Sqrt)
                    # If dist < height, we assume user is directly under anchor (dist = 0.01)
                    if r1_raw > TAG_HEIGHT: r1_final = np.sqrt(r1_raw**2 - TAG_HEIGHT**2)
                    else:                   r1_final = 0.01
                        
                    if r2_raw > TAG_HEIGHT: r2_final = np.sqrt(r2_raw**2 - TAG_HEIGHT**2)
                    else:                   r2_final = 0.01
                    
                    valid_inputs = True

        # 2. CALCULATE
        if valid_inputs:
            p1 = np.array(ANCHOR_CONFIG[id1])
            p2 = np.array(ANCHOR_CONFIG[id2])
            
            pos, status = get_circle_intersection_v2(p1, r1_final, p2, r2_final)
            
            if pos is not None:
                # 3. FILTERING
                
                # A. Teleport Check (Jumping 0 -> 2m -> 0 is glitch)
                if last_valid_pos is not None:
                    jump = np.linalg.norm(pos - last_valid_pos)
                    if jump > MAX_JUMP_METERS:
                        # Reject this frame, keep last known pos
                        # print(f"⚠️ Ignored Jump ({jump:.2f}m)")
                        pos = last_valid_pos
                        status = "JUMP_REJECTED"
                
                # B. Median Filter (Smoothing)
                pos_history.append(pos)
                x_filt = np.median([p[0] for p in pos_history])
                y_filt = np.median([p[1] for p in pos_history])
                
                # Update State
                last_valid_pos = np.array([x_filt, y_filt])
                
                # 4. OUTPUT
                angle = np.degrees(np.arctan2(x_filt, y_filt))
                
                print(f"Pos: ({x_filt:.2f}, {y_filt:.2f}) | Angle: {angle:>5.1f}° | {status:<10} | R1:{r1_final:.2f} R2:{r2_final:.2f}")
                
                try:
                    writer.writerow([datetime.now().isoformat(), x_filt, y_filt, angle, status, r1_final, r2_final])
                    f.flush()
                except: pass
            else:
                # Math failed (Inside error, or massive gap)
                print(f"Bad Geom: {status}")

if __name__ == '__main__':
    main()

