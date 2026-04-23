# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 18:44:30 2026

@author: pedro
"""

import serial
import json
import numpy as np
import time
import threading
import sys

# --- CONFIGURATION ---
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1"] 
BAUD_RATE = 115200

# 1. ANCHOR COORDINATES (Where they are on the wall)
ANCHOR_CONFIG = {
    "1781": [-0.204, 0.0],  # Left
    "1782": [ 0.204, 0.0]   # Right
}

# 2. TAG TRUE POSITION (Where you placed the tag for this test)
#    Example: 1.5 meters straight out from the center
TRUE_TAG_X = 0.0
TRUE_TAG_Y = 1.50 

# 3. Z-AXIS (Height Difference)
#    If tag and anchors are at same height, set to 0.0
#    If tag is 0.5m lower than anchors, set to 0.5
HEIGHT_DIFF = 0.0 

# --- INTERNAL VARIABLES ---
samples = { id: [] for id in ANCHOR_CONFIG.keys() }
data_lock = threading.Lock()
running = True

def calculate_expected_distance(anchor_pos, tag_pos):
    """Calculates the physical distance (hypotenuse) between anchor and tag"""
    dx = anchor_pos[0] - tag_pos[0]
    dy = anchor_pos[1] - tag_pos[1]
    dz = HEIGHT_DIFF
    return np.sqrt(dx*dx + dy*dy + dz*dz)

def serial_reader(port_name):
    global running
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        print(f"✅ Connected to {port_name}")
    except Exception as e:
        print(f"❌ Failed to open {port_name}: {e}")
        return

    while running:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line.startswith("{"): continue
                
                try:
                    data = json.loads(line)
                    addr = data.get("A")
                    dist = float(data.get("R"))
                    
                    if addr in samples:
                        with data_lock:
                            samples[addr].append(dist)
                except: pass
        except: break

def main():
    global running
    print(f"Target Location: X={TRUE_TAG_X}m, Y={TRUE_TAG_Y}m")
    
    # Calculate Expected Distances
    expected = {}
    print("\nExpected Distances (Calculated from Geometry):")
    for addr, pos in ANCHOR_CONFIG.items():
        d = calculate_expected_distance(pos, [TRUE_TAG_X, TRUE_TAG_Y])
        expected[addr] = d
        print(f"  Anchor {addr}: {d:.4f} m")

    # Start Listeners
    threads = []
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()
        threads.append(t)

    print("\nCollecting 100 samples... (Please keep tag still)")
    
    # Wait until we have enough samples
    while True:
        time.sleep(0.5)
        counts = [len(samples[k]) for k in samples]
        min_count = min(counts) if counts else 0
        
        sys.stdout.write(f"\rSamples Collected: {min_count}/100")
        sys.stdout.flush()
        
        if min_count >= 100:
            break
    
    running = False
    print("\n\n--- CALIBRATION RESULTS ---")
    
    results = {}
    
    for addr in ANCHOR_CONFIG:
        data = samples[addr]
        if len(data) == 0:
            print(f"⚠️ Anchor {addr}: NO DATA RECEIVED")
            continue
            
        # Filter outliers for calibration (simple trim)
        data.sort()
        trim_amt = int(len(data) * 0.1) # Trim top/bottom 10%
        clean_data = data[trim_amt:-trim_amt]
        
        avg_measured = np.mean(clean_data)
        true_dist = expected[addr]
        
        # BIAS = MEASURED - TRUE
        # If it measures 1.7m but is really 1.5m, Bias is +0.2m.
        # We need to SUBTRACT 0.2m in the main code.
        bias = avg_measured - true_dist
        
        print(f"ANCHOR {addr}:")
        print(f"  Measured Avg: {avg_measured:.4f} m")
        print(f"  True Dist:    {true_dist:.4f} m")
        print(f"  Calculated Bias: {bias:.4f} m")
        print("-" * 30)
        
        results[addr] = round(bias, 3)

    print("\n>>> COPY THIS INTO YOUR PYTHON SCRIPT <<<")
    print(f"ANCHOR_BIAS = {json.dumps(results, indent=4)}")
    print("-----------------------------------------")

if __name__ == '__main__':
    main()
