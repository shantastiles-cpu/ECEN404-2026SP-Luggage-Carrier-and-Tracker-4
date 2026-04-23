import json
import numpy as np
import time
import paho.mqtt.client as mqtt
import csv
from datetime import datetime
import os
from filterpy.kalman import KalmanFilter 

# --- MQTT Configuration ---
MQTT_BROKER_IP = "localhost" 
MQTT_BROKER_PORT = 1883
DATA_TOPIC = "uwb/tag/data"
STATUS_TOPIC = "uwb/tag/status"
RESULT_TOPIC = "uwb/tag/result"

# --- File Path ---
LOG_FILE_NAME = 'position_log.csv' 

# --- 1. CALIBRATION ---
ANCHOR_BIAS = { 
    "1782": 0.0, 
    "1783": 0.0, 
    "1781": 0.0 
}

# --- 2. GEOMETRY CONFIG ---
ANCHOR_CONFIG = {
    "1782": [-0.204, 0.0],
    "1783": [0.204, 0.0],
    "1781": [0.0, 0.59]
}

# --- 3. ROBUSTNESS & Z-AXIS SETTINGS ---
MIN_DIST = 0.01
MAX_DIST = 50.0
MAX_SPEED_MPS = 3.0 
TAG_HEIGHT = 0.7  # Fixed height of tag in meters
ROOM_BOUNDS = { "x_min": -5.0, "x_max": 5.0, "y_min": -5.0, "y_max": 5.0 }

def trilaterate_2d(anchors, distances):
    anchors = np.array(anchors); distances = np.array(distances)
    p1 = anchors[0]; r1 = distances[0]
    A, b = [], []
    for i in range(1, len(anchors)):
        pi = anchors[i]; ri = distances[i]
        A.append(2 * (pi - p1))
        b.append(r1**2 - ri**2 - np.dot(p1, p1) + np.dot(pi, pi))
    A = np.array(A); b = np.array(b)
    try:
        pos, *_ = np.linalg.lstsq(A, b, rcond=None)
        return np.array([pos[0], pos[1]])
    except np.linalg.LinAlgError: return None

def create_kalman_filter():
    kf = KalmanFilter(dim_x=4, dim_z=2) 
    dt = 0.5 
    kf.F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])
    kf.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])
    kf.R = np.eye(2) * (0.3**2) # Tuned for robustness
    kf.Q = np.eye(4) * 0.01
    kf.P = np.eye(4) * 500.
    return kf

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT Broker (Code: {rc})")
    client.subscribe(DATA_TOPIC, qos=1)
    client.subscribe(STATUS_TOPIC, qos=1)

def is_physically_possible(new_pos, last_pos, dt):
    x, y = new_pos
    if not (ROOM_BOUNDS["x_min"] <= x <= ROOM_BOUNDS["x_max"]): return False
    if not (ROOM_BOUNDS["y_min"] <= y <= ROOM_BOUNDS["y_max"]): return False
    if last_pos is None: return True
    distance_moved = np.linalg.norm(new_pos - last_pos)
    speed = distance_moved / dt
    if speed > MAX_SPEED_MPS: return False
    return True

def on_message(client, userdata, msg):
    if msg.topic == STATUS_TOPIC:
        status = msg.payload.decode('utf-8')
        print(f"\n== STATUS UPDATE: Tag is now {status} ==\n")
        return 
    
    try:
        payload_str = msg.payload.decode('UTF-8')
        uwb_data = json.loads(payload_str)
        uwb_list = uwb_data.get("links", [])
        
        log_file_handle = client.log_file_handle
        log_csv_writer = client.log_csv_writer
        kf = client.kalman_filter
        
        current_time = time.time()
        last_time = getattr(client, 'last_update_time', current_time - 0.5)
        client.last_update_time = current_time
        dt = current_time - last_time
        if dt <= 0: dt = 0.1

    except Exception: return

    current_anchors, current_distances = [], []
    
    for link in uwb_list:
        try:
            addr = link.get("A"); raw_dist = float(link.get("R"))
            
            if addr in ANCHOR_CONFIG:
                bias = ANCHOR_BIAS.get(addr, 0.0)
                d_raw = raw_dist - bias
                
                # Height Compensation Logic
                if d_raw > TAG_HEIGHT:
                    d_horizontal = np.sqrt(d_raw**2 - TAG_HEIGHT**2)
                    if MIN_DIST < d_horizontal < MAX_DIST:
                        current_anchors.append(ANCHOR_CONFIG[addr])
                        current_distances.append(d_horizontal)
                else:
                    # Directly underneath -> horizontal distance near zero
                    current_anchors.append(ANCHOR_CONFIG[addr])
                    current_distances.append(0.01)
                    
        except Exception: pass
    
    kf.predict()
    is_good_measurement = False
    tag_est = None 
    
    if len(current_anchors) >= 3:
        candidate_pos = trilaterate_2d(current_anchors, current_distances)
        if candidate_pos is not None:
            last_x, last_y = float(kf.x[0]), float(kf.x[1])
            if is_physically_possible(candidate_pos, np.array([last_x, last_y]), dt):
                is_good_measurement = True
                tag_est = candidate_pos
                kf.update(z=tag_est)
    
    x_filt, y_filt = float(kf.x[0]), float(kf.x[1])
    
    # Calculate Angle & Distance from Origin
    angle_rad = np.arctan2(x_filt, y_filt)
    angle_deg = np.degrees(angle_rad)
    dist_origin = np.sqrt(x_filt**2 + y_filt**2)
    
    print(f"  Pos: ({x_filt:.2f}, {y_filt:.2f}) | Angle: {angle_deg:.1f}° | Dist: {dist_origin:.2f}m")
    
    # Publish Result for Laptop
    result_payload = json.dumps({
        "x": x_filt, "y": y_filt, "angle": angle_deg, "dist": dist_origin
    })
    client.publish(RESULT_TOPIC, result_payload)
    
    try:
        timestamp = datetime.now().isoformat()
        calc_x, calc_y = (tag_est[0], tag_est[1]) if is_good_measurement else (np.nan, np.nan)
        
        # Log simplified data
        log_data = [timestamp, calc_x, calc_y, x_filt, y_filt, angle_deg, dist_origin]
        log_csv_writer.writerow(log_data)
        log_file_handle.flush() 
    except Exception: pass

def main():
    print("\n>>> RUNNING SERVER v8 (Clean Production) <<<\n")
    
    file_needs_header = not os.path.exists(LOG_FILE_NAME)
    try:
        log_file = open(LOG_FILE_NAME, 'a', newline='')
        csv_writer = csv.writer(log_file)
        if file_needs_header:
            csv_writer.writerow(["timestamp", "x_calc", "y_calc", "x_filtered", "y_filtered", "angle_deg", "dist_origin"])
            log_file.flush()
    except Exception as e: print(f"FATAL: {e}"); return

    client = mqtt.Client() 
    kf = create_kalman_filter()
    client.kalman_filter = kf
    client.log_file_handle = log_file
    client.log_csv_writer = csv_writer
    client.last_update_time = time.time()
    
    client.on_connect = on_connect
    client.on_message = on_message

    try: client.connect(MQTT_BROKER_IP, MQTT_BROKER_PORT, 60)
    except Exception as e: print(f"Connection Error: {e}"); log_file.close(); return

    client.loop_start()
    print("Network loop started. Waiting for UWB data...")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        client.loop_stop(); client.disconnect(); log_file.close()
        print("Server stopped.")

if __name__ == '__main__':
    main()