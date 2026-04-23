import serial
import json
import numpy as np
import time
import threading

# --- CONFIGURATION ---
# UPDATE THIS with your ports
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"] 
BAUD_RATE = 115200

# ANCHOR CONFIG
ANCHOR_CONFIG = {
    "1782": [-0.204, 0.0],
    "1783": [0.204, 0.0],
    "1784": [0.0, 0.59]
}
# SET THESE TO 0.0 FOR NOW
ANCHOR_BIAS = { "1782": 0.0, "1783": 0.0, "1784": 0.0 }

# --- CRITICAL SETTINGS ---
TAG_HEIGHT = 0.0   # Must be 0.0 for desk testing
MIN_DIST = 0.01

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

def serial_reader(port_name):
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
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
    print(">>> DEBUGGING STUCK COORDINATES <<<")
    
    for port in SERIAL_PORTS:
        t = threading.Thread(target=serial_reader, args=(port,), daemon=True)
        t.start()

    while True:
        time.sleep(0.5) 
        
        current_anchors = []
        current_distances = []
        now = time.time()
        
        print("\n--- NEW FRAME ---")
        
        with data_lock:
            for addr, info in latest_distances.items():
                if (now - info["time"]) < 1.0: # 1 second timeout
                    
                    raw_val = info["dist"]
                    bias_val = ANCHOR_BIAS.get(addr, 0.0)
                    corrected_val = raw_val - bias_val
                    
                    # LOGIC CHECK
                    final_dist = 0.01
                    status = "BAD (Too Small)"
                    
                    if corrected_val > TAG_HEIGHT:
                        final_dist = np.sqrt(corrected_val**2 - TAG_HEIGHT**2)
                        status = "GOOD"
                        current_anchors.append(ANCHOR_CONFIG[addr])
                        current_distances.append(final_dist)
                    else:
                        current_anchors.append(ANCHOR_CONFIG[addr])
                        current_distances.append(0.01)

                    print(f"Anchor {addr}: Raw={raw_val:.2f} | Bias={bias_val} | Height={TAG_HEIGHT} -> Horizontal={final_dist:.2f} ({status})")

        if len(current_anchors) >= 3:
            pos = trilaterate_2d(current_anchors, current_distances)
            if pos is not None:
                print(f"RESULT: ({pos[0]:.2f}, {pos[1]:.2f})")
        else:
            print("WAITING FOR 3 ANCHORS...")

if __name__ == '__main__':
    main()