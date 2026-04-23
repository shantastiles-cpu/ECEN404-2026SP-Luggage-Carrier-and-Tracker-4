import serial
import json
import numpy as np
import time
import csv
import threading
from datetime import datetime

# --- CONFIGURATION ---
# UPDATE THESE PORTS! (Only 2 needed now)
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1"] 
BAUD_RATE = 115200

# --- 2 ANCHOR GEOMETRY ---
# We assume they are on the wall (Y=0)
ANCHOR_CONFIG = {
    "1781": [-0.204, 0.0],  # Left Anchor
    "1782": [ 0.204, 0.0]   # Right Anchor
}

# Calibration
ANCHOR_BIAS = { "1781": 0.0, "1782": 0.0 }
TAG_HEIGHT = 0.0 # Set to 0.0 for desk test, 0.7 for walking

# --- GLOBAL DATA ---
latest_distances = {}
data_lock = threading.Lock()

def get_circle_intersection(p1, r1, p2, r2):
    """
    Finds intersection of two circles.
    Returns the point with Positive Y (In front of wall).
    """
    d = np.linalg.norm(p2 - p1)
    
    # 1. Check for valid solution
    if d > r1 + r2: return None # Circles too far apart
    if d < abs(r1 - r2): return None # One circle inside other
    if d == 0: return None # Coincident circles
    
    # 2. Geometry
    # 'a' is distance from p1 to the perpendicular line between intersections
    a = (r1**2 - r2**2 + d**2) / (2*d)
    
    # 'h' is the height from that line to the intersection points
    h = np.sqrt(max(0, r1**2 - a**2))
    
    # Find the "middle point" P2 on the line between centers
    x2 = p1[0] + a * (p2[0] - p1[0]) / d
    y2 = p1[1] + a * (p2[1] - p1[1]) / d
    
    # 3. Calculate the two possible points
    # Solution A
    x3_a = x2 + h * (p2[1] - p1[1]) / d
    y3_a = y2 - h * (p2[0] - p1[0]) / d

    # Solution B
    x3_b = x2 - h * (p2[1] - p1[1]) / d
    y3_b = y2 + h * (p2[0] - p1[0]) / d
    
    # 4. CONSTRAINT: Pick the one "In Front" (Positive Y)
    # We assume the wall is at Y=0 and user is at Y > 0
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
    print(">>> STARTING 2-ANCHOR TRACKING <<<")
    
    # Start Threads
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()

    print("Waiting for data...")
    
    while True:
        time.sleep(0.05) # 20Hz
        
        # Get Current Data
        anchors_found = []
        dists_found = []
        
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
                    
                    # 1. Get Geometry
                    p1 = np.array(ANCHOR_CONFIG[id1])
                    p2 = np.array(ANCHOR_CONFIG[id2])
                    
                    # 2. Apply Bias & Height Comp
                    r1_raw = info1["dist"] - ANCHOR_BIAS.get(id1, 0)
                    r2_raw = info2["dist"] - ANCHOR_BIAS.get(id2, 0)
                    
                    # Height Math
                    r1 = np.sqrt(max(0.01, r1_raw**2 - TAG_HEIGHT**2))
                    r2 = np.sqrt(max(0.01, r2_raw**2 - TAG_HEIGHT**2))
                    
                    # 3. Calculate Intersection
                    pos = get_circle_intersection(p1, r1, p2, r2)
                    
                    if pos is not None:
                        x, y = pos[0], pos[1]
                        print(f"Pos: ({x:.2f}, {y:.2f}) | Dist: {r1:.2f}m / {r2:.2f}m")
                    else:
                        print(f"Bad Geom: Circles don't touch! ({r1:.2f} + {r2:.2f} < Dist)")

if __name__ == '__main__':
    main()