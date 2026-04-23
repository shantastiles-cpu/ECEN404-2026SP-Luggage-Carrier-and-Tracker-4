import serial
import time
import curses
import sys

# --- Sabertooth Class (Unchanged) ---
class Sabertooth:
    def __init__(self, port, baud_rate=9600):
        try:
            self.ser = serial.Serial(port, baud_rate, timeout=1)
            self.connected = True
        except serial.SerialException:
            self.connected = False
            self.ser = None 

    def set_motor_speed(self, motor, speed):
        speed = max(-100, min(100, int(speed)))
        if not self.connected: return 
        
        if motor == 1:
            value = int(64 + (speed / 100.0) * 63.0)
            value = max(1, min(127, value)) if speed != 0 else 64
        else: # Motor 2
            value = int(192 + (speed / 100.0) * 63.0)
            value = max(129, min(255, value)) if speed != 0 else 192
        try:
            self.ser.write(bytes([value]))
        except:
            pass

    def stop_all(self):
        if self.connected: self.ser.write(bytes([0]))

    def close(self):
        self.stop_all()
        if self.connected: self.ser.close()

# --- PID Controller Class (UPDATED) ---
class PIDController:
    def __init__(self, Kp, Ki, Kd, setpoint, integral_limit=50.0):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.integral_limit = integral_limit
        self.prev_error = 0
        self.integral = 0
        
    def update(self, current_value, is_heading_pid=False):
        if is_heading_pid:
            error = self.setpoint - current_value
            if error > 180: error -= 360
            elif error < -180: error += 360
        else:
            # "Follow-Me" Logic: Error positive if too far
            error = current_value - self.setpoint
        
        self.integral += error
        self.integral = max(-self.integral_limit, min(self.integral_limit, self.integral))
        
        derivative = error - self.prev_error
        output = (self.Kp * error) + (self.Ki * self.integral) + (self.Kd * derivative)
        self.prev_error = error
        return output

    # --- NEW: Reset method to clear integral windup ---
    def reset(self):
        self.integral = 0
        self.prev_error = 0

# --- Main Curses Application ---
def main(stdscr):
    # 1. Setup Curses (The UI)
    curses.curs_set(0) # Hide cursor
    stdscr.nodelay(True) # Don't block waiting for input
    stdscr.timeout(50) # Refresh every 50ms

    # 2. Hardware Setup
    SERIAL_PORT = '/dev/serial0'
    saber = Sabertooth(SERIAL_PORT)

    # 3. PID Configuration
    DIST_KP, DIST_KI, DIST_KD = 0.5, 0.25, 0.05
    TARGET_DISTANCE = 35.0
    
    HEAD_KP, HEAD_KI, HEAD_KD = 0.5, 0.3, 0.05
    TARGET_HEADING = 0.0
    
    SPEED_CAP = 50.0
    TURN_CAP = 30.0
    
    # --- NEW: Safety Thresholds ---
    MAX_FOLLOW_DIST = 183.0 # 6 feet in cm
    
    pid_dist = PIDController(DIST_KP, DIST_KI, DIST_KD, TARGET_DISTANCE, integral_limit=40.0)
    pid_head = PIDController(HEAD_KP, HEAD_KI, HEAD_KD, TARGET_HEADING, integral_limit=20.0)

    # 4. Simulation State Variables
    sim_distance = 35.0 # Start at target
    sim_heading = 0.0   # Start at target
    luggage_fallen = False # NEW: Luggage state
    
    try:
        while True:
            # --- INPUT HANDLING ---
            key = stdscr.getch()
            
            if key == ord('q'): # Quit
                break
            elif key == ord(' '): # Emergency Stop / Reset
                sim_distance = 35.0
                sim_heading = 0.0
                luggage_fallen = False
                # --- FIX: Reset PIDs to clear integral memory ---
                pid_dist.reset()
                pid_head.reset()
                
            elif key == ord('l'): # Toggle Luggage Sensor
                luggage_fallen = not luggage_fallen
                # --- FIX: Reset everything if luggage falls ---
                if luggage_fallen:
                    sim_distance = 35.0
                    sim_heading = 0.0
                    pid_dist.reset()
                    pid_head.reset()
                    
            elif key == ord('w'): # Move object AWAY
                sim_distance += 1.0
            elif key == ord('s'): # Move object CLOSER
                sim_distance -= 1.0
            elif key == ord('a'): # Drift LEFT
                sim_heading -= 1.0
            elif key == ord('d'): # Drift RIGHT
                sim_heading += 1.0

            # --- PID CALCULATIONS ---
            base_speed_raw = pid_dist.update(sim_distance)
            turn_adjust_raw = pid_head.update(sim_heading, is_heading_pid=True)
            
            # Caps
            base_speed = max(-SPEED_CAP, min(SPEED_CAP, base_speed_raw))
            turn_adjust = max(-TURN_CAP, min(TURN_CAP, turn_adjust_raw))
            
            # Mixer
            left_speed = max(-100, min(100, base_speed + turn_adjust))
            right_speed = max(-100, min(100, base_speed - turn_adjust))
            
            # --- SAFETY OVERRIDES ---
            safety_active = False
            safety_msg = ""
            
            # Check 1: Distance Safety
            if sim_distance > MAX_FOLLOW_DIST:
                left_speed = 0
                right_speed = 0
                safety_active = True
                safety_msg = "STOPPED: USER > 6 FT AWAY"
            
            # Check 2: Luggage Safety (Overrides everything)
            if luggage_fallen:
                left_speed = 0
                right_speed = 0
                safety_active = True
                safety_msg = "STOPPED: LUGGAGE FELL OFF!"

            # Send to Motors
            saber.set_motor_speed(1, left_speed)
            saber.set_motor_speed(2, right_speed)

            # --- DRAW DASHBOARD ---
            stdscr.clear()
            stdscr.addstr(0, 0,  "=== ROVER NAVIGATION CONTROLLER DEMO ===", curses.A_BOLD)
            
            stdscr.addstr(2, 2,  "INSTRUCTIONS:")
            stdscr.addstr(3, 4,  "[W/S] Change Distance")
            stdscr.addstr(4, 4,  "[A/D] Change Heading")
            stdscr.addstr(5, 4,  "[L]   Toggle Luggage Sensor")
            stdscr.addstr(6, 4,  "[SPC] Reset All")
            stdscr.addstr(7, 4,  "[Q]   Quit")

            stdscr.addstr(9, 2, "SENSORS & STATE:", curses.A_UNDERLINE)
            
            # Distance Display
            dist_attr = curses.A_NORMAL
            if sim_distance > MAX_FOLLOW_DIST: dist_attr = curses.A_BLINK | curses.A_STANDOUT
            elif abs(sim_distance - TARGET_DISTANCE) > 5: dist_attr = curses.A_BOLD
            stdscr.addstr(10, 4, f"Distance: {sim_distance:.1f} cm", dist_attr)
            stdscr.addstr(10, 30, f"(Target: {TARGET_DISTANCE} | Max: {MAX_FOLLOW_DIST})")
            
            # Heading Display
            head_attr = curses.A_NORMAL
            if abs(sim_heading - TARGET_HEADING) > 2: head_attr = curses.A_BOLD
            stdscr.addstr(11, 4, f"Heading:  {sim_heading:.1f} deg", head_attr)

            # Luggage Display
            lug_attr = curses.A_NORMAL
            lug_status = "SECURE"
            if luggage_fallen: 
                lug_attr = curses.A_BLINK | curses.A_STANDOUT
                lug_status = "FALLEN"
            stdscr.addstr(12, 4, f"Luggage:  {lug_status}", lug_attr)

            # Safety Message
            if safety_active:
                stdscr.addstr(14, 2, f"!!! {safety_msg} !!!", curses.A_BLINK | curses.A_STANDOUT)
            else:
                stdscr.addstr(14, 2, "SYSTEM ACTIVE - FOLLOWING", curses.A_BOLD)

            stdscr.addstr(16, 2, "MOTOR COMMANDS:", curses.A_UNDERLINE)
            
            l_bar = "#" * int(abs(left_speed) / 5)
            r_bar = "#" * int(abs(right_speed) / 5)
            l_dir = "FWD" if left_speed > 0 else "REV"
            r_dir = "FWD" if right_speed > 0 else "REV"
            
            # If stopped, verify direction is neutral
            if left_speed == 0: l_dir = "---"
            if right_speed == 0: r_dir = "---"
            
            stdscr.addstr(17, 4, f"LEFT:  [{l_dir}] {left_speed:.1f}%  {l_bar}")
            stdscr.addstr(18, 4, f"RIGHT: [{r_dir}] {right_speed:.1f}%  {r_bar}")
            
            if not saber.connected:
                 stdscr.addstr(20, 2, "WARNING: Sabertooth not connected (Sim Mode)", curses.A_BLINK)

            stdscr.refresh()

    except Exception as e:
        # Clean exit if crash
        saber.close()
        curses.endwin()
        print(f"Error: {e}")
        sys.exit(1)
    
    finally:
        saber.close()

if __name__ == "__main__":
    curses.wrapper(main)
