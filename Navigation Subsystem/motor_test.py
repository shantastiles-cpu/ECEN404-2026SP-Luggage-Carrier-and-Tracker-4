import serial
import time

try:
    print("Opening /dev/serial0...")
    ser = serial.Serial('/dev/serial0', 9600, timeout=1)
    
    print("Port open! Testing Motor 1 (Left) at ~20% speed...")
    # 64 is Stop, 127 is Full Forward. 76 is a slow crawl.
    ser.write(bytes([76])) 
    time.sleep(2)
    ser.write(bytes([64])) # Stop
    
    print("Testing Motor 2 (Right) at ~20% speed...")
    # 192 is Stop, 255 is Full Forward. 204 is a slow crawl.
    ser.write(bytes([204])) 
    time.sleep(2)
    ser.write(bytes([192])) # Stop

    print("Test Complete.")
    ser.close()

except PermissionError:
    print("\nERROR: Permission Denied! You need to run the usermod dialout command.")
except Exception as e:
    print(f"\nERROR: {e}")