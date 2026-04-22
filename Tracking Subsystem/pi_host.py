import serial
import struct
import threading
import time
import math
from datetime import datetime

# --- SYSTEM CONFIGURATION ---
# IMPORTANT: Measure the distance between your two anchors and set it here!
ANCHOR_DISTANCE_M = 1.0  

PORT_A = '/dev/ttyUSB0' 
PORT_B = '/dev/ttyUSB1' 

# --- BINARY PROTOCOL DEFINITION ---
PACKET_SIZE = 13
# <2s = 2 byte char[] header
# H   = unsigned short (2 bytes) tag address
# f   = float (4 bytes) range
# f   = float (4 bytes) rx_power
# B   = unsigned char (1 byte) checksum
PACKET_FORMAT = '<2sHffB' 

# Shared dictionary to hold the latest valid distances
latest_ranges = {
    PORT_A: {"range": None, "time": 0},
    PORT_B: {"range": None, "time": 0}
}
data_lock = threading.Lock()

def calculate_position(r1, r2, d):
    """
    Trilateration for 2 Anchors.
    Anchor 1 is at (0,0), Anchor 2 is at (d,0).
    Returns (x, y). 
    """
    if r1 is None or r2 is None: return None
    
    # Prevent math domain errors if the circles don't intersect
    if r1 + r2 < d: return None 
    if abs(r1 - r2) > d: return None 
    
    try:
        x = (r1**2 - r2**2 + d**2) / (2 * d)
        y = math.sqrt(abs(r1**2 - x**2))
        return (x, y)
    except ValueError:
        return None

def read_anchor_stream(port_name):
    """Continuously reads and decodes the binary stream from an anchor."""
    try:
        ser = serial.Serial(port_name, 115200, timeout=0.1)
        print(f"✅ Connected to Anchor on {port_name}")
        
        buffer = bytearray()
        
        while True:
            # Grab all available bytes
            if ser.in_waiting > 0:
                buffer.extend(ser.read(ser.in_waiting))
            
            # Process packets if buffer is large enough
            while len(buffer) >= PACKET_SIZE:
                # Look for the sync header (0xA5, 0x5A)
                if buffer[0] == 0xA5 and buffer[1] == 0x5A:
                    packet_data = buffer[:PACKET_SIZE]
                    try:
                        _, tag_addr, dist, dbm, checksum = struct.unpack(PACKET_FORMAT, packet_data)
                        
                        # Calculate and verify XOR checksum
                        calc_chk = 0
                        for b in packet_data[2:-1]: 
                            calc_chk ^= b
                        
                        if calc_chk == checksum:
                            # Data is valid! Update the shared state.
                            with data_lock:
                                latest_ranges[port_name]["range"] = dist
                                latest_ranges[port_name]["time"] = time.time()
                            
                            buffer = buffer[PACKET_SIZE:] # Remove processed packet
                        else:
                            buffer = buffer[1:] # CRC Fail, slide window by 1
                    except struct.error:
                        buffer = buffer[1:] # Struct error, slide window by 1
                else:
                    buffer.pop(0) # Not a header, slide window by 1
            
            time.sleep(0.005) # Yield to CPU

    except Exception as e:
        print(f"❌ Error reading {port_name}: {e}")

def main():
    print("--- 📍 UWB Wired Tracking System 📍 ---")
    print(f"--- Anchor Separation Baseline: {ANCHOR_DISTANCE_M}m ---")
    
    # Start threads for both USB ports
    t1 = threading.Thread(target=read_anchor_stream, args=(PORT_A,))
    t2 = threading.Thread(target=read_anchor_stream, args=(PORT_B,))
    t1.daemon = True
    t2.daemon = True
    t1.start()
    t2.start()

    # Main Calculation Loop
    while True:
        with data_lock:
            rA = latest_ranges[PORT_A]["range"]
            rB = latest_ranges[PORT_B]["range"]
            tA = latest_ranges[PORT_A]["time"]
            tB = latest_ranges[PORT_B]["time"]

        now = time.time()
        
        # Make sure we have fresh data from BOTH anchors (less than 0.5s old)
        if (now - tA < 0.5) and (now - tB < 0.5):
            pos = calculate_position(rA, rB, ANCHOR_DISTANCE_M)
            if pos:
                print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Tag Position: X = {pos[0]:5.2f}m, Y = {pos[1]:5.2f}m")
            else:
                print(f"⚠️  Math Error (Geom impossible): rA={rA:.2f}m, rB={rB:.2f}m")
        else:
            # Data is stale, waiting for sync
            pass
            
        time.sleep(0.1) # Calculate 10 times a second

if __name__ == "__main__":
    main()