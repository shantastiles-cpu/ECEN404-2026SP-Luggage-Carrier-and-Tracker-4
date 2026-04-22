import serial
import threading
import json
import time
from datetime import datetime

# --- CONFIGURATION ---
BAUD_RATE = 115200 
# We will auto-detect which port is AAAA or BBBB based on the data stream
PORT_1 = '/dev/ttyUSB0' 
PORT_2 = '/dev/ttyUSB1'

# Print Lock to prevent lines from getting mixed up in the console
print_lock = threading.Lock()

# Shared state to detect synchronization
# We store the last seen payload to compare across threads
last_seen = {
    "data": None,
    "time": 0,
    "source": None
}
sync_lock = threading.Lock()

def analyze_packet(port_name, data):
    """
    Analyzes incoming JSON to determine if it's a new packet or a sync event.
    """
    global last_seen
    current_time = time.time()
    
    # Extract identifier and value
    agent_id = data.get("A", "UNKNOWN")
    val = data.get("R", "N/A")
    
    # Prepare the log string
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    with sync_lock:
        # Check if we saw this EXACT value recently (within 200ms) from a different source
        # This logic assumes 'R' is a unique-ish value per packet (like a sequence or sensor reading)
        if (last_seen["data"] == val) and \
           (current_time - last_seen["time"] < 0.2) and \
           (last_seen["source"] != port_name):
            
            # This is a SYNC event (Both anchors heard the same tag broadcast)
            log_msg = f"✅ [SYNC]  R: {val:<8} | Detected on BOTH ports"
            
        else:
            # This is a NEW or SOLO event
            last_seen["data"] = val
            last_seen["time"] = current_time
            last_seen["source"] = port_name
            
            # Formatting to show which Anchor heard it
            if agent_id == "AAAA":
                log_msg = f"[{timestamp}] {port_name}: {agent_id} -> R: {val:<8} (BBBB Missed?)"
            elif agent_id == "BBBB":
                log_msg = f"[{timestamp}] {port_name}: {agent_id} -> R: {val:<8} (AAAA Missed?)"
            else:
                log_msg = f"[{timestamp}] {port_name}: {agent_id} -> R: {val}"

    with print_lock:
        print(log_msg)

def read_anchor(port_name):
    """
    Continuous loop to read from a specific serial port.
    """
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        with print_lock:
            print(f"🔵 Listening on {port_name}...")

        while True:
            try:
                if ser.in_waiting > 0:
                    raw_line = ser.readline().decode('utf-8', errors='replace').strip()
                    if raw_line:
                        try:
                            data = json.loads(raw_line)
                            analyze_packet(port_name, data)
                        except json.JSONDecodeError:
                            with print_lock:
                                print(f"⚠️  [{port_name}] RAW/ERR: {raw_line}")
                            
            except OSError as e:
                with print_lock:
                    print(f"❌ Error reading {port_name}: {e}")
                break
                
    except serial.SerialException as e:
        with print_lock:
            print(f"⚠️  Could not open {port_name}: {e}")

if __name__ == "__main__":
    print("--- Dual Anchor Fusion Monitor ---")
    print(f"--- Baud Rate: {BAUD_RATE} ---")
    print("--- Logic: Matching 'R' values within 200ms = SYNC ---")
    print("--- Press Ctrl+C to stop ---\n")

    t1 = threading.Thread(target=read_anchor, args=(PORT_1,))
    t2 = threading.Thread(target=read_anchor, args=(PORT_2,))

    t1.daemon = True
    t2.daemon = True

    t1.start()
    t2.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping debugger...")