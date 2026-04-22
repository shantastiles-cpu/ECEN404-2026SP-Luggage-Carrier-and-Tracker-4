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
    "1781": [-0.204, 0.0],
    "1782": [ 0.204, 0.0],
    "1783": [ 0.0,   0.59]
}

ANCHOR_BIAS = {
    "1781": 0.0,
    "1782": 0.0,
    "1783": 0.0
}

# --- Z-AXIS ---
TAG_HEIGHT = 0.7  # Height difference between tag and anchors (meters)
MIN_DIST = 0.01
MAX_DIST = 50.0

# --- GROUND TRUTH (for error calculation) ---
TRUE_X = 0.0
TRUE_Y = 1.5

# --- FILTERING ---
USE_MEDIAN_FILTER = True  # Enable simple median filtering for stability
FILTER_WINDOW = 5  # Number of recent positions to use for median filter

# --------------------

# Global data buffer
latest_distances = {}
data_lock = threading.Lock()

# Position history for filtering
position_history = []

def trilaterate_2d(anchors, distances):
    """
    2D trilateration using least-squares method
    Returns [x, y] position or None if calculation fails
    """
    anchors = np.array(anchors)
    distances = np.array(distances)
    
    try:
        # Use first anchor as reference point
        p1 = anchors[0]
        r1 = distances[0]
        
        # Build linear system A*pos = b
        A, b = [], []
        for i in range(1, len(anchors)):
            pi = anchors[i]
            ri = distances[i]
            A.append(2 * (pi - p1))
            b.append(r1**2 - ri**2 - np.dot(p1, p1) + np.dot(pi, pi))
        
        # Solve using least squares
        pos, *_ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
        return np.array([pos[0], pos[1]])
    except:
        return None

def median_filter(new_position):
    """
    Apply median filter to smooth position estimates
    """
    global position_history
    
    if new_position is None:
        return None
    
    # Add new position to history
    position_history.append(new_position)
    
    # Keep only recent positions
    if len(position_history) > FILTER_WINDOW:
        position_history.pop(0)
    
    # Need at least 3 points for meaningful median
    if len(position_history) < 3:
        return new_position
    
    # Calculate median x and y separately
    x_values = [pos[0] for pos in position_history]
    y_values = [pos[1] for pos in position_history]
    
    median_x = np.median(x_values)
    median_y = np.median(y_values)
    
    return np.array([median_x, median_y])

# --- SERIAL WORKER THREAD ---
def serial_reader(port_name):
    """
    Reads from one USB port and updates the global distance list
    """
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        print(f"✅ Connected to {port_name}")
    except Exception as e:
        print(f"❌ Failed to open {port_name}: {e}")
        return

    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue

            if not line.startswith("{"):
                continue

            data = json.loads(line)

            addr = str(data.get("A", "")).strip()
            dist = float(data.get("R"))

            # Normalize common formats
            addr = addr.lower().replace("0x", "")

            with data_lock:
                latest_distances[addr] = {
                    "dist": dist, 
                    "time": time.time(), 
                    "port": port_name
                }

        except Exception:
            continue


# --- MAIN LOOP ---
def main():
    print("="*70)
    print(">>> UWB TAG TRACKING SYSTEM <<<")
    print("="*70)
    print(f"\nConfiguration:")
    print(f"  Anchors: {list(ANCHOR_CONFIG.keys())}")
    print(f"  Tag height: {TAG_HEIGHT}m")
    print(f"  Median filter: {'ENABLED' if USE_MEDIAN_FILTER else 'DISABLED'}")
    print(f"  Ground truth: ({TRUE_X}, {TRUE_Y})")
    print()
    
    # 1. Start Serial Threads
    print("Starting serial readers...")
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()
    
    time.sleep(1)

    # 2. Setup Logging
    try:
        f = open(LOG_FILE_NAME, 'a', newline='')
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "x_raw", "y_raw", "x_filtered", "y_filtered",
            "angle", "dist_origin", "error_raw", "error_filtered"
        ])
        print(f"✅ Logging to: {LOG_FILE_NAME}\n")
    except Exception as e:
        print(f"⚠️  Logging disabled: {e}\n")
        writer = None
    
    print("Waiting for anchor data...")
    time.sleep(2)
    
    # Quick diagnostic check
    print("\n" + "="*70)
    print("INITIAL ANCHOR CHECK")
    print("="*70)
    time.sleep(1)
    
    with data_lock:
        if latest_distances:
            print("✅ Receiving data from anchors:")
            for addr, info in latest_distances.items():
                status = "✓" if addr in ANCHOR_CONFIG else "✗ UNKNOWN"
                print(f"  {status} Anchor {addr}: {info['dist']:.2f}m (port: {info['port']})")
        else:
            print("❌ NO DATA RECEIVED - Check your setup!")
            return
    
    print("\n" + "="*70)
    print("STARTING REAL-TIME TRACKING")
    print("="*70)
    print("Press Ctrl+C to stop\n")
    
    loop_count = 0
    last_valid_position = None
    
    try:
        while True:
            time.sleep(0.1)  # 10Hz Update Loop
            loop_count += 1
            
            current_anchors = []
            current_distances = []
            current_anchor_ids = []
            now = time.time()
            
            with data_lock:
                # Collect valid anchor measurements
                for addr, info in latest_distances.items():
                    # Only use fresh data (less than 0.5s old)
                    age = now - info["time"]
                    if age < 0.5:
                        if addr in ANCHOR_CONFIG:
                            bias = ANCHOR_BIAS.get(addr, 0.0)
                            d_raw = info["dist"] - bias
                            
                            # Apply height compensation
                            if d_raw > TAG_HEIGHT:
                                d_horiz = np.sqrt(d_raw**2 - TAG_HEIGHT**2)
                                if MIN_DIST < d_horiz < MAX_DIST:
                                    current_anchors.append(ANCHOR_CONFIG[addr])
                                    current_distances.append(d_horiz)
                                    current_anchor_ids.append(addr)
                            else:
                                # Tag is very close to or below anchor plane
                                if MIN_DIST < d_raw < MAX_DIST:
                                    current_anchors.append(ANCHOR_CONFIG[addr])
                                    current_distances.append(d_raw)
                                    current_anchor_ids.append(addr)

            # Calculate position if we have enough anchors
            if len(current_anchors) >= 3:
                # Raw trilateration
                pos_raw = trilaterate_2d(current_anchors, current_distances)
                
                if pos_raw is not None:
                    x_raw, y_raw = pos_raw[0], pos_raw[1]
                    
                    # Apply median filter if enabled
                    if USE_MEDIAN_FILTER:
                        pos_filtered = median_filter(pos_raw)
                        if pos_filtered is not None:
                            x_filt, y_filt = pos_filtered[0], pos_filtered[1]
                        else:
                            x_filt, y_filt = x_raw, y_raw
                    else:
                        x_filt, y_filt = x_raw, y_raw
                    
                    # Calculate metrics
                    angle = np.degrees(np.arctan2(x_filt, y_filt))
                    dist = np.sqrt(x_filt**2 + y_filt**2)
                    error_raw = np.sqrt((x_raw - TRUE_X)**2 + (y_raw - TRUE_Y)**2)
                    error_filt = np.sqrt((x_filt - TRUE_X)**2 + (y_filt - TRUE_Y)**2)
                    
                    # Display position
                    print(f"[{loop_count:04d}] Pos: ({x_filt:6.2f}, {y_filt:6.2f})m | "
                          f"Angle: {angle:6.1f}° | Dist: {dist:5.2f}m | "
                          f"Error: {error_filt:5.2f}m | Anchors: {len(current_anchors)}")
                    
                    # Log to file
                    if writer:
                        try:
                            writer.writerow([
                                datetime.now().isoformat(),
                                x_raw, y_raw, x_filt, y_filt,
                                angle, dist, error_raw, error_filt
                            ])
                            f.flush()
                        except:
                            pass
                    
                    last_valid_position = [x_filt, y_filt]
            
            else:
                # Not enough anchors
                if loop_count % 10 == 0:  # Only print every second
                    print(f"⚠️  Waiting for anchors... (have {len(current_anchors)}/3)")
                    with data_lock:
                        for addr in ["1781", "1782", "1783"]:
                            if addr in latest_distances:
                                age = now - latest_distances[addr]["time"]
                                print(f"    {addr}: {latest_distances[addr]['dist']:.2f}m (age: {age:.1f}s)")
                            else:
                                print(f"    {addr}: NO DATA")
    
    except KeyboardInterrupt:
        print("\n\n" + "="*70)
        print("TRACKING STOPPED")
        print("="*70)
        if last_valid_position:
            print(f"Last position: ({last_valid_position[0]:.2f}, {last_valid_position[1]:.2f})")
        print(f"Total updates: {loop_count}")
        if writer:
            f.close()
            print(f"Log saved to: {LOG_FILE_NAME}")

if __name__ == '__main__':
    main()
