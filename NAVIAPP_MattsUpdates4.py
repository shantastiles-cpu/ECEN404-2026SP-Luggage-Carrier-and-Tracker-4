#!/usr/bin/env python3
"""
NAVIAPP_movementtest_merged.py
================================
ARCHITECTURE  : old2.py  (state machine, heartbeat, telemetry buffer,
                           UWB confidence scorer, pairing manager,
                        enhanced GUI)
FOLLOW LOGIC  : movementtest.py  (LocalUwbTracker with is_pose_good(),
                                   UWB-lost safety stop in AUTO mode)
SPEED TUNING  : movementtest.py  (SPEED_CAP=20, TURN_CAP=20)
AVOIDANCE     : movementtest.py  (AV_REV=-15, AV_PIVOT=20)
LOAD CELL     : read_weight.py   (HX711 on DT=GPIO5 / SCK=GPIO6,
                                   auto-calibration on first boot saves to
                                   /home/pi/loadcell_calibration.json,
                                   weight displayed in lb + kg,
                                   luggage_fallen if weight < threshold,
                                   threshold + override pushed from app)
"""
import queue
import os
import json
import glob
import argparse
import logging
import serial
import time
import curses
import sys
import threading
import statistics
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum, auto
import csv
import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

import lgpio as GPIO
from supabase import create_client, Client

try:
    try:
        import RPi.GPIO as RPIGPIO
    except RuntimeError:
        import rpi_lgpio as RPIGPIO
    from hx711 import HX711
    HX711_AVAILABLE = True
except ImportError as e:
    print(f"[LOADCELL] HX711 not available: {e}", file=sys.stderr)
    HX711_AVAILABLE = False


logging.basicConfig(
    filename='/tmp/rover_debug.log', # <--- Route all logs to the background file
    level=logging.WARNING,           # <--- Silence the 'INFO' HTTP spam!
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("uwb_host")
# ======================================================================================
# Validation CLI args  — parsed once at module level so all classes can read them
# ======================================================================================
def _parse_args():
    p = argparse.ArgumentParser(
        description="NAVI Rover Controller with Validation Logging",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--test-name", default="UNSPECIFIED_TEST",
                   help="Short identifier logged in every CSV row, e.g. 'distance_step_response'")
    p.add_argument("--session-id", default=None,
                   help="Session UUID. Auto-generated if omitted.")
    p.add_argument("--log-dir", default="/home/pi5-1/Desktop/Luggage/validation_logs",
                   help="Directory for all validation CSV outputs")
    return p.parse_args()

_ARGS = _parse_args()

VALIDATION_LOG_DIR  = _ARGS.log_dir
CURRENT_TEST_NAME   = _ARGS.test_name
CURRENT_SESSION_ID  = _ARGS.session_id or str(uuid.uuid4())[:8]

# ---------- per-session sub-directory so runs never overwrite each other ----------
_SESSION_DIR = os.path.join(
    VALIDATION_LOG_DIR,
    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{CURRENT_TEST_NAME}_{CURRENT_SESSION_ID}"
)
os.makedirs(_SESSION_DIR, exist_ok=True)

# Canonical CSV paths (all land inside the session folder)
ROVER_CSV_PATH    = os.path.join(_SESSION_DIR, "rover_telemetry.csv")
ROVER_EVENT_PATH  = os.path.join(_SESSION_DIR, "rover_events.csv")
ROVER_LATENCY_PATH= os.path.join(_SESSION_DIR, "rover_latency.csv")

print(f"[VALIDATION] Session dir : {_SESSION_DIR}", file=sys.stderr)
print(f"[VALIDATION] Test name   : {CURRENT_TEST_NAME}", file=sys.stderr)
print(f"[VALIDATION] Session ID  : {CURRENT_SESSION_ID}", file=sys.stderr)

UWB_CSV_PATH   = os.path.join(_SESSION_DIR, "uwb_positions.csv")
UWB_EVENT_PATH = os.path.join(_SESSION_DIR, "uwb_events.csv")


BAUD = 115200
DETECT_TIMEOUT_S = 12
PUBLISH_INTERVAL_S = 0.1

# Height compensation: project 3D slant range to floor-plane horizontal distance
# Set both to 0.0 to disable correction.
ANCHOR_HEIGHT_M = 0.27
TAG_HEIGHT_M = 1.13

# ── D_mid reference point offset ──────────────────────────────────────────────
# By default distance_m is measured from (0,0) — the midpoint between anchors.
# Set these to shift the reference point to somewhere else on the rover.
# e.g. REF_OFFSET_Y = 0.3 means 0.3 m in front of the anchor midpoint.
REF_OFFSET_X = 0.0   # metres, positive = towards anchor 2
REF_OFFSET_Y = 0.12  # metres, positive = in front of wall
MIN_VALID_CONTROL_M = 0.50       # ignore control distances below ~1.64 ft
MAX_CONTROL_DROP_M = 0.35        # ignore sudden drops > ~1.15 ft


AVOID_BUBBLE_MIN_CM = 45.0    # Tight carving for crawling speeds
AVOID_BUBBLE_MAX_CM = 85.0   # Long-range scanning for high speeds

AVOID_BRAKE_MIN_CM  = 30.0    # Late braking for crawling
AVOID_BRAKE_MAX_CM  = 150.0    # Early braking for sprinting

AVOID_MAX_FORCE_CM     = 25.0    # Distance where repulsion hits 100% power
AVOID_CENTER_FORCE     = 0.0     # Gentle push force from Center-Left/Right sensors
AVOID_FENDER_FORCE     = 50.0    # Hard carve force from Left/Right Fender sensors
AVOID_ESCAPE_FORCE     = 35.0    # Base spin force if trapped dead-center
AVOID_BOOST_MULTIPLIER = 1.0     # Multiplier to punch out of dead-center traps


# ======================================================================================
# State Machine Definition  [from old2.py]
# ======================================================================================
class RoverState(Enum):
    WAITING_FOR_PAIR  = auto()
    IDLE              = auto()
    AUTO              = auto()
    MANUAL            = auto()
    MANUAL_OVERRIDE   = auto()   # Manual with ultrasonic hard-stop only
    OBSTACLE_AVOID    = auto()
    HARD_STOP         = auto()
    LOST_UWB          = auto()
    EMERGENCY_STOP    = auto()


# ======================================================================================
# Load Cell Reader  [from read_weight.py — HX711 on DT=GPIO5 / SCK=GPIO6]
# ======================================================================================
# Default threshold: rover stops if weight drops BELOW this value.
# Overridable at runtime via rover_commands.luggage_weight_threshold_kg
LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT = 1.0   # ~2.2 lb — tune to empty-tray weight
LOAD_CELL_REFERENCE_UNIT = 87               # fallback only — auto-calibration overrides this
LOAD_CELL_CALIBRATION_FILE = "/home/pi5-1/Desktop/Luggage/loadcell_calibration.json"
LOAD_CELL_DT_PIN   = 17                      # GPIO5  (BCM)
LOAD_CELL_SCK_PIN  = 27                      # GPIO6  (BCM)
LOAD_CELL_SAMPLES  = 5                      # readings averaged per measurement
LOAD_CELL_POLL_S   = 0.25                   # how often to read (seconds)
DEFAULT_FOLLOW_DISTANCE_CM = 100.0

# Piezo buzzer (weight-loss alarm)
BUZZER_GPIO_PIN = 24   # BCM 24 / physical pin 18
BUZZER_TONE_HZ  = 2200
BUZZER_ARM_MARGIN_KG = 0.0

# Weight unit helpers
def kg_to_lb(kg: float) -> float:
    return kg * 2.20462

def lb_to_kg(lb: float) -> float:
    return lb / 2.20462

def grams_to_lb(g: float) -> float:
    return g * 0.00220462

def fmt_weight(kg: float) -> str:
    """Returns weight as both lb and kg string for display."""
    return f"{kg_to_lb(kg):.2f} lb  ({kg:.3f} kg)"


def load_or_calibrate(hx) -> float:
    """
    Load saved reference unit from calibration file.
    If no file exists, use the fixed reference unit from read_weight.py behavior.
    Do NOT block startup waiting for known-weight calibration.
    """
    if os.path.exists(LOAD_CELL_CALIBRATION_FILE):
        try:
            with open(LOAD_CELL_CALIBRATION_FILE, "r") as f:
                data = json.load(f)
            ref = float(data["reference_unit"])
            cal_date = data.get("calibrated_at", "unknown date")
            print(f"[LOADCELL] Loaded calibration from {LOAD_CELL_CALIBRATION_FILE}", file=sys.stderr)
            print(f"[LOADCELL]   reference_unit={ref:.4f} calibrated={cal_date}", file=sys.stderr)
            return ref
        except Exception as e:
            print(f"[LOADCELL] Failed to load calibration file: {e}", file=sys.stderr)

    print(f"[LOADCELL] No calibration file found — using fixed reference_unit={LOAD_CELL_REFERENCE_UNIT}", file=sys.stderr)
    return float(LOAD_CELL_REFERENCE_UNIT)


class LoadCellReader:
    """
    Background thread that reads the HX711 load cell every LOAD_CELL_POLL_S seconds.
    Exposes:
      weight_kg         — latest smoothed weight (kg)
      luggage_fallen    — True when weight < threshold AND override is OFF
      tare_done         — True once tare has completed on startup
    """

    def __init__(self,
                 threshold_kg: float = LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT,
                 weight_alarm_override: bool = False):
        self._lock             = threading.Lock()
        self._stop             = threading.Event()
        self._thread           = None
        self._hx               = None
        self._recent_weights = deque(maxlen=5)
        self._below_threshold_count = 0
        self._last_good_kg = 0.0
        self._hx_lock = threading.Lock()
        self._tare_in_progress = False

        # Public state (read from main loop)
        self.weight_kg         = 0.0
        self.luggage_fallen    = False
        self.tare_done         = False
        self.hw_available      = False
        self.last_error        = None

        # Tunables — updated from rover_commands / app
        self.threshold_kg      = float(threshold_kg)
        self.weight_alarm_override = bool(weight_alarm_override)

    def set_threshold(self, kg: float):
        with self._lock:
            self.threshold_kg = float(kg)

    def set_override(self, enabled: bool):
        with self._lock:
            self.weight_alarm_override = bool(enabled)

    
    def request_tare(self):
        def _do_tare():
            with self._lock:
                hx = self._hx
                hw_available = self.hw_available
                current_weight = self.weight_kg
                if self._tare_in_progress:
                    print("[LOADCELL] tare skipped: already in progress", file=sys.stderr)
                    return
                self._tare_in_progress = True

            try:
                if not hw_available or hx is None:
                    with self._lock:
                        self.last_error = "tare requested but HX711 is not available"
                    print("[LOADCELL] tare skipped: HX711 not available", file=sys.stderr)
                    return

                if current_weight > 0.2:
                    with self._lock:
                        self.last_error = (
                            f"tare blocked: current weight {current_weight:.3f} kg indicates tray is not empty"
                        )
                    print(f"[LOADCELL] tare blocked: tray not empty ({current_weight:.3f} kg)", file=sys.stderr)
                    return

                print("[LOADCELL] Re-taring with empty tray...", file=sys.stderr)
                with self._hx_lock:
                    hx.reset()
                    time.sleep(0.2)
                    hx.tare(times=20)

                with self._lock:
                    self.tare_done = True
                    self._recent_weights.clear()
                    self._below_threshold_count = 0
                    self.weight_kg = 0.0
                    self.last_error = None

                print("[LOADCELL] Re-tare complete.", file=sys.stderr)

            except Exception as e:
                with self._lock:
                    self.last_error = str(e)
                print(f"[LOADCELL] tare failed: {e}", file=sys.stderr)

            finally:
                with self._lock:
                    self._tare_in_progress = False

        threading.Thread(target=_do_tare, daemon=True).start()

    def start(self):
        if not HX711_AVAILABLE:
            print("[LOADCELL] hx711/RPi.GPIO not installed — load cell disabled", file=sys.stderr)
            with self._lock:
                self.hw_available = False
                self.luggage_fallen = True   # fail safe
                self.last_error = "HX711 library not available"
            return

        try:
            RPIGPIO.setwarnings(False)

            self._hx = HX711(LOAD_CELL_DT_PIN, LOAD_CELL_SCK_PIN)
            self._hx.set_reading_format("MSB", "MSB")
            self._hx.set_reference_unit(load_or_calibrate(self._hx))

            # Match standalone behavior
            self._hx.reset()

            print("[LOADCELL] Taring with empty tray (2s settle)…", file=sys.stderr)
            time.sleep(2)
            self._hx.tare(times=20)

            with self._lock:
                self.tare_done = True
                self.hw_available = True
                self.last_error = None

            print("[LOADCELL] Tare done. Reading started.", file=sys.stderr)

            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        except Exception as e:
            print(f"[LOADCELL] start() failed: {e}", file=sys.stderr)
            with self._lock:
                self.hw_available = False
                self.luggage_fallen = True   # fail safe
                self.last_error = str(e)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._hx is not None:
            try:
                self._hx.power_down()
            except:
                pass
        try:
            RPIGPIO.cleanup()
        except:
            pass

    def get(self) -> dict:
        """Return a snapshot of current load-cell state (thread-safe)."""
        with self._lock:
            return {
                "weight_kg":         self.weight_kg,
                "luggage_fallen":    self.luggage_fallen,
                "tare_done":         self.tare_done,
                "hw_available":      self.hw_available,
                "threshold_kg":      self.threshold_kg,
                "override":          self.weight_alarm_override,
                "last_error":        self.last_error,
            }

    def _run(self):
        while not self._stop.is_set():
            try:
                with self._lock:
                    tare_in_progress = self._tare_in_progress

                if tare_in_progress:
                    time.sleep(0.1)
                    continue

                with self._hx_lock:
                    grams = self._hx.get_weight(5)
                    print(f"[LOADCELL_RAW] grams={grams}", file=sys.stderr)

                    kg = max(0.0, grams / 1000.0)

                    self._hx.power_down()
                    self._hx.power_up()

                with self._lock:
                    self.weight_kg = kg
                    if not self.weight_alarm_override:
                        self.luggage_fallen = (kg < self.threshold_kg)
                    else:
                        self.luggage_fallen = False
                    self.last_error = None

                print(f"[LOADCELL_OK] kg={kg:.3f}", file=sys.stderr)

            except Exception as e:
                with self._lock:
                    self.last_error = str(e)
                print(f"[LOADCELL] read error: {e}", file=sys.stderr)

            time.sleep(0.2)
    # def _run(self):
    #     while not self._stop.is_set():
    #         try:
    #             grams = self._hx.get_weight(LOAD_CELL_SAMPLES)
    #             print(f"[LOADCELL_RAW] grams={grams}", file=sys.stderr)
    #             self._hx.power_down()
    #             time.sleep(0.05)
    #             self._hx.power_up()
    #             time.sleep(0.05)

    #             kg_raw = grams / 1000.0
    #             print(f"[LOADCELL_OK] kg_raw={kg_raw:.3f} kg={kg:.3f}", file=sys.stderr)

    #             # clamp small negatives to zero
    #             if kg_raw < 0:
    #                 kg_raw = 0.0

    #             # reject impossible spikes
    #             # tune 60.0 to your actual max luggage weight
    #             if kg_raw > 60.0:
    #                 raise ValueError(f"Rejected unrealistic load cell value: {kg_raw:.3f} kg")

    #             # reject sudden one-sample jumps
    #             if self._recent_weights and abs(kg_raw - self._recent_weights[-1]) > 15.0:
    #                 raise ValueError(f"Rejected sudden jump: {self._recent_weights[-1]:.3f} -> {kg_raw:.3f} kg")

    #             self._recent_weights.append(kg_raw)
    #             kg = statistics.median(self._recent_weights)
    #             self._last_good_kg = kg

    #             with self._lock:
    #                 self.weight_kg = kg

    #                 if not self.weight_alarm_override:
    #                     if kg < self.threshold_kg:
    #                         self._below_threshold_count += 1
    #                     else:
    #                         self._below_threshold_count = 0

    #                     # require 3 consecutive low reads
    #                     self.luggage_fallen = self._below_threshold_count >= 3
    #                 else:
    #                     self._below_threshold_count = 0
    #                     self.luggage_fallen = False

    #                 self.last_error = None

    #         except Exception as e:
    #             with self._lock:
    #                 self.last_error = str(e)
    #                 # keep last known weight, do not force a bogus new value
    #             print(f"[LOADCELL] read error: {e}", file=sys.stderr)

    #         time.sleep(LOAD_CELL_POLL_S)


class PiezoAlarm:
    """
    Continuous Siren Alarm (Latched & Active-Low)
    """

    def __init__(self, gpio_pin: int = BUZZER_GPIO_PIN, tone_hz: float = BUZZER_TONE_HZ):
        self.gpio_pin = int(gpio_pin)
        self._chip = None
        self._lock = threading.Lock()

        # State tracking
        self._armed = False
        self._alarm_latched = False
        self.hw_available = False

        # Hardware logic: Active-Low
        self.SILENT = 1  # HIGH keeps it quiet
        self.SIREN = 0  # LOW sounds the alarm

        self._pin_state = None

    def start(self):
        try:
            self._chip = GPIO.gpiochip_open(0)
            GPIO.gpio_claim_output(self._chip, self.gpio_pin)
            self._write(self.SILENT)  # Default to silent on startup
            self.hw_available = True
        except Exception as e:
            self.hw_available = False
            print(f"[BUZZER] start() failed: {e}", file=sys.stderr)

    def stop(self):
        try:
            if self._chip is not None:
                # Float the pin so your physical pull-up resistor can take over
                GPIO.gpio_free(self._chip, self.gpio_pin)
                GPIO.gpio_claim_input(self._chip, self.gpio_pin)
                GPIO.gpiochip_close(self._chip)
        except Exception as e:
            print(f"[BUZZER] Shutdown error: {e}", file=sys.stderr)

        self._chip = None
        self._pin_state = self.SILENT

    def update(self, weight_kg: float, threshold_kg: float, hw_available: bool, override: bool, state_name: str,
               uwb_age_s: float):
        with self._lock:
            # 1. Hardware unavailable
            if not self.hw_available or not hw_available:
                self._write(self.SILENT)
                return

            # 2. Priority 1: Luggage Alarm (Continuous Siren)
            # Still safely locked behind the override switch!
            if not bool(override) and state_name in ["AUTO", "HARD_STOP"]:
                if float(weight_kg) < float(threshold_kg):
                    self._write(self.SIREN)
                    return  # Exit early, continuous siren wins

            # 3. Priority 2: UWB Diagnostic Alarm (Beep Pattern)
            # THE FIX: Removed the override check here! This will now monitor
            # the UWB tracking data 24/7, regardless of the luggage switch.
            if uwb_age_s is not None and uwb_age_s > 3.0:
                cycle_time = time.time() % 2.0

                # Beep 1: 0.0s to 0.15s | Beep 2: 0.3s to 0.45s
                if (0.0 <= cycle_time < 0.15) or (0.3 <= cycle_time < 0.45):
                    self._write(self.SIREN)
                else:
                    self._write(self.SILENT)
                return

            # 4. Otherwise, stay silent
            self._write(self.SILENT)

    def _write(self, level: int):
        if self._chip is None:
            return
            
        if level != self._pin_state:
            try:
                # THE FIX: Clean, standard GPIO output now that you are on 3.3V power!
                GPIO.gpio_write(self._chip, self.gpio_pin, level)
                self._pin_state = level
            except Exception as e:
                print(f"[BUZZER] write failed: {e}", file=sys.stderr)


# ======================================================================================
# Telemetry Buffer  [from old2.py]
# ======================================================================================
class _CsvBase:
    """Shared CSV writer used by all logger classes below."""
    def __init__(self, path: str, fieldnames: list):
        self.path       = path
        self.fieldnames = fieldnames
        self._lock      = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="") as f:   # always create fresh per session
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    def write_row(self, row: dict):
        with self._lock:
            with open(self.path, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=self.fieldnames).writerow(row)


class CsvTelemetryLogger(_CsvBase):
    """1 Hz snapshot of all rover state — covers most validation tests."""
    def __init__(self, path: str):
        super().__init__(path, [
            "ts_iso", "session_id", "test_name",
            "state", "mode",
            "distance_m", "target_distance_cm",
            "heading_deg", "target_heading_deg",
            "left_speed_cmd", "right_speed_cmd",
            
            # THE FIX: Add the two new columns here!
            "dynamic_bubble_cm", "dynamic_brake_cm",
            
            "obstacle_hold", "obstacle_avoid_active", "obstacle_reason",
            "uwb_live", "uwb_confidence",
            "loop_time_ms",                       
            "cpu_temp_c",                         
            "uptime_s",                           
            "telem_buffer_size",                  
            "weight_kg", "luggage_fallen", "weight_alarm_override",
            "front_cm", "left_cm", "right_cm", "back_cm",
            "paired",                             
            "uwb_override_enabled",               
            "anchor1_connected", "anchor2_connected",  
        ])


class CsvEventLogger(_CsvBase):
    """
    Discrete event log — one row per notable state transition or safety event.
    Written immediately when the event fires (not on a timer).
    Covers: Secure Pairing Gate, LOST_UWB Failsafe, UWB Override Recovery,
            Anchor Disruption, Bounds Safety, Weight Alarm, Obstacle events,
            Telemetry Buffer flush.
    """
    def __init__(self, path: str):
        super().__init__(path, [
            "ts_iso", "session_id", "test_name",
            "event_type",    # e.g. LOST_UWB, PAIR_CONFIRMED, BOUNDS_STOP, OBSTACLE_HARD_STOP …
            "detail",        # human-readable context string
            "state_before",
            "state_after",
            "distance_m",
            "uwb_live",
            "weight_kg",
            "luggage_fallen",
        ])

    def log(self, event_type: str, detail: str = "",
            state_before: str = "", state_after: str = "",
            distance_m: float = 0.0, uwb_live: bool = False,
            weight_kg: float = 0.0, luggage_fallen: bool = False):
        self.write_row({
            "ts_iso":        datetime.now(timezone.utc).isoformat(),
            "session_id":    CURRENT_SESSION_ID,
            "test_name":     CURRENT_TEST_NAME,
            "event_type":    event_type,
            "detail":        detail,
            "state_before":  state_before,
            "state_after":   state_after,
            "distance_m":    round(distance_m, 3),
            "uwb_live":      uwb_live,
            "weight_kg":     round(weight_kg, 3),
            "luggage_fallen": luggage_fallen,
        })


class CsvLatencyLogger(_CsvBase):
    """
    High-rate latency log — measures UWB-update-to-motor-command delay.
    Covers: End-to-End Latency test.
    Written every loop iteration (not throttled), only while in AUTO mode
    and UWB is live.
    """
    def __init__(self, path: str):
        super().__init__(path, [
            "ts_iso", "session_id", "test_name",
            "uwb_updated_at_iso",   # timestamp of the UWB row that drove this command
            "motor_cmd_ts_iso",     # timestamp motor bytes were sent
            "latency_ms",           # delta in milliseconds
            "distance_m",
            "heading_deg",
            "left_speed_cmd",
            "right_speed_cmd",
        ])


class TelemetryBuffer:
    """Buffers telemetry when Supabase is unavailable; retries later."""
    MAX_BUFFER = 50

    def __init__(self):
        self._buffer = deque(maxlen=self.MAX_BUFFER)
        self._lock = threading.Lock()

    def add(self, data: dict):
        with self._lock:
            self._buffer.append(data)

    def flush_to(self, writer):
        with self._lock:
            failed = deque()
            while self._buffer:
                entry = self._buffer.popleft()
                try:
                    writer.supabase.table('telemetry_snapshots').insert(entry).execute()
                except:
                    failed.append(entry)
            self._buffer.extendleft(reversed(failed))

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)


# ======================================================================================
# Heartbeat Monitor  [from old2.py]
# ======================================================================================
class HeartbeatMonitor:
    """Writes robot health status every 1 s to robot_heartbeat table."""

    def __init__(self, supabase: Client, robot_id: str):
        self._supa       = supabase
        self._robot_id   = robot_id
        self._stop       = threading.Event()
        self._thread     = None
        self.cpu_temp    = 0.0
        self.loop_time_ms = 0.0
        self.uwb_live    = False
        self.ultra_active = False
        self.uptime_s    = 0.0
        self._start_time = time.monotonic()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def update(self, loop_time_ms: float, uwb_live: bool, ultra_active: bool):
        self.loop_time_ms = loop_time_ms
        self.uwb_live     = uwb_live
        self.ultra_active = ultra_active
        self.uptime_s     = time.monotonic() - self._start_time
        self.cpu_temp     = self._read_cpu_temp()

    def _read_cpu_temp(self) -> float:
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                return float(f.read().strip()) / 1000.0
        except:
            return 0.0

    def _run(self):
        while not self._stop.is_set():
            try:
                data = {
                    'robot_id':     self._robot_id,
                    'cpu_temp':     round(self.cpu_temp, 1),
                    'uptime_s':     round(self.uptime_s, 1),
                    'loop_time_ms': round(self.loop_time_ms, 1),
                    'uwb_live':     self.uwb_live,
                    'ultra_active': self.ultra_active,
                    'updated_at':   datetime.now(timezone.utc).isoformat(),
                }
                self._supa.table('robot_heartbeat').upsert(
                    data, on_conflict='robot_id'
                ).execute()
            except Exception as e:
                print(f"[HEARTBEAT] Error: {e}", file=sys.stderr)
            time.sleep(1.0)


# ======================================================================================
# UWB Confidence Calculator  [from old2.py]
# ======================================================================================
class UwbConfidence:
    """Calculates UWB position confidence (0-100) from age + geometry."""

    @staticmethod
    def calculate(age_s: float, d1_m: float, d2_m: float,
                  anchor_sep_m: float = 0.5) -> dict:
        # Age scoring
        if age_s < 1.0:
            age_score = 100.0
        elif age_s < 2.0:
            age_score = 100.0 * (2.0 - age_s)
        else:
            age_score = 0.0

        # Geometry scoring
        anchor_imbalance   = abs(d1_m - d2_m)
        max_good_imbalance = anchor_sep_m * 2.0
        if anchor_imbalance < max_good_imbalance:
            geom_score = 100.0
        else:
            excess     = anchor_imbalance - max_good_imbalance
            geom_score = max(0.0, 100.0 - (excess / max_good_imbalance) * 50.0)

        confidence = age_score * 0.6 + geom_score * 0.4
        return {
            'confidence':       round(confidence, 1),
            'age_penalty':      round(100.0 - age_score, 1),
            'geometry_penalty': round(100.0 - geom_score, 1),
            'is_reliable':      confidence >= 70.0,
        }


# ======================================================================================
# Pairing Manager  [from old2.py]
# ======================================================================================
PAIR_RECONNECT_GRACE_S = 120  # accept an existing paired session for 2 minutes after rover reboot

class PairingManager:
    def __init__(self, supabase: Client, robot_id: str):
        self._supa     = supabase
        self._robot_id = robot_id
        self._lock     = threading.Lock()
        self._token    = None
        self._stop     = threading.Event()
        self._thread   = threading.Thread(target=self._poll, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def is_paired(self) -> bool:
        with self._lock:
            return self._token is not None

    def validate(self, token) -> bool:
        with self._lock:
            if self._token is None:
                return False
            return token == self._token

    def get_token(self):
        with self._lock:
            return self._token

    def _within_reconnect_grace(self, confirmed_at_str: str) -> bool:
        if not confirmed_at_str:
            return False
        try:
            dt = datetime.fromisoformat(str(confirmed_at_str))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_s = (datetime.now(timezone.utc) - dt).total_seconds()
            return age_s <= PAIR_RECONNECT_GRACE_S
        except Exception:
            return False

    def _poll(self):
        print("[PAIR] Waiting for app pairing...", file=sys.stderr)
        while not self._stop.is_set():
            try:
                result = (
                    self._supa.table('pairing_sessions')
                    .select('session_token, paired, expires_at, confirmed_at')
                    .eq('robot_id', self._robot_id)
                    .execute()
                )
                if result.data:
                    row = result.data[0]

                    already_paired = row.get('paired', False)
                    token = row.get('session_token')
                    expires_str = row.get('expires_at', '')
                    confirmed_at = row.get('confirmed_at', '')

                    if token and not already_paired and self._not_expired(expires_str):
                        self._confirm_pairing(token)

                    elif token and already_paired:
                        with self._lock:
                            rover_has_no_local_token = self._token is None
                            rover_token_mismatch = (self._token is not None and self._token != token)

                        # Case 1: Rover rebooted and lost local token, but DB still has a recent valid paired session.
                        if rover_has_no_local_token and self._within_reconnect_grace(confirmed_at):
                            with self._lock:
                                self._token = token
                            print("[PAIR] Restored recent paired session after reboot", file=sys.stderr)

                        # Case 2: App created/replaced session token while rover is running.
                        elif rover_token_mismatch:
                            with self._lock:
                                self._token = token
                            print("[PAIR] Re-synced to updated session token", file=sys.stderr)

                        # Refresh confirmation timestamp so reconnect window stays alive
                        try:
                            self._supa.table('pairing_sessions').update({
                                'confirmed_at': datetime.now(timezone.utc).isoformat(),
                            }).eq('robot_id', self._robot_id).execute()
                        except Exception as e:
                            print(f"[PAIR] Re-pair stamp failed: {e}", file=sys.stderr)

            except Exception as e:
                print(f"[PAIR] Poll error: {e}", file=sys.stderr)

            time.sleep(1.0)

    def _not_expired(self, expires_str: str) -> bool:
        if not expires_str:
            return False
        try:
            dt = datetime.fromisoformat(str(expires_str))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < dt
        except:
            return False

    def _confirm_pairing(self, token: str):
        try:
            self._supa.table('pairing_sessions').update({
                'paired': True,
                'confirmed_at': datetime.now(timezone.utc).isoformat(),
            }).eq('robot_id', self._robot_id).execute()
            with self._lock:
                self._token = token
            print("[PAIR] ✓ Paired successfully", file=sys.stderr)
        except Exception as e:
            print(f"[PAIR] Confirm failed: {e}", file=sys.stderr)



# ======================================================================================
# UWB Publisher
# ======================================================================================
class UwbPublisher:
    def __init__(self, url, key, robot_id, anchor_sep_m):
        self._supa: Client = create_client(url, key)
        self._robot_id = robot_id
        self._anchor_sep_m = anchor_sep_m
        self._last_write = 0.0
        self._publish_count = 0
        self._publish_start = time.monotonic()

        # --- NEW: Single Worker Queue ---
        self._queue = queue.Queue(maxsize=10)  # Max 10 items in line before dropping old ones
        self._worker = threading.Thread(target=self._process_queue, daemon=True)
        self._worker.start()

        log.info(f"[Supabase] Connected for robot_id='{robot_id}'")

    def _process_queue(self):
        """Runs forever in the background, executing writes one by one."""
        while True:
            payload = self._queue.get()
            try:
                self._supa.table("uwb_positions").upsert(payload, on_conflict="robot_id").execute()
            except Exception:
                pass  # Fail silently if Wi-Fi drops
            self._queue.task_done()

    def publish(self, x_m, y_m, angle_deg, d1_m, d2_m):
        now = time.monotonic()
        if now - self._last_write < PUBLISH_INTERVAL_S:
            return
        self._last_write = now
        self._publish_count += 1

        distance_control_m = math.hypot(x_m - REF_OFFSET_X, y_m - REF_OFFSET_Y)
        distance_display_m = math.hypot(x_m, y_m)
        confidence = self._calculate_confidence(d1_m, d2_m, y_m)

        payload = {
            "robot_id": self._robot_id,
            "x_m": round(x_m, 4),
            "y_m": round(y_m, 4),
            "angle_deg": round(angle_deg, 3),
            "distance_m": round(distance_display_m, 4),
            "d1_m": round(d1_m, 4),
            "d2_m": round(d2_m, 4),
            "confidence": round(confidence, 1),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Put the payload in the waiting line. If line is full, skip it.
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def _calculate_confidence(self, d1_m, d2_m, y_m):
        imbalance = abs(d1_m - d2_m)
        max_good = self._anchor_sep_m * 2.0
        if imbalance < max_good:
            imbalance_score = 100.0
        else:
            imbalance_score = max(0.0, 100.0 - ((imbalance - max_good) / max_good) * 50.0)
        if y_m < 2.0:
            distance_score = 100.0
        elif y_m < 5.0:
            distance_score = 100.0 - ((y_m - 2.0) / 3.0) * 20.0
        else:
            distance_score = max(50.0, 80.0 - (y_m - 5.0) * 5.0)
        return max(0.0, min(100.0, imbalance_score * 0.6 + distance_score * 0.4))

    def get_publish_rate(self):
        elapsed = time.monotonic() - self._publish_start
        return 0.0 if elapsed < 1.0 else self._publish_count / elapsed
    


class LocalUwbTracker:
    def __init__(self, supabase_url, supabase_key, robot_id,
                 a1x=-0.325, a2x=0.325, stale_s=1.5, min_conf=30.0,
                 publish_to_supabase=True):
        self.robot_id = robot_id
        self.stale_s = stale_s
        self.min_conf = min_conf
        self.publish_to_supabase = publish_to_supabase
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_good_control_m = None
        self._last_good_display_m = None

        self.pose = {
            "distance_cm": None,
            "distance_display_cm": None,
            "angle_deg": None,
            "confidence": None,
            "age_s": None,
            "updated_at": None,
            "a1_connected": None,
            "a2_connected": None,
            "a1_age_s": None,
            "a2_age_s": None,
            "last_error": None,
        }

        self.a1x = a1x
        self.a2x = a2x
        self.geometry = AnchorGeometry.validate((a1x, 0.0), (a2x, 0.0))

        self.supabase = create_client(supabase_url, supabase_key) if publish_to_supabase else None
        self.health_monitor = UwbHealthMonitor(self.supabase, robot_id) if publish_to_supabase else None
        self.publisher = UwbPublisher(supabase_url, supabase_key, robot_id, self.geometry["separation_m"]) if publish_to_supabase else None
        # --- NEW: X/Y Smoothing State ---
        self._ema_x = None
        self._ema_y = None
        self.EMA_ALPHA = 0.25  # The shock-absorber. Lower = smoother but slight delay.
        
        self.readers = []
        self.thread = threading.Thread(target=self._run, daemon=True)

        self.uwb_csv = CsvSimpleLogger(UWB_CSV_PATH, [
            "ts_iso", "session_id", "test_name",
            "x_m", "y_m", "angle_deg", "distance_m",
            "d1_m", "d2_m", "confidence",
            "anchor1_connected", "anchor2_connected",
            "anchor1_age_s", "anchor2_age_s",
            "publish_rate_hz", "geometry_valid", "geometry_warnings",
        ])

        self.uwb_event_logger = CsvUwbEventLogger(UWB_EVENT_PATH)


    def start(self):
        ports = detect_anchor_ports()
        if len(ports) < 2:
            raise RuntimeError("Need 2 anchor ports")

        self.readers = [
            AnchorReader(1, ports[0], median_window=3),
            AnchorReader(2, ports[1], median_window=3),
        ]
        for r in self.readers:
            r.start()

        if self.health_monitor:
            self.health_monitor.start()

        self.thread.start()

    def stop(self):
        self._stop.set()
        for r in self.readers:
            r.stop()
        if self.health_monitor:
            self.health_monitor.stop()

    # inside LocalUwbTracker.get()
    def get(self):
        with self._lock:
            pose = dict(self.pose)
        upd = pose.get("updated_at")
        if upd is not None:
            pose["age_s"] = (datetime.now(timezone.utc) - upd).total_seconds()
        else:
            pose["age_s"] = None
        return pose

    def is_pose_good(self, pose):
        if pose.get("distance_cm") is None or pose.get("angle_deg") is None:
            return False
        age_s = pose.get("age_s")
        if age_s is None or age_s > self.stale_s:
            return False
        conf = pose.get("confidence")
        if self.min_conf is not None and conf is not None and conf < self.min_conf:
            return False
        if pose.get("a1_connected") is False or pose.get("a2_connected") is False:
            return False
        return True

    def _run(self):
        last_ts1 = 0.0
        last_ts2 = 0.0
        anchor_sep = abs(self.a2x - self.a1x)

        while not self._stop.is_set():
            try:
                now = time.monotonic()
                d1, ts1, conn1 = self.readers[0].get()
                d2, ts2, conn2 = self.readers[1].get()

                if d1 is None or d2 is None:
                    time.sleep(0.01)
                    continue

                # Only recalculate if at least one anchor has a new reading
                if ts1 == last_ts1 and ts2 == last_ts2:
                    time.sleep(0.005)
                    continue

                last_ts1 = ts1
                last_ts2 = ts2
                
                age1 = now - ts1
                age2 = now - ts2

                # Discard if either reading is stale
                if age1 > self.stale_s or age2 > self.stale_s:
                    time.sleep(0.01)
                    continue

                pos = solve_position(d1, d2, anchor_sep)
                if pos is None:
                    time.sleep(0.01)
                    continue

                raw_x, raw_y = pos

                # ==========================================
                # NEW: Apply Exponential Smoothing to X & Y
                # ==========================================
                if self._ema_x is None:
                    self._ema_x = raw_x
                    self._ema_y = raw_y
                else:
                    self._ema_x = (self.EMA_ALPHA * raw_x) + ((1 - self.EMA_ALPHA) * self._ema_x)
                    self._ema_y = (self.EMA_ALPHA * raw_y) + ((1 - self.EMA_ALPHA) * self._ema_y)

                # Overwrite x and y with the smoothed values for the rest of the loop
                x = self._ema_x
                y = self._ema_y

                # Calculate angles and distances using the smooth coordinates
                angle_deg = math.degrees(math.atan2(x - REF_OFFSET_X, y - REF_OFFSET_Y))
                distance_control_m = math.hypot(x - REF_OFFSET_X, y - REF_OFFSET_Y)
                distance_display_m = math.hypot(x, y)
                # ==========================================

                reject_control = False

                if distance_control_m < MIN_VALID_CONTROL_M:
                    if self._last_good_control_m is not None:
                        if (self._last_good_control_m - distance_control_m) > MAX_CONTROL_DROP_M:
                            reject_control = True

                if reject_control:
                    print(
                        f"[UWB_REJECT] control_m={distance_control_m:.3f} "
                        f"last_good_control_m={self._last_good_control_m:.3f}",
                        file=sys.stderr,
                    )
                    time.sleep(0.005)
                    continue

                confidence = UwbPublisher._calculate_confidence(self.publisher, d1, d2, y) if self.publisher else 100.0
                updated_at = datetime.now(timezone.utc)

                with self._lock:
                    self.pose = {
                        "distance_cm": distance_control_m * 100.0,
                        "distance_display_cm": distance_display_m * 100.0,
                        "angle_deg": angle_deg,
                        "confidence": confidence,
                        "age_s": 0.0,
                        "updated_at": updated_at,
                        "a1_connected": conn1,
                        "a2_connected": conn2,
                        "a1_age_s": age1,
                        "a2_age_s": age2,
                        "last_error": None,
                    }
                    self._last_good_control_m = distance_control_m
                    self._last_good_display_m = distance_display_m
                    
                if self.publisher:
                    self.publisher.publish(x, y, angle_deg, d1, d2)
                if self.health_monitor:
                    self.health_monitor.update(conn1, conn2, d1, d2, age1, age2,
                                               self.publisher.get_publish_rate() if self.publisher else 0.0)
                            
                pub_rate = self.publisher.get_publish_rate() if self.publisher else 0.0

                self.uwb_csv.write_row({
                    "ts_iso":            datetime.now(timezone.utc).isoformat(),
                    "session_id":        CURRENT_SESSION_ID,
                    "test_name":         CURRENT_TEST_NAME,
                    "x_m":               round(x, 4),
                    "y_m":               round(y, 4),
                    "angle_deg":         round(angle_deg, 3),
                    "distance_m":        round(distance_display_m, 4),
                    "d1_m":              round(d1, 4),
                    "d2_m":              round(d2, 4),
                    "confidence":        round(confidence, 1),
                    "anchor1_connected": conn1,
                    "anchor2_connected": conn2,
                    "anchor1_age_s":     round(age1, 3),
                    "anchor2_age_s":     round(age2, 3),
                    "publish_rate_hz":   round(pub_rate, 2),
                    "geometry_valid":    self.geometry["is_valid"],
                    "geometry_warnings": " | ".join(self.geometry["warnings"]),
                    })
                
            except Exception as e:
                with self._lock:
                    self.pose["last_error"] = str(e)
                time.sleep(0.01)

# ======================================================================================
# Command Listener  [old2.py — includes pairing validation, smart_recovery,
#                    manual_override_mode, uwb_override_enabled]
# ======================================================================================
class CommandListener:
    def __init__(self, supabase_url, supabase_key, robot_id, pairing_manager=None):
        self.supabase        = create_client(supabase_url, supabase_key)
        self.robot_id        = robot_id
        self.pairing_manager = pairing_manager
        self.stop_event      = threading.Event()

        self.mode                   = 'auto'
        self.manual_override_mode   = False
        self.manual_left_speed      = 0.0
        self.manual_right_speed     = 0.0
        self.emergency_stop         = False
        self.target_distance        = DEFAULT_FOLLOW_DISTANCE_CM
        self.target_heading         = 0.0
        self.reset_luggage          = False
        self.command_version        = 0
        self.obstacle_avoid_enabled = True
        self.obstacle_threshold_cm  = 40
        self.obstacle_clear_margin_cm = 8
        self.obstacle_action        = "avoid"
        self.clear_obstacle_override = False
        self.smart_recovery         = False
        self.uwb_override_enabled   = False
        self.recalibrate_luggage    = False   # full recalibration (delete cal file + re-run)
        # Load cell fields
        self.luggage_weight_threshold_kg = LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT
        self.weight_alarm_override       = False

        self.thread = threading.Thread(target=self._poll_commands, daemon=True)

    def start(self):
        self._fetch_latest()
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        try:
            self.thread.join(timeout=1.0)
        except:
            pass

    def _fetch_latest(self):
        try:
            result = (
                self.supabase.table('rover_commands')
                .select('*')
                .eq('robot_id', self.robot_id)
                .order('command_updated_at', desc=True)
                .limit(1)
                .execute()
            )
            if result.data and len(result.data) > 0:
                cmd = result.data[0]

                if self.pairing_manager is not None:
                    incoming_token = cmd.get('session_token')
                    if self.pairing_manager is not None and not self.pairing_manager.is_paired():
                        print("[CMD] ignored — not yet paired", file=sys.stderr)
                        return
                    if self.pairing_manager is not None and not self.pairing_manager.validate(cmd.get('session_token')):
                        print("[CMD] ignored — token mismatch", file=sys.stderr)
                        return

                self.command_version        = cmd.get('command_version', self.command_version)
                self.mode                   = cmd.get('mode', 'auto')
                self.manual_override_mode   = cmd.get('manual_override_mode', False)
                self.manual_left_speed      = float(cmd.get('manual_left_speed') or 0.0)
                self.manual_right_speed     = float(cmd.get('manual_right_speed') or 0.0)
                self.emergency_stop         = cmd.get('emergency_stop', False)
                self.target_distance        = float(cmd.get('target_distance') or 100.0)
                self.target_heading         = float(cmd.get('target_heading') or 0.0)
                self.reset_luggage          = cmd.get('reset_luggage', False)
                self.obstacle_avoid_enabled = cmd.get('obstacle_avoid_enabled', True)
                self.obstacle_threshold_cm  = int(cmd.get('obstacle_threshold_cm') or 45)
                self.obstacle_clear_margin_cm = int(cmd.get('obstacle_clear_margin_cm') or 8)
                self.obstacle_action        = cmd.get('obstacle_action') or 'avoid'
                self.clear_obstacle_override = cmd.get('clear_obstacle_override', False)
                self.smart_recovery         = cmd.get('smart_recovery', False)
                self.uwb_override_enabled   = cmd.get('uwb_override_enabled', False)
                self.recalibrate_luggage    = bool(cmd.get('recalibrate_luggage', False))
                # Load cell tunables pushed from app
                self.luggage_weight_threshold_kg = float(
                    cmd.get('luggage_weight_threshold_kg') or LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT
                )
                self.weight_alarm_override = bool(
                    cmd.get('weight_alarm_override', False)
                )

                print(
                    f"[POLL] mode={self.mode} "
                    f"L={self.manual_left_speed:.1f} "
                    f"R={self.manual_right_speed:.1f} "
                    f"thr_kg={self.luggage_weight_threshold_kg:.3f} "
                    f"override={self.weight_alarm_override} "
                    f"v={self.command_version}",
                    file=sys.stderr
                )

        except Exception as e:
            print(f"[CMD] Poll error: {e}", file=sys.stderr)

    def _poll_commands(self):
        while not self.stop_event.is_set():
            try:
                self._fetch_latest()
                time.sleep(0.05)
            except:
                time.sleep(1.0)


# ======================================================================================
# Supabase Writer  [old2.py — telemetry buffer + obstacle alerts]
# ======================================================================================
class SupabaseWriter:
    def __init__(self, supabase_url, supabase_key, robot_id):
        self.supabase = create_client(supabase_url, supabase_key)
        self.robot_id = robot_id
        self.telem_buffer = TelemetryBuffer()

        # --- THE FIX: A Multi-Threaded Worker Pool ---
        self._queue = queue.Queue(maxsize=150)

        # Spin up 4 parallel workers instead of 1 so the Pi can
        # process multiple Supabase HTTP requests simultaneously!
        for _ in range(4):
            worker = threading.Thread(target=self._process_queue, daemon=True)
            worker.start()
        
    def _process_queue(self):
        while True:
            job = self._queue.get()
            action, table, data, conflict_col = job
            try:
                if action == "upsert":
                    self.supabase.table(table).upsert(data, on_conflict=conflict_col).execute()
                elif action == "insert":
                    self.supabase.table(table).insert(data).execute()

                # If telemetry succeeded, flush any backlog
                if table == 'telemetry_snapshots' and self.telem_buffer.size() > 0:
                    self.telem_buffer.flush_to(self)
            except Exception:
                if table == 'telemetry_snapshots':
                    self.telem_buffer.add(data)
            self._queue.task_done()

    def _enqueue(self, action, table, data, conflict_col=None):
        try:
            self._queue.put_nowait((action, table, data, conflict_col))
        except queue.Full:
            pass

    def write_nav_state(self, left_speed, right_speed):
        data = {
            'robot_id': self.robot_id,
            'left_speed_cmd': float(left_speed),
            'right_speed_cmd': float(right_speed),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._enqueue("upsert", "nav_state", data, "robot_id")

    def write_obstacle_alert(self, reason: str):
        data = {
            'robot_id': self.robot_id,
            'alert_type': 'obstacle',
            'message': reason,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        self._enqueue("insert", "rover_alerts", data)

    def write_live_state(self, mode, state, weight_kg, obstacle_hold, arrived,
                         obstacle_avoid_active=False, luggage_fallen=False,
                         obstacle_reason=None):
        data = {
            'robot_id': self.robot_id,
            'mode': mode,
            'state': state,
            'weight_kg': round(float(weight_kg), 3),
            'obstacle_hold': bool(obstacle_hold),
            'arrived': bool(arrived),
            'obstacle_avoid_active': bool(obstacle_avoid_active),
            'luggage_fallen': bool(luggage_fallen),
            'obstacle_reason': obstacle_reason or "",
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._enqueue("upsert", "rover_live_state", data, "robot_id")

    def write_telemetry(self, distance_m, weight_kg, obstacle_hold, arrived,
                        ultra=None, ultra_reason=None, obstacle_avoid_active=False,
                        uwb_live=False, uwb_confidence=None,
                        loop_time_ms=0.0, state=None,
                        luggage_fallen=False, weight_alarm_override=False,
                        luggage_weight_threshold_kg=LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT):
        ultra = ultra or {}

        def to_int(v): return None if v is None else int(v)

        data = {
            'robot_id': self.robot_id,
            'distance_meters': float(distance_m),
            'weight_kg': round(float(weight_kg), 3),
            'obstacle_hold': bool(obstacle_hold),
            'arrived': bool(arrived),
            'obstacle_reason': ultra_reason,
            'obstacle_avoid_active': bool(obstacle_avoid_active),
            'ultra_front_cm': to_int(ultra.get('front')),
            'ultra_right_cm': to_int(ultra.get('right')),
            'ultra_left_cm': to_int(ultra.get('left')),
            'ultra_back_cm': to_int(ultra.get('back')),
            'uwb_live': bool(uwb_live),
            'uwb_confidence': round(uwb_confidence or 0.0, 1),
            'loop_time_ms': round(loop_time_ms, 1),
            'state': state or 'UNKNOWN',
            'weight_alarm_override': bool(weight_alarm_override),
            'luggage_weight_threshold_kg': round(float(luggage_weight_threshold_kg), 3),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        self._enqueue("insert", "telemetry_snapshots", data)

    def write_obstacle_sensor_debug(self, state, obstacle_reason, obstacle_hold,
                                    obstacle_avoid_active, ultra=None):
        ultra = ultra or {}
        def to_int(v): return None if v is None else int(v)

        data = {
            "robot_id": self.robot_id,
            "state": state,
            "obstacle_reason": obstacle_reason,
            "obstacle_hold": bool(obstacle_hold),
            "obstacle_avoid_active": bool(obstacle_avoid_active),
            "ultra_front_cm": to_int(ultra.get("front")),
            "ultra_left_cm": to_int(ultra.get("left")),
            "ultra_right_cm": to_int(ultra.get("right")),
            "ultra_back_cm": to_int(ultra.get("back")),
        }
        self._enqueue("insert", "obstacle_sensor_debug", data)

# ======================================================================================
# Ultrasonic Avoidance (Vector Repulsion Field)
# ======================================================================================
SENSORS = {
    "front": {"trig": 23, "echo": 12},
    "right": {"trig": 25, "echo": 16},
    "left": {"trig": 8, "echo": 20},
    "back": {"trig": 7, "echo": 21},
}

EMA_ALPHA = 0.65
SAMPLES = 2
PING_GAP = 0.01
SPEED_CM_S = 34300
ECHO_TIMEOUTS = {"front": 0.10, "back": 0.10, "right": 0.08, "left": 0.08}
TRIG_PULSE_S = 12e-6
MIN_CM, MAX_CM = 2, 200
MISSES_TO_BLANK = 10


class UltrasonicAvoidance:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._distances = {n: None for n in SENSORS}
        self._misses = {n: 0 for n in SENSORS}

        self.threshold_cm = 40.0
        self._thread = None

    def set_params(self, threshold_cm, clear_margin_cm=None, action=None):
        with self._lock:
            self.threshold_cm = float(threshold_cm)

    def clear_override(self):
        pass

    def get_distances(self):
        with self._lock:
            return dict(self._distances)

    # ─── Hardware Interface (Now using RPIGPIO) ───────────────────────────
    def _ping_once(self, trig, echo, to):
        try:
            # Clear stuck pins (Wait up to 50ms for ghost echoes to drop)
            if RPIGPIO.input(echo) == 1:
                end_clear = time.perf_counter() + 0.05
                while RPIGPIO.input(echo) == 1 and time.perf_counter() < end_clear:
                    pass

            # Fire Trigger
            RPIGPIO.output(trig, False)
            time.sleep(5e-6)
            RPIGPIO.output(trig, True)
            time.sleep(TRIG_PULSE_S)
            RPIGPIO.output(trig, False)

            # Wait for Echo HIGH
            t0 = None
            end = time.perf_counter() + to
            while RPIGPIO.input(echo) == 0:
                t0 = time.perf_counter()
                if t0 > end: return None

            # Wait for Echo LOW
            t1 = None
            end = time.perf_counter() + to
            while RPIGPIO.input(echo) == 1:
                t1 = time.perf_counter()
                if t1 > end: return float(MAX_CM)

            if t0 and t1:
                d = (t1 - t0) * SPEED_CM_S / 2.0

                # --- NEW: The Horizon Clamp ---
                # If the sensor sees empty space and times out at ~650cm,
                # just tell the UI the path is clear up to our max range!
                if d > MAX_CM:
                    return float(MAX_CM)
                elif d >= MIN_CM:
                    return d

            return None
        except Exception as e:
            print(f"[ULTRA_HW_ERROR] {e}", file=sys.stderr)
            return None

        finally:
            time.sleep(PING_GAP)

    def _robust_measure(self, name, trig, echo, n=3):
        vals = []
        to = ECHO_TIMEOUTS.get(name, 0.08)
        for _ in range(n):
            d = self._ping_once(trig, echo, to)
            if d is not None: vals.append(d)
        if not vals: return None
        return statistics.median(vals)

    def start(self):
        # Configure RPIGPIO to manage the pins alongside the load cell
        RPIGPIO.setmode(RPIGPIO.BCM)
        RPIGPIO.setwarnings(False)
        for _, cfg in SENSORS.items():
            RPIGPIO.setup(cfg["trig"], RPIGPIO.OUT)
            RPIGPIO.output(cfg["trig"], False)
            RPIGPIO.setup(cfg["echo"], RPIGPIO.IN, pull_up_down=RPIGPIO.PUD_DOWN)

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=1.0)

    def _run(self):
        order = ["front", "back", "left", "right"]
        while not self._stop.is_set():
            try:
                for name in order:
                    cfgu = SENSORS[name]
                    dval = self._robust_measure(name, cfgu["trig"], cfgu["echo"], n=2)
                    with self._lock:
                        if dval is not None:
                            self._misses[name] = 0
                            self._distances[name] = dval
                        else:
                            self._misses[name] += 1
                            if self._misses[name] >= 3:
                                self._distances[name] = None
                time.sleep(0.01)

            except Exception as e:
                print(f"[ULTRA_THREAD_CRASH] Avoided fatal exit: {e}", file=sys.stderr)
                time.sleep(0.1)

    # ─── Vector Repulsion Engine ──────────────────────────────────────────
    def get_deflection_vector(self, target_dist=None, brake_zone=40.0):
        with self._lock:
            dist = dict(self._distances)
            threshold = self.threshold_cm

        def d(n):
            v = dist.get(n)
            return v if v is not None else 999.0

        lc = d("front")
        rc = d("back")
        lf = d("left")
        rf = d("right")

        # ==========================================================
        # THE SMART SHIELD (Dynamic Target Masking)
        # ==========================================================
        # If the front sensors see an object within 80cm of where the
        # UWB tag is located, it is YOU. Erase it from the avoidance math!
        if target_dist is not None:
            if abs(lc - target_dist) < 80.0: lc = 999.0
            if abs(rc - target_dist) < 80.0: rc = 999.0

        def repulse(dist, max_force):
            if dist >= threshold: return 0.0
            if dist <= AVOID_MAX_FORCE_CM: return max_force
            return max_force * (1.0 - ((dist - AVOID_MAX_FORCE_CM) / (threshold - AVOID_MAX_FORCE_CM)))
            
        # ==========================================================
        # 1. FENDER SENSORS (Straight Ahead Orientation)
        # ==========================================================
        # Left fender sees obstacle on Left -> Pushes Right (+)
        push_right_from_lf = repulse(lf, AVOID_FENDER_FORCE)

        # Right fender sees obstacle on Right -> Pushes Left (-)
        push_left_from_rf = repulse(rf, AVOID_FENDER_FORCE)

        # ==========================================================
        # 2. NET TURN CALCULATION (FENDERS ONLY)
        # ==========================================================
        # Only the Fenders are allowed to smoothly steer the rover!
        turn_deflection = push_right_from_lf - push_left_from_rf

        # Grab the raw physical distance in front of the rover
        min_center = min(lc, rc)

        # ==========================================================
        # 3. THE TIE-BREAKER (DEAD-CENTER TRAP)
        # ==========================================================
        if abs(turn_deflection) < 5.0 and min_center < (brake_zone / 2.0):

            if lf > rf:
                turn_deflection = -AVOID_ESCAPE_FORCE  # Spin Left
            else:
                turn_deflection = AVOID_ESCAPE_FORCE  # Spin Right

            return 0.0, turn_deflection, "TRAPPED"

            # ==========================================================
            # THE FIX: Use the dynamic brake_zone for the throttle drop
            # ==========================================================
        if min_center >= brake_zone:
            speed_mult = 1.0
        else:
            speed_mult = max(0.0, (min_center - 10.0) / (brake_zone - 10.0))

        if speed_mult < 0.8:
            boost = AVOID_BOOST_MULTIPLIER
            turn_deflection *= boost

        reason = None
        if min_center < brake_zone:
            reason = f"BRAKING ({int(min_center)}cm)"
        elif abs(turn_deflection) > 1.0:
            reason = "DEFLECTING"

        return speed_mult, turn_deflection, reason

# ======================================================================================
# Sabertooth
# ======================================================================================
class Sabertooth:
    def __init__(self, port, baud_rate=9600):
        try:
            self.ser       = serial.Serial(port, baud_rate, timeout=1)
            self.connected = True
        except serial.SerialException:
            self.connected = False
            self.ser       = None

    def set_motor_speed(self, motor, speed):
        speed = max(-100, min(100, int(speed)))
        if not self.connected:
            return
        if motor == 1:
            value = int(64 + (speed / 100.0) * 63.0)
            value = max(1, min(127, value)) if speed != 0 else 64
        else:
            value = int(192 + (speed / 100.0) * 63.0)
            value = max(129, min(255, value)) if speed != 0 else 192
        try:
            self.ser.write(bytes([value]))
        except:
            pass

    def stop_all(self):
        if self.connected:
            try:
                self.ser.write(bytes([0]))
            except:
                pass

    def close(self):
        self.stop_all()
        if self.connected:
            self.ser.close()


# ======================================================================================
# PID Controller
# ======================================================================================
class PIDController:
    def __init__(self, Kp, Ki, Kd, setpoint, integral_limit=50.0, deadband=0.0):
        # Store the "Empty" base values permanently
        self.base_Kp = Kp
        self.base_Ki = Ki
        self.base_Kd = Kd

        # The active gains that will actually be used
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd

        self.setpoint = setpoint
        self.integral_limit = integral_limit
        self.deadband = deadband  # <--- NEW: Deadband allowance

        self.prev_error = 0
        self.integral = 0
        self._last_time = None

    def schedule_gains(self, current_weight_lbs, max_weight_lbs, full_Kp, full_Ki, full_Kd):
        """Dynamically adjusts PID gains based on weight."""
        safe_weight = max(0.0, min(max_weight_lbs, current_weight_lbs))
        ratio = safe_weight / max_weight_lbs
        self.Kp = self.base_Kp + (ratio * (full_Kp - self.base_Kp))
        self.Ki = self.base_Ki + (ratio * (full_Ki - self.base_Ki))
        self.Kd = self.base_Kd + (ratio * (full_Kd - self.base_Kd))

    def update(self, current_value, is_heading_pid=False):
        now = time.perf_counter()
        dt = (now - self._last_time) if self._last_time is not None else 0.05
        dt = max(0.005, min(dt, 0.5))
        self._last_time = now

        if is_heading_pid:
            error = current_value - self.setpoint
            if error > 180:
                error -= 360
            elif error < -180:
                error += 360
        else:
            error = current_value - self.setpoint

        # ==========================================
        # THE DEADBAND LOGIC
        # ==========================================
        if abs(error) <= self.deadband:
            self.integral = 0.0  # Prevent integral windup while resting!
            self.prev_error = 0.0  # Reset derivative
            return 0.0  # Output zero effort
        # ==========================================

        self.integral += error * dt
        self.integral = max(-self.integral_limit, min(self.integral_limit, self.integral))
        derivative = (error - self.prev_error) / dt

        output = (self.Kp * error) + (self.Ki * self.integral) + (self.Kd * derivative)

        self.prev_error = error
        return output

    def reset(self):
        self.integral = 0
        self.prev_error = 0
        self._last_time = None


# ======================================================================================
# Helpers
# ======================================================================================
def command_direction(left_speed, right_speed, deadband=5.0):
    l = float(left_speed)
    r = float(right_speed)
    if abs(l) < deadband and abs(r) < deadband:
        return "stop"
    if l < -deadband and r > deadband:
        return "left"
    if l > deadband and r < -deadband:
        return "right"
    avg = (l + r) / 2.0
    if avg > deadband:  return "fwd"
    if avg < -deadband: return "rev"
    return "stop"


def safe_addstr(win, y, x, s, attr=0):
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        max_len = w - x - 1
        if max_len <= 0:
            return
        win.addstr(y, x, str(s)[:max_len], attr)
    except curses.error:
        pass



# ======================================================================================
# Anchor Geometry Validator
# ======================================================================================
class AnchorGeometry:
    @staticmethod
    def validate(a1_pos: Tuple[float, float], a2_pos: Tuple[float, float]) -> dict:
        x1, y1 = a1_pos
        x2, y2 = a2_pos

        separation = math.hypot(x2 - x1, y2 - y1)
        warnings = []

        if separation < 0.3:
            warnings.append(f"Anchors too close ({separation:.2f}m < 0.3m min)")
        if abs(y1 - y2) > 0.05:
            warnings.append(f"Anchors not level (Y1={y1:.2f}, Y2={y2:.2f})")
        midpoint_x = (x1 + x2) / 2.0
        if abs(midpoint_x) > 0.05:
            warnings.append(f"Anchors not symmetric around origin (mid={midpoint_x:.2f})")
        if abs(x1 - x2) < 0.1:
            warnings.append("Anchors nearly vertical — poor geometry for X positioning")

        return {
            'separation_m': round(separation, 3),
            'is_symmetric': abs(midpoint_x) < 0.05,
            'is_valid': len(warnings) == 0,
            'warnings': warnings,
        }


# ======================================================================================
# UWB Health Monitor
# ======================================================================================
class UwbHealthMonitor:
    def __init__(self, supabase: Client, robot_id: str):
        self._supa = supabase
        self._robot_id = robot_id
        self._stop = threading.Event()
        self._thread = None
        self.anchor1_connected = False
        self.anchor2_connected = False
        self.anchor1_last_range = 0.0
        self.anchor2_last_range = 0.0
        self.anchor1_age_s = 999.0
        self.anchor2_age_s = 999.0
        self.publish_rate_hz = 0.0

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def update(self, a1_connected, a2_connected, a1_range, a2_range,
               a1_age, a2_age, pub_rate):
        self.anchor1_connected = a1_connected
        self.anchor2_connected = a2_connected
        self.anchor1_last_range = a1_range
        self.anchor2_last_range = a2_range
        self.anchor1_age_s = a1_age
        self.anchor2_age_s = a2_age
        self.publish_rate_hz = pub_rate

    def _run(self):
        while not self._stop.is_set():
            try:
                self._supa.table('uwb_health').upsert({
                    'robot_id': self._robot_id,
                    'anchor1_connected': self.anchor1_connected,
                    'anchor2_connected': self.anchor2_connected,
                    'anchor1_range_m': round(self.anchor1_last_range, 3),
                    'anchor2_range_m': round(self.anchor2_last_range, 3),
                    'anchor1_age_s': round(self.anchor1_age_s, 2),
                    'anchor2_age_s': round(self.anchor2_age_s, 2),
                    'publish_rate_hz': round(self.publish_rate_hz, 1),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                }, on_conflict='robot_id').execute()
            except Exception as e:
                log.warning(f"[HEALTH] Write failed: {e}")
            time.sleep(2.0)



# ======================================================================================
# Port detection — sequential to avoid DTR race condition
# ======================================================================================
def find_serial_ports() -> list[str]:
    candidates = []
    for pattern in ["/dev/ttyUSB*", "/dev/ttyACM*"]:
        candidates.extend(sorted(glob.glob(pattern)))
    if not candidates:
        sys.exit("No serial ports found — check USB connections.")
    return candidates


def is_anchor_port(port: str, timeout_s: float = DETECT_TIMEOUT_S) -> bool:
    try:
        with serial.Serial(port, BAUD, timeout=1.0) as ser:
            ser.dtr = False
            time.sleep(0.3)   # longer settle before reset
            ser.dtr = True
            ser.reset_input_buffer()
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                raw = ser.readline()
                if not raw:
                    continue
                try:
                    text = raw.decode("utf-8", errors="replace").strip()
                    if not text.startswith("{"):
                        continue
                    frame = json.loads(text)
                    if frame.get("event") == "boot" or "range" in frame:
                        log.info(f"  {port} → anchor {frame.get('anchor', '?')}")
                        return True
                except (json.JSONDecodeError, ValueError):
                    continue
    except serial.SerialException as e:
        log.warning(f"  {port} → error: {e}")
    return False


def detect_anchor_ports() -> list[str]:
    """Probe ports sequentially to avoid DTR reset race condition."""
    ports = find_serial_ports()
    log.info(f"Scanning ports: {ports}")
    found = []
    for port in ports:
        log.info(f"  Probing {port}...")
        if is_anchor_port(port):
            found.append(port)
        else:
            log.warning(f"  {port} → no anchor detected")
        # Small gap between probes so ESP32 boot sequences don't overlap
        time.sleep(0.5)
    return found


# ======================================================================================
# Median filter
# ======================================================================================
class MedianFilter:
    def __init__(self, window: int = 5):
        self._buf = []
        self._window = window

    def update(self, v: float) -> float:
        self._buf.append(v)
        if len(self._buf) > self._window:
            self._buf.pop(0)
        s = sorted(self._buf)
        n = len(s)
        mid = n // 2
        return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0

    def ready(self) -> bool:
        return len(self._buf) >= max(1, self._window // 2)


# ======================================================================================
# Anchor reader
# ======================================================================================
RANGE_MIN_M = 0.05
RANGE_MAX_M = 20.0


@dataclass
class AnchorReader:
    anchor_num: int
    port: str
    median_window: int = 7

    range_m: Optional[float] = field(default=None, init=False)
    ts: float = field(default=0.0, init=False)
    connected: bool = field(default=False, init=False)

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _filter: MedianFilter = field(init=False)

    def __post_init__(self):
        self._filter = MedianFilter(self.median_window)

    def start(self):
        threading.Thread(
            target=self._run, daemon=True,
            name=f"anchor{self.anchor_num}"
        ).start()

    def stop(self):
        self._stop.set()

    def get(self) -> Tuple[Optional[float], float, bool]:
        with self._lock:
            return self.range_m, self.ts, self.connected

    def _run(self):
        log.info(f"[Anchor {self.anchor_num}] Reading from {self.port}")
        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, BAUD, timeout=1.0) as ser:
                    self.connected = True
                    while not self._stop.is_set():
                        raw = ser.readline()
                        if raw:
                            self._parse(raw)

                        elif self.ts > 0 and time.monotonic() - self.ts > 2.0:
                            with self._lock:
                                self.connected = False
            except serial.SerialException as e:
                with self._lock:
                    self.connected = False
                log.warning(f"[Anchor {self.anchor_num}] {e} — retrying in 2s")
                time.sleep(2.0)

    def _parse(self, raw: bytes):
        try:
            text = raw.decode("utf-8", errors="replace").strip()
            if not text.startswith("{"):
                return

            frame = json.loads(text)

            if frame.get("event"):
                log.info(f"[Anchor {self.anchor_num}] Event: {frame}")
                return

            rng = float(frame["range"])
            if rng < RANGE_MIN_M or rng > RANGE_MAX_M:
                log.debug(f"[Anchor {self.anchor_num}] Out of bounds ({rng:.2f} m) — discarded")
                return

            height_diff = abs(ANCHOR_HEIGHT_M - TAG_HEIGHT_M)
            if height_diff > 0.0:
                horiz_sq = rng**2 - height_diff**2
                if horiz_sq <= 0.0:
                    rng = 0.01  # Clamp to 1cm instead of discarding the packet
                else:
                    rng = math.sqrt(horiz_sq)

            smoothed = self._filter.update(rng)
            if not self._filter.ready():
                return

            with self._lock:
                self.range_m = smoothed
                self.ts = time.monotonic()
                self.connected = True
            
            log.debug(
                f"[Anchor {self.anchor_num}] accepted ts={self.ts:.3f} "
                f"raw={rng:.4f} filtered={smoothed:.4f}"
            )

            log.debug(f"[Anchor {self.anchor_num}] raw={rng:.4f} m  filtered={smoothed:.4f} m")

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.debug(f"[Anchor {self.anchor_num}] Parse error: {e}")


# ======================================================================================
# Position solver — law of cosines
# ======================================================================================
def solve_position(b, a, c) -> Optional[Tuple[float, float]]:
    if c < 1e-9 or b < 1e-9:
        return None
    cos_a = (b**2 + c**2 - a**2) / (2.0 * b * c)
    cos_a = max(-1.0, min(1.0, cos_a))
    sin_a = math.sqrt(1.0 - cos_a**2)
    x = b * cos_a - c / 2.0
    y = b * sin_a
    return x, y


class CsvSimpleLogger:
    def __init__(self, path: str, fieldnames: list[str]):
        self.path = path
        self.fieldnames = fieldnames
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Open the file ONCE and keep it open
        self.file = open(path, "w", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=self.fieldnames)
        self.writer.writeheader()
        self.file.flush()

    def write_row(self, row: dict):
        with self._lock:
            self.writer.writerow(row)
            self.file.flush()  # Force it to save without closing



class CsvUwbEventLogger:
    def __init__(self, path: str):
        self.path = path
        self.fieldnames = [
            "ts_iso", "session_id", "test_name",
            "event_type", "detail",
            "anchor1_connected", "anchor2_connected",
            "publish_rate_hz", "geometry_valid",
        ]
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=self.fieldnames).writeheader()

    def log(self, event_type, detail="",
            anchor1_connected=True, anchor2_connected=True,
            publish_rate_hz=0.0, geometry_valid=True):
        row = {
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "session_id": CURRENT_SESSION_ID,
            "test_name": CURRENT_TEST_NAME,
            "event_type": event_type,
            "detail": detail,
            "anchor1_connected": anchor1_connected,
            "anchor2_connected": anchor2_connected,
            "publish_rate_hz": round(publish_rate_hz, 2),
            "geometry_valid": geometry_valid,
        }
        with self._lock:
            with open(self.path, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=self.fieldnames).writerow(row)





# ======================================================================================
# Main curses app 
# ======================================================================================
def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(50)

    SUPABASE_URL = "https://jbhjvtfeezmityewhtwa.supabase.co"
    SUPABASE_KEY = "sb_publishable_z7vaIDJQaQcjB0bpKG7Rzg_0m6boJHf"
    ROBOT_ID     = "rover_01"
    SERIAL_PORT  = "/dev/ttyAMA0"

    saber = Sabertooth(SERIAL_PORT)

    # Pairing  [old2.py]
    supa_raw        = create_client(SUPABASE_URL, SUPABASE_KEY)
    pairing_manager = PairingManager(supa_raw, ROBOT_ID)
    pairing_manager.start()



    # Supabase
    try:
        cmd_listener = CommandListener(SUPABASE_URL, SUPABASE_KEY, ROBOT_ID,
                                       pairing_manager=pairing_manager)
        db_writer    = SupabaseWriter(SUPABASE_URL, SUPABASE_KEY, ROBOT_ID)
        cmd_listener.start()
        supabase_enabled = True
    except Exception as e:
        print(f"[SUPABASE INIT FAILED] {e}", file=sys.stderr)
        supabase_enabled = False
        cmd_listener     = None
        db_writer        = None

    # UWB — [movementtest.py] LocalUwbTracker with is_pose_good() gating
    uwb_listener = None
    uwb_enabled = False
    try:
        uwb_listener = LocalUwbTracker(
            SUPABASE_URL, SUPABASE_KEY, ROBOT_ID,
            a1x=-0.325, a2x=0.325,
            stale_s=1.5,
            min_conf=30.0,
            publish_to_supabase=True,
        )
        uwb_listener.start()
        uwb_enabled = True
    except Exception as e:
        print(f"[UWB] local tracker start failed: {e}", file=sys.stderr)

    # Heartbeat  [old2.py]
    try:
        heartbeat         = HeartbeatMonitor(supa_raw, ROBOT_ID)
        heartbeat.start()
        heartbeat_enabled = True
    except Exception as e:
        heartbeat_enabled = False
        heartbeat         = None
        print(f"[HEARTBEAT] Failed to start: {e}", file=sys.stderr)

    # Ultrasonic
    avoid     = UltrasonicAvoidance()
    hit_count = {"front": 0, "back": 0, "left": 0, "right": 0}
    HIT_CONFIRM = 2  # faster confirmation for a moving rover
    try:
        avoid.start()
        ultrasonic_enabled_local = True
    except Exception as e:
        ultrasonic_enabled_local = False
        print(f"[ULTRA] start() failed: {e}", file=sys.stderr)

    # Load cell  [from read_weight.py — HX711 DT=GPIO5, SCK=GPIO6]
    load_cell = LoadCellReader(
        threshold_kg=LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT,
        weight_alarm_override=False,
    )
    load_cell.start()   # tares on startup; gracefully no-ops if HX711 not available

    buzzer = PiezoAlarm(BUZZER_GPIO_PIN, BUZZER_TONE_HZ)
    buzzer.start()

    csv_logger     = CsvTelemetryLogger(ROVER_CSV_PATH)
    event_logger   = CsvEventLogger(ROVER_EVENT_PATH)
    latency_logger = CsvLatencyLogger(ROVER_LATENCY_PATH)

    # Log session start event
    event_logger.log("SESSION_START",
                     detail=f"test={CURRENT_TEST_NAME} session={CURRENT_SESSION_ID}")

    ## PID  — [movementtest.py] reduced speed caps for safer UWB follow
    #DIST_KP, DIST_KI, DIST_KD = 0.0, 0.0, 0.0
    DIST_KP, DIST_KI, DIST_KD = 0.10, 0.10, 0.025
    HEAD_KP, HEAD_KI, HEAD_KD = 0.07, 0.075, 0.025
    SPEED_CAP      = 100.0
    TURN_CAP       = 100.0
    MAX_FOLLOW_DIST = 600  # 20 ft for testing

    pid_dist = PIDController(
        DIST_KP, DIST_KI, DIST_KD,
        DEFAULT_FOLLOW_DISTANCE_CM,
        integral_limit=100.0,
        deadband=5.0  # <--- NEW: 5cm allowance on distance
    )

    pid_head = PIDController(
        HEAD_KP, HEAD_KI, HEAD_KD,
        0.0,
        integral_limit=100.0,
        deadband=5.0  # <--- NEW: 5 degree allowance on heading
    )

    # Sim fallback state
    sim_distance = DEFAULT_FOLLOW_DISTANCE_CM
    sim_heading   = 0.0
    # luggage_fallen and weight_kg come from load_cell.get() each loop.
    # sim_weight_kg is only used as display fallback when HX711 is unavailable.
    sim_weight_kg = 0.0

    last_nav_write      = 0.0
    last_telemetry_write = 0.0
    _last_alert_time    = 0.0
    _last_obstacle_active = False
    _prev_paired        = False          # for PAIR_CONFIRMED event
    _last_buffer_size   = 0             # for TELEM_BUFFER_FLUSHED event
    _prev_fallen        = False          # for WEIGHT_ALARM_TRIGGERED / CLEARED events
    _prev_a1_conn       = None           # for ANCHOR1_DISCONNECTED edge trigger
    _prev_a2_conn       = None           # for ANCHOR2_DISCONNECTED edge trigger
    _cmd_rejected_last  = False          # debounce CMD_REJECTED_UNPAIRED

    # State machine  [old2.py]
    current_state                  = RoverState.WAITING_FOR_PAIR
    _prev_state                    = RoverState.WAITING_FOR_PAIR   # for event logging
    uwb_lost_confirmation_required = False

    MIN_H = 38   # taller now — load cell adds 2 rows
    MIN_W = 80

    effective_distance = 0.0  # initialised here; updated each loop from UWB / sim

    pid_mute_timer = 0.0
    turn_latch_timer = 0.0
    latched_turn = 0.0
    latched_speed = 0.0
    escape_macro_timer = 0.0
    latched_escape_turn = 0.0

    try:
        while True:
            loop_start = time.perf_counter()
            current_time = time.time()

            # THE FIX: Initialize these here so the UI always has a value to print!
            dynamic_bubble_cm = AVOID_BUBBLE_MAX_CM
            dynamic_brake_cm = AVOID_BRAKE_MAX_CM
            
            # --- keyboard ---
            key = stdscr.getch()
            if key == ord('q'):
                break
            elif key == ord('l'):
                print("[LOADCELL] Manual tare requested via [L]", file=sys.stderr)
                load_cell.request_tare()
            elif key == ord('w'):
                sim_distance += 1.0
            elif key == ord('s'):
                sim_distance -= 1.0
            elif key == ord('a'):
                sim_heading -= 1.0
            elif key == ord('d'):
                sim_heading += 1.0

            # --- [movementtest.py] UWB pose selection with is_pose_good() ---
            effective_distance = sim_distance
            effective_heading  = sim_heading
            uwb_status         = "UWB: OFF"
            uwb_live           = False
            uwb_lost           = False
            uwb_confidence     = {}
            age_s              = None

            if uwb_enabled and uwb_listener:
                pose = uwb_listener.get()
                if uwb_listener.is_pose_good(pose):
                    effective_distance = pose["distance_cm"]
                    effective_heading  = pose["angle_deg"]
                    age_s   = pose.get("age_s")
                    conf    = pose.get("confidence")
                    uwb_live = True
                    uwb_status = f"LIVE  age={age_s:.2f}s  conf={conf}"
                    # Build confidence dict for display (reuse UwbConfidence scorer)
                    uwb_confidence = UwbConfidence.calculate(
                        age_s or 0.0,
                        pose.get("a1_age_s") or 0.0,
                        pose.get("a2_age_s") or 0.0,
                    )
                else:
                    age_s = pose.get("age_s")
                    conf  = pose.get("confidence")
                    uwb_lost   = (age_s is not None and age_s > uwb_listener.stale_s)
                    uwb_status = (
                        f"STALE age={age_s}  conf={conf}  "
                        f"err={pose.get('last_error')}"
                        if age_s is not None
                        else "UWB: NO DATA"
                    )

            # --- Pull load-cell snapshot FIRST so luggage_fallen is defined
            #     before the state machine and the AUTO-resume guard use it. ---
            # --- Pull Sensor Data (Load Cell + Ultrasonics) ---
            lc = load_cell.get()
            luggage_fallen = lc["luggage_fallen"]

            # Use our unified, fast-median array for everything.
            ultra = avoid.get_distances() if ultrasonic_enabled_local else {}


            if not lc["hw_available"]:
                live_weight_kg = 0.0
                luggage_fallen = True
            else:
                live_weight_kg = lc["weight_kg"]
                luggage_fallen = lc["luggage_fallen"]

            buzzer.update(
                weight_kg=live_weight_kg,
                threshold_kg=lc["threshold_kg"],
                hw_available=lc["hw_available"],
                override=lc["override"],
                state_name=current_state.name,

                # THE FIX: Feed the raw UWB age into the buzzer!
                uwb_age_s=age_s
            )

            print(
                f"[LC_STATE] live_weight_kg={live_weight_kg:.3f} "
                f"lc_threshold_kg={lc['threshold_kg']:.3f} "
                f"lc_override={lc['override']}",
                file=sys.stderr,
            )

            # --- state machine transitions  [old2.py] ---
            # Recalculate separation_stop each loop now that effective_distance is current.
            separation_stop = (
                effective_distance > MAX_FOLLOW_DIST
                and current_state not in (RoverState.MANUAL, RoverState.MANUAL_OVERRIDE)
            )

            if not pairing_manager.is_paired():
                current_state = RoverState.WAITING_FOR_PAIR
            elif current_state == RoverState.WAITING_FOR_PAIR and pairing_manager.is_paired():
                # Just became paired — allow auto to start
                current_state = RoverState.AUTO

            elif uwb_lost:
                allow_no_uwb = False
                if supabase_enabled and cmd_listener:
                    allow_no_uwb = (
                        bool(cmd_listener.uwb_override_enabled)
                        or (cmd_listener.mode == 'manual')
                    )
                if allow_no_uwb:
                    uwb_lost_confirmation_required = False
                else:
                    if not uwb_lost_confirmation_required:
                        current_state                  = RoverState.LOST_UWB
                        uwb_lost_confirmation_required = True

            elif uwb_live and current_state == RoverState.LOST_UWB:
                uwb_lost_confirmation_required = False
                current_state = RoverState.AUTO

            elif uwb_lost_confirmation_required and key == ord(' '):
                uwb_lost_confirmation_required = False
                current_state = RoverState.AUTO

            # --- process app commands  [old2.py] ---
            control_mode          = "LOCAL"
            obstacle_avoid_enabled = False
            obstacle_reason       = None

            if supabase_enabled and cmd_listener:
                if cmd_listener.smart_recovery:
                    pid_dist.reset()
                    pid_head.reset()
                    avoid.clear_override()
                    uwb_lost_confirmation_required = False
                    cmd_listener.smart_recovery = False

                if cmd_listener.reset_luggage:
                    print("[LOADCELL] reset_luggage requested", file=sys.stderr)
                    load_cell.request_tare()
                    pid_dist.reset()
                    pid_head.reset()
                    cmd_listener.reset_luggage = False
                    try:
                        cmd_listener.supabase.table('rover_commands').update({
                            'reset_luggage': False
                        }).eq('robot_id', ROBOT_ID).execute()
                    except Exception as e:
                        print(f"[LOADCELL] failed to clear reset_luggage in DB: {e}", file=sys.stderr)

                if cmd_listener.recalibrate_luggage:
                    # Full recalibration: delete saved cal file and re-run load_or_calibrate.
                    # This is the same sequence that runs on first boot.
                    print("[LOADCELL] Full recalibration requested from app.", file=sys.stderr)
                    cmd_listener.recalibrate_luggage = False
                    if load_cell.hw_available and load_cell._hx:
                        def _do_full_recal():
                            try:
                                # Delete existing calibration so load_or_calibrate runs fresh
                                if os.path.exists(LOAD_CELL_CALIBRATION_FILE):
                                    os.remove(LOAD_CELL_CALIBRATION_FILE)
                                    print("[LOADCELL] Deleted old calibration file.", file=sys.stderr)
                                # Re-run full calibration (interactive — waits for known weight)
                                new_ref = load_or_calibrate(load_cell._hx)
                                load_cell._hx.set_reference_unit(new_ref)
                                # Re-tare with empty tray
                                print("[LOADCELL] Re-taring after recalibration…", file=sys.stderr)
                                load_cell._hx.tare(times=20)
                                with load_cell._lock:
                                    load_cell.tare_done = True
                                print("[LOADCELL] Full recalibration complete.", file=sys.stderr)
                            except Exception as _e:
                                print(f"[LOADCELL] Recalibration failed: {_e}", file=sys.stderr)
                        threading.Thread(target=_do_full_recal, daemon=True).start()
                    else:
                        print("[LOADCELL] HW not available — recalibration skipped.", file=sys.stderr)

                # Update PID setpoints and print whenever target_distance changes
                # so you can confirm the rover received the new follow distance.
                _new_dist_setpoint = float(
                    cmd_listener.target_distance or DEFAULT_FOLLOW_DISTANCE_CM
                )
                if abs(_new_dist_setpoint - pid_dist.setpoint) > 0.5:
                    print(
                        f"[PID] target_distance changed: "
                        f"{pid_dist.setpoint:.1f} cm → {_new_dist_setpoint:.1f} cm",
                        file=sys.stderr,
                    )
                pid_dist.setpoint = _new_dist_setpoint
                pid_head.setpoint = float(cmd_listener.target_heading or 0.0)

                # Propagate weight threshold + override into load cell thread
                load_cell.set_threshold(cmd_listener.luggage_weight_threshold_kg)
                load_cell.set_override(cmd_listener.weight_alarm_override)

                print(
                    f"[LOADCELL_CMD] applying threshold={cmd_listener.luggage_weight_threshold_kg:.3f}kg "
                    f"override={cmd_listener.weight_alarm_override}",
                    file=sys.stderr,
                )

                if cmd_listener.clear_obstacle_override:
                    avoid.clear_override()
                    cmd_listener.clear_obstacle_override = False
                    avoid.set_params(AVOID_BUBBLE_MAX_CM)

                obstacle_avoid_enabled = (
                        bool(cmd_listener.obstacle_avoid_enabled) and ultrasonic_enabled_local
                )

                if cmd_listener.emergency_stop or cmd_listener.mode == 'stop':
                    current_state = RoverState.EMERGENCY_STOP
                elif cmd_listener.mode == 'manual':
                    uwb_lost_confirmation_required = False
                    if cmd_listener.manual_override_mode:
                        current_state = RoverState.MANUAL_OVERRIDE
                    else:
                        current_state = RoverState.MANUAL
                elif cmd_listener.mode == 'auto':
                    # Once pairing is valid, AUTO should be allowed to leave WAITING_FOR_PAIR.
                    if current_state == RoverState.WAITING_FOR_PAIR and pairing_manager.is_paired():
                        if not uwb_lost_confirmation_required and not separation_stop and not luggage_fallen:
                            current_state = RoverState.AUTO

                    elif current_state == RoverState.LOST_UWB and not uwb_lost_confirmation_required:
                        current_state = RoverState.AUTO

                    elif current_state == RoverState.EMERGENCY_STOP and cmd_listener.emergency_stop:
                        pass

                    elif not separation_stop and not luggage_fallen:
                        current_state = RoverState.AUTO

            # (lc, luggage_fallen, live_weight_kg already fetched above the state machine)

            left_speed         = 0.0
            right_speed        = 0.0
            safety_active      = False
            safety_msg         = ""
            obstacle_avoid_active = False
            obstacle_hold_active  = False

            def classify_override(reason, l_cmd, r_cmd):
                reason_u = (reason or "").upper()
                if abs(l_cmd) < 1e-6 and abs(r_cmd) < 1e-6:
                    return True, False
                if "HARD STOP" in reason_u or reason_u.startswith("STOP"):
                    return True, False
                if "TURN" in reason_u or "REVERSE" in reason_u or "AVOID" in reason_u:
                    return False, True
                return False, False

            if current_state == RoverState.WAITING_FOR_PAIR:
                left_speed = right_speed = 0
                safety_active = True
                safety_msg    = "WAITING FOR APP PAIRING"

            elif current_state == RoverState.LOST_UWB:
                left_speed = right_speed = 0
                safety_active = True
                safety_msg    = "UWB LOST — PRESS SPACE TO CONFIRM RECOVERY"

            elif current_state == RoverState.EMERGENCY_STOP:
                left_speed = right_speed = 0
                safety_active = True
                safety_msg    = "EMERGENCY STOP"

            elif luggage_fallen and current_state not in (RoverState.MANUAL, RoverState.MANUAL_OVERRIDE):
                left_speed = right_speed = 0
                safety_active = True
                safety_msg    = "STOPPED: LUGGAGE FELL OFF!"
                current_state = RoverState.HARD_STOP


            elif current_state == RoverState.MANUAL_OVERRIDE:
                left_speed    = cmd_listener.manual_left_speed
                right_speed   = cmd_listener.manual_right_speed
                control_mode  = "MANUAL OVERRIDE"
                # Hard-stop only (no avoidance maneuvers)
                if ultrasonic_enabled_local:
                    fast = avoid.get_fast_distances()
                    for sensor_name, val in fast.items():
                        if val is not None and val <= 10.0:
                            left_speed = right_speed = 0
                            safety_active = True
                            safety_msg    = f"HARD STOP: {sensor_name.upper()} {int(val)}cm"
                            break

            elif current_state == RoverState.MANUAL:
                left_speed   = cmd_listener.manual_left_speed
                right_speed  = cmd_listener.manual_right_speed
                control_mode = "MANUAL"

            elif current_state == RoverState.AUTO:
                # The rover should stop ONLY if the user has gone more than 6 ft away.
                # target_distance is the follow setpoint, not the stop threshold.

                arrival_threshold_cm = 15.0 

                if effective_distance > MAX_FOLLOW_DIST:
                    left_speed = right_speed = 0.0
                    safety_active = True
                    dist_ft = effective_distance / 30.48
                    safety_msg = (
                        f"STOPPED: user > 6 ft ({effective_distance:.0f}cm / {dist_ft:.1f}ft)"
                        f" [{uwb_status}]"
                    )
                    current_state = RoverState.HARD_STOP

                elif uwb_enabled and uwb_listener and not uwb_live:
                    left_speed = right_speed = 0.0
                    safety_active = True
                    safety_msg = "STOPPED: UWB LOST/LOW CONF"
                    current_state = RoverState.LOST_UWB

                else:
                    ALIGN_START_DEG = 20.0
                    PIVOT_DEG = 45.0

                    angle_error = abs(effective_heading)

                    if angle_error <= ALIGN_START_DEG:
                        forward_scale = 1.0
                    elif angle_error >= PIVOT_DEG:
                        forward_scale = 0.0
                    else:
                        forward_scale = 1.0 - (
                            (angle_error - ALIGN_START_DEG) /
                            (PIVOT_DEG - ALIGN_START_DEG)
                        )

                    # ==============================================================
                    # UPDATED PID & DEAD-BAND LOGIC
                    # ==============================================================
                    # 1. Convert live DB weight to lbs for easier tuning math
                    live_weight_lbs = kg_to_lb(live_weight_kg)

                    # 2. Schedule Distance Gains
                    # (Current weight, Max Weight, Full_Kp, Full_Ki, Full_Kd)
                    # NOTE: Tune these "Full" values experimentally!
                    pid_dist.schedule_gains(live_weight_lbs, 20.0,
                                            full_Kp=0.15, full_Ki=0.12, full_Kd=0.025)

                    # 3. Schedule Heading Gains (Heavier rover = harder to pivot)
                    pid_head.schedule_gains(live_weight_lbs, 20.0,
                                            full_Kp=0.1, full_Ki=0.1, full_Kd=0.025)

                    # Now perform the normal updates with the newly scheduled gains
                    # ==============================================================
                    # 1. UWB PID Base Commands
                    # ==============================================================
                    base_speed_raw = pid_dist.update(effective_distance)

                    # Enable Reversing: Clamp lower bound to -SPEED_CAP
                    base_speed = max(-SPEED_CAP, min(SPEED_CAP, base_speed_raw))

                    # Forward & Reverse Slop Compensation
                    if base_speed > 1:
                        base_speed += 3.0
                    elif base_speed < -1:
                        base_speed -= 3.0

                        # Apply alignment scaling
                    if abs(base_speed) > 0:
                        base_speed *= forward_scale


                    AVOID_FULL_STRETCH_POWER = 35.0
                    speed_ratio = min(1.0, abs(base_speed) / AVOID_FULL_STRETCH_POWER)

                    # Dynamically stretch the bubble and brake zone based on that speed!
                    dynamic_bubble_cm = AVOID_BUBBLE_MIN_CM + (speed_ratio * (AVOID_BUBBLE_MAX_CM - AVOID_BUBBLE_MIN_CM))
                    dynamic_brake_cm = AVOID_BRAKE_MIN_CM + (speed_ratio * (AVOID_BRAKE_MAX_CM - AVOID_BRAKE_MIN_CM))

                    turn_adjust = pid_head.update(effective_heading, is_heading_pid=True)

                    # Turning Slop Compensation
                    if abs(turn_adjust) > 0.5:
                        turn_sign = 1.0 if turn_adjust > 0 else -1.0
                        dynamic_turn_slop = 23.0 if base_speed < 1.0 else 18.0
                        turn_adjust += turn_sign * dynamic_turn_slop
                    # ==============================================================
                    # 2. Ultrasonic Vector Repulsion (The Fluid Avoidance)
                    # ==============================================================
                    if (not safety_active) and obstacle_avoid_enabled and ultrasonic_enabled_local:

                        # ----------------------------------------------------------
                        # THE MACRO INTERCEPTOR: Takes total control of the rover
                        # ----------------------------------------------------------
                        if current_time < escape_macro_timer:
                            time_left = escape_macro_timer - current_time
                            obstacle_avoid_active = True

                            # PHASE 1: Reverse for 0.4 seconds (1.2s to 0.8s)
                            if time_left > 0.8:
                                base_speed = -25.0
                                turn_adjust = 0.0
                                safety_msg = "MACRO: REVERSING"

                            # PHASE 2: Hard Pivot in place for 0.4 seconds (0.8s to 0.4s)
                            elif time_left > 0.4:
                                base_speed = 0.0
                                turn_adjust = latched_escape_turn
                                safety_msg = "MACRO: PIVOTING"

                            # PHASE 3: Coast forward away from the box for 0.4 seconds
                            else:
                                base_speed = 25.0
                                turn_adjust = 0.0
                                safety_msg = "MACRO: COASTING"

                        # ----------------------------------------------------------
                        # NORMAL FLUID AVOIDANCE
                        # ----------------------------------------------------------
                        else:
                            avoid.set_params(dynamic_bubble_cm)
                            speed_mult, avoid_turn, obs_reason = avoid.get_deflection_vector(target_dist=effective_distance, brake_zone=dynamic_brake_cm)

                            if obs_reason == "TRAPPED":
                                # FIRE THE MACRO! Set the timer for 1.2 seconds in the future
                                escape_macro_timer = current_time + 0.75
                                latched_escape_turn = avoid_turn
                                obstacle_avoid_active = True
                                safety_msg = "TRAP TRIGGERED!"

                            elif obs_reason:
                                obstacle_reason = obs_reason
                                obstacle_avoid_active = True
                                safety_msg = obs_reason

                                base_speed *= speed_mult
                                turn_adjust = avoid_turn

                                if abs(avoid_turn) > 15.0:
                                    latched_turn = avoid_turn
                                    latched_speed = base_speed
                                    turn_latch_timer = current_time + 0.00
                                    pid_mute_timer = current_time + 0.75
                            else:
                                obstacle_avoid_active = False
                                obstacle_reason = None

                                if current_time < turn_latch_timer:
                                    turn_adjust = latched_turn
                                    base_speed = latched_speed
                                elif current_time < pid_mute_timer:
                                    turn_adjust *= 0.0

                    # ==============================================================
                    # 3. Final Clamps and Motor Mixing
                    # ==============================================================
                    # Clamp turn speed
                    turn_adjust = max(-TURN_CAP, min(TURN_CAP, turn_adjust))

                    # Prevent the inside wheel from reversing during a banking turn
                    #if base_speed > 5.0:
                       # turn_adjust = max(-abs(base_speed), min(abs(base_speed), turn_adjust))

                    # Mix Channels
                    left_speed = max(-100, min(100, base_speed - turn_adjust))
                    right_speed = max(-100, min(100, base_speed + turn_adjust))
                    control_mode = "AUTO"

                    # Stop cleanly when already at follow target
                    if abs(effective_distance - pid_dist.setpoint) <= arrival_threshold_cm and angle_error <= ALIGN_START_DEG:
                        left_speed = right_speed = 0.0
                        safety_msg = "AT FOLLOW TARGET"
                        
            # --- motors ---
            motor_cmd_ts = datetime.now(timezone.utc)

            print(
                f"[MOTOR_CMD] "
                f"state={current_state.name} "
                f"mode={cmd_listener.mode if cmd_listener else 'unknown'} "
                f"L={left_speed:.2f} "
                f"R={right_speed:.2f} "
                f"safety_active={safety_active} "
                f"safety_msg={safety_msg}",
                file=sys.stderr,
            )

            saber.set_motor_speed(1, left_speed)
            saber.set_motor_speed(2, right_speed)

            # ── Event logging (fires immediately on state transitions / safety events) ──
            if current_state != _prev_state:
                event_logger.log(
                    event_type=f"STATE_{current_state.name}",
                    detail=safety_msg or "",
                    state_before=_prev_state.name,
                    state_after=current_state.name,
                    distance_m=effective_distance / 100.0,
                    uwb_live=uwb_live,
                    weight_kg=live_weight_kg,
                    luggage_fallen=luggage_fallen,
                )
                _prev_state = current_state

            # Pairing confirmed
            now_paired = pairing_manager.is_paired()
            if now_paired and not _prev_paired:
                event_logger.log("PAIR_CONFIRMED",
                                 detail=f"token={pairing_manager.get_token() or '?'}",
                                 state_before=_prev_state.name,
                                 state_after=current_state.name)
            _prev_paired = now_paired

            # Telemetry buffer flush (Wi-Fi restored)
            cur_buf = db_writer.telem_buffer.size() if (supabase_enabled and db_writer) else 0
            if _last_buffer_size > 0 and cur_buf == 0:
                event_logger.log("TELEM_BUFFER_FLUSHED",
                                 detail=f"flushed {_last_buffer_size} buffered entries",
                                 state_after=current_state.name,
                                 uwb_live=uwb_live)
            elif cur_buf > 0 and _last_buffer_size == 0:
                event_logger.log("TELEM_BUFFER_STARTED",
                                 detail="Wi-Fi/Supabase unavailable — buffering locally",
                                 state_after=current_state.name,
                                 uwb_live=uwb_live)
            _last_buffer_size = cur_buf

            # Luggage fallen / alarm events
            if luggage_fallen and not _prev_fallen:
                event_logger.log("WEIGHT_ALARM_TRIGGERED",
                                 detail=f"weight={live_weight_kg:.3f}kg thr={lc['threshold_kg']:.3f}kg",
                                 state_after=current_state.name,
                                 weight_kg=live_weight_kg, luggage_fallen=True)
            if not luggage_fallen and _prev_fallen:
                event_logger.log("WEIGHT_ALARM_CLEARED",
                                 detail=f"weight={live_weight_kg:.3f}kg override={lc['override']}",
                                 state_after=current_state.name,
                                 weight_kg=live_weight_kg)
            _prev_fallen = luggage_fallen

            # Rejected command while unpaired (debounced — logs once per entry into WAITING_FOR_PAIR)
            if current_state == RoverState.WAITING_FOR_PAIR and supabase_enabled and cmd_listener:
                cmd_active = (abs(cmd_listener.manual_left_speed) > 1.0 or
                              abs(cmd_listener.manual_right_speed) > 1.0 or
                              cmd_listener.mode in ('auto', 'manual'))
                if cmd_active and not _cmd_rejected_last:
                    event_logger.log("CMD_REJECTED_UNPAIRED",
                                     detail=f"mode={cmd_listener.mode} "
                                            f"L={cmd_listener.manual_left_speed:.1f} "
                                            f"R={cmd_listener.manual_right_speed:.1f}",
                                     state_after=current_state.name)
                _cmd_rejected_last = cmd_active
            else:
                _cmd_rejected_last = False

            pose_now = uwb_listener.get() if (uwb_enabled and uwb_listener) else {}
            # Anchor connect/disconnect events (edge-triggered — only fires on change)
            if uwb_enabled and uwb_listener:
                a1ok = pose_now.get("a1_connected")
                a2ok = pose_now.get("a2_connected")
                if a1ok is not None and a1ok != _prev_a1_conn:
                    event_logger.log(
                        "ANCHOR1_CONNECTED" if a1ok else "ANCHOR1_DISCONNECTED",
                        detail=f"anchor1_connected changed to {a1ok}",
                        state_after=current_state.name, uwb_live=uwb_live,
                        distance_m=effective_distance / 100.0,
                    )
                    _prev_a1_conn = a1ok
                if a2ok is not None and a2ok != _prev_a2_conn:
                    event_logger.log(
                        "ANCHOR2_CONNECTED" if a2ok else "ANCHOR2_DISCONNECTED",
                        detail=f"anchor2_connected changed to {a2ok}",
                        state_after=current_state.name, uwb_live=uwb_live,
                        distance_m=effective_distance / 100.0,
                    )
                    _prev_a2_conn = a2ok

            # ── End-to-End Latency log (every AUTO loop with live UWB) ──────────────
            if current_state == RoverState.AUTO and uwb_live and uwb_enabled and uwb_listener:
                pose_ts = pose_now.get("updated_at")  # UTC datetime of last UWB row
                if pose_ts is not None:
                    latency_ms = (motor_cmd_ts - pose_ts).total_seconds() * 1000.0
                    latency_logger.write_row({
                        "ts_iso":             motor_cmd_ts.isoformat(),
                        "session_id":         CURRENT_SESSION_ID,
                        "test_name":          CURRENT_TEST_NAME,
                        "uwb_updated_at_iso": pose_ts.isoformat(),
                        "motor_cmd_ts_iso":   motor_cmd_ts.isoformat(),
                        "latency_ms":         round(latency_ms, 2),
                        "distance_m":         round(effective_distance / 100.0, 3),
                        "heading_deg":        round(effective_heading, 2),
                        "left_speed_cmd":     round(left_speed, 2),
                        "right_speed_cmd":    round(right_speed, 2),
                    })

            # --- supabase writes ---
            if supabase_enabled and db_writer:

                # Fast Loop (10 Hz): Live Joystick/Nav State
                if current_time - last_nav_write >= 0.10:
                    db_writer.write_nav_state(left_speed, right_speed)
                    last_nav_write = current_time

                # Slow Loop (3 Hz): Heavy Telemetry & Ultrasonic DB Writes
                # THIS STOPS THE QUEUE FROM OVERFLOWING!
                if current_time - last_telemetry_write >= 0.33:
                    loop_time_ms = (time.perf_counter() - loop_start) * 1000.0
                    distance_m = effective_distance / 100.0

                    arrival_threshold_cm = 5.0
                    arrived = (
                            current_state == RoverState.AUTO
                            and abs(effective_distance - pid_dist.setpoint) <= arrival_threshold_cm
                    )

                    db_weight_kg = float(live_weight_kg)

                    db_writer.write_live_state(
                        mode=cmd_listener.mode if cmd_listener else "auto",
                        state=current_state.name,
                        weight_kg=db_weight_kg,
                        obstacle_hold=obstacle_hold_active,
                        arrived=arrived,
                        obstacle_avoid_active=obstacle_avoid_active,
                        luggage_fallen=luggage_fallen,
                        obstacle_reason=obstacle_reason or "",
                    )

                    db_writer.write_telemetry(
                        distance_m,
                        db_weight_kg,
                        obstacle_hold_active,
                        False,
                        ultra=ultra,
                        ultra_reason=obstacle_reason,
                        obstacle_avoid_active=obstacle_avoid_active,
                        uwb_live=uwb_live,
                        uwb_confidence=uwb_confidence.get('confidence', 0.0),
                        loop_time_ms=loop_time_ms,
                        state=current_state.name,
                        luggage_fallen=luggage_fallen,
                        weight_alarm_override=lc["override"],
                        luggage_weight_threshold_kg=lc["threshold_kg"],
                    )

                    db_writer.write_obstacle_sensor_debug(
                        state=current_state.name,
                        obstacle_reason=obstacle_reason,
                        obstacle_hold=obstacle_hold_active,
                        obstacle_avoid_active=obstacle_avoid_active,
                        ultra=ultra,
                    )

                    last_telemetry_write = current_time

                    csv_logger.write_row({
                        "ts_iso": datetime.now(timezone.utc).isoformat(),
                        "session_id": CURRENT_SESSION_ID,
                        "test_name": CURRENT_TEST_NAME,
                        "state": current_state.name,
                        "mode": cmd_listener.mode if cmd_listener else "unknown",
                        "distance_m": round(distance_m, 3),
                        "target_distance_cm": round(pid_dist.setpoint, 1),
                        "heading_deg": round(effective_heading, 2),
                        "target_heading_deg": round(pid_head.setpoint, 2),
                        "left_speed_cmd": round(left_speed, 2),
                        "right_speed_cmd": round(right_speed, 2),
                        "dynamic_bubble_cm": round(dynamic_bubble_cm, 1),
                        "dynamic_brake_cm": round(dynamic_brake_cm, 1),
                        "obstacle_hold": bool(obstacle_hold_active),
                        "obstacle_avoid_active": bool(obstacle_avoid_active),
                        "obstacle_reason": obstacle_reason,
                        "obstacle_hold": bool(obstacle_hold_active),  # <--- FIXED TYPO HERE
                        "obstacle_avoid_active": bool(obstacle_avoid_active),
                        "obstacle_reason": obstacle_reason,
                        "uwb_live": bool(uwb_live),
                        "uwb_confidence": round(uwb_confidence.get("confidence", 0.0), 1),
                        "loop_time_ms": round(loop_time_ms, 2),
                        "cpu_temp_c": round(heartbeat.cpu_temp, 1) if (heartbeat_enabled and heartbeat) else None,
                        "uptime_s": round(heartbeat.uptime_s, 1) if (heartbeat_enabled and heartbeat) else None,
                        "telem_buffer_size": cur_buf,
                        "weight_kg": round(live_weight_kg, 3),
                        "luggage_fallen": bool(luggage_fallen),
                        "weight_alarm_override": bool(lc["override"]),
                        "front_cm": ultra.get("front"),
                        "left_cm": ultra.get("left"),
                        "right_cm": ultra.get("right"),
                        "back_cm": ultra.get("back"),
                        "paired": bool(pairing_manager.is_paired()),
                        "uwb_override_enabled": bool(cmd_listener.uwb_override_enabled) if cmd_listener else False,
                        "anchor1_connected": pose_now.get("a1_connected") if uwb_listener else None,
                        "anchor2_connected": pose_now.get("a2_connected") if uwb_listener else None,
                    })

            # --- heartbeat  [old2.py] ---
            if heartbeat_enabled and heartbeat:
                loop_time_ms = (time.perf_counter() - loop_start) * 1000.0
                heartbeat.update(loop_time_ms, uwb_live, ultrasonic_enabled_local)

            # ==============================================================
            # GUI  [old2.py — system health dashboard with color-coded icons]
            # ==============================================================
            h, w = stdscr.getmaxyx()
            stdscr.erase()

            if h < MIN_H or w < MIN_W:
                safe_addstr(stdscr, 0, 0,
                            f"Terminal too small: {w}x{h}. Need {MIN_W}x{MIN_H}+",
                            curses.A_BOLD)
                safe_addstr(stdscr, 1, 0, "Press Q to quit.", curses.A_BOLD)
                stdscr.refresh()
                continue

            safe_addstr(stdscr, 0, 0, "=== ROVER NAV - MERGED ===", curses.A_BOLD)

            # ── System Health ──────────────────────────────────────────────
            safe_addstr(stdscr, 1, 0, "SYSTEM HEALTH:", curses.A_UNDERLINE)

            # UWB status row
            if uwb_live:
                uwb_color = curses.A_BOLD
                uwb_icon  = "UWB[OK]"
            elif uwb_lost:
                uwb_color = curses.A_BLINK | curses.A_STANDOUT
                uwb_icon  = "UWB[LOST]"
            else:
                uwb_color = curses.A_NORMAL
                uwb_icon  = "UWB[--]"
            safe_addstr(stdscr, 2, 2, f"{uwb_icon} {uwb_status}", uwb_color)

            # UWB confidence
            conf_val = uwb_confidence.get('confidence', 0.0) if isinstance(uwb_confidence, dict) else 0.0
            conf_str = f"Confidence: {conf_val:.0f}%"
            conf_color = (curses.A_BOLD   if conf_val >= 80
                          else curses.A_NORMAL if conf_val >= 60
                          else curses.A_DIM)
            safe_addstr(stdscr, 2, 30, conf_str, conf_color)

            # Ultrasonic status
            ultra_label = "ACTIVE" if ultrasonic_enabled_local else "DISABLED"
            safe_addstr(stdscr, 3, 2, f"Ultrasonic: {ultra_label}")

            # Pairing status
            if pairing_manager.is_paired():
                token     = pairing_manager.get_token() or ''
                user_code = str(int(token.replace('-', '')[:8], 16) % 1_000_000).zfill(6)
                pair_str  = f"Paired: {user_code}"
                pair_attr = curses.A_BOLD
            else:
                pair_str  = "NOT PAIRED"
                pair_attr = curses.A_BLINK | curses.A_STANDOUT
            safe_addstr(stdscr, 4, 2, pair_str, pair_attr)

            # ── State machine ───────────────────────────────────────────────
            safe_addstr(stdscr, 6, 0, f"STATE: {current_state.name}", curses.A_BOLD)
            mode_color = curses.A_NORMAL
            if control_mode == "E-STOP":
                mode_color = curses.A_BLINK | curses.A_STANDOUT
            elif control_mode in ("MANUAL", "MANUAL OVERRIDE"):
                mode_color = curses.A_BOLD
            safe_addstr(stdscr, 7, 2, f"Mode: {control_mode}", mode_color)

            loop_time_display = (time.perf_counter() - loop_start) * 1000.0
            safe_addstr(stdscr, 8, 2, f"Loop: {loop_time_display:.1f} ms")

            # ── UWB Tracking ────────────────────────────────────────────────
            safe_addstr(stdscr, 10, 0, "UWB TRACKING:", curses.A_UNDERLINE)
            uwb_attr = curses.A_BOLD if uwb_live else curses.A_NORMAL
            safe_addstr(stdscr, 11, 2,
                        f"Dist: {effective_distance:.1f} cm  "
                        f"Angle: {effective_heading:+.1f}  "
                        f"(sim: {sim_distance:.1f} cm / {sim_heading:.1f})",
                        uwb_attr)

            if supabase_enabled and cmd_listener:
                safe_addstr(stdscr, 12, 2,
                            f"Target dist: {pid_dist.setpoint:.1f} cm  "
                            f"Target heading: {pid_head.setpoint:.1f}")

            # ── Load Cell ───────────────────────────────────────────────────
            safe_addstr(stdscr, 13, 0, "LOAD CELL:", curses.A_UNDERLINE)
            if load_cell.hw_available:
                lug_status = "FALLEN" if luggage_fallen else "SECURE"
                lug_attr   = (curses.A_BLINK | curses.A_STANDOUT) if luggage_fallen else curses.A_BOLD
                override_str = "OVERRIDE ON" if lc["override"] else "threshold armed"
                weight_lb  = kg_to_lb(live_weight_kg)
                thr_lb     = kg_to_lb(lc['threshold_kg'])
                safe_addstr(stdscr, 14, 2,
                            f"Weight: {weight_lb:.2f} lb ({live_weight_kg:.3f} kg)  "
                            f"Thr: {thr_lb:.2f} lb  "
                            f"Status: {lug_status}  [{override_str}]",
                            lug_attr)
                # Show calibration file status on second line
                cal_str = (
                    f"Calibrated (file: {LOAD_CELL_CALIBRATION_FILE})"
                    if os.path.exists(LOAD_CELL_CALIBRATION_FILE)
                    else "Using fallback ref_unit — delete cal file to recalibrate"
                )
                safe_addstr(stdscr, 15, 2, cal_str, curses.A_DIM)
            else:
                tare_str = "tare done" if load_cell.tare_done else "no tare"
                err_str  = f" err={load_cell.last_error}" if load_cell.last_error else ""
                safe_addstr(stdscr, 14, 2,
                            f"HW not available ({tare_str}{err_str})",
                            curses.A_DIM)

            # ── Ultrasonic ──────────────────────────────────────────────────
            safe_addstr(stdscr, 16, 0, "ULTRASONIC:", curses.A_UNDERLINE)

            # Simplified UI to reflect the actual fluid bubble radius
            safe_addstr(stdscr, 17, 2, f"Sides (Carve): {dynamic_bubble_cm:.1f} cm  |  Front (Brake): {dynamic_brake_cm:.1f} cm")

            if ultrasonic_enabled_local:
                def fmt(n):
                    v = ultra.get(n)
                    if v is None:
                        return "--"
                    # Changed avoid.hard_stop_cm to a hardcoded 10.0
                    if v < 10.0:
                        return f"{int(v)}cm[CRITICAL]"
                    elif v < avoid.threshold_cm:
                        return f"{int(v)}cm[WARN]"
                    return f"{int(v)}cm"
                safe_addstr(stdscr, 18, 2, f"Front: {fmt('front')}")
                safe_addstr(stdscr, 19, 2, f"Back:  {fmt('back')}")
                safe_addstr(stdscr, 20, 2, f"Left:  {fmt('left')}")
                safe_addstr(stdscr, 21, 2, f"Right: {fmt('right')}")
            else:
                safe_addstr(stdscr, 18, 2, "Ultrasonic hardware not available",
                            curses.A_BLINK)

            # ── Motor Commands ───────────────────────────────────────────────
            safe_addstr(stdscr, 23, 0, "MOTOR COMMANDS:", curses.A_UNDERLINE)
            safe_addstr(stdscr, 24, 2, f"LEFT:  {left_speed:6.1f}%")
            safe_addstr(stdscr, 25, 2, f"RIGHT: {right_speed:6.1f}%")

            # ── PID State ────────────────────────────────────────────────────
            safe_addstr(stdscr, 27, 0, "PID STATE:", curses.A_UNDERLINE)
            safe_addstr(stdscr, 28, 2, f"Distance target: {pid_dist.setpoint:.1f} cm")
            safe_addstr(stdscr, 29, 2, f"Heading target:  {pid_head.setpoint:.1f}")

            # ── Telemetry buffer warning  [old2.py] ──────────────────────────
            if db_writer and db_writer.telem_buffer.size() > 0:
                safe_addstr(stdscr, 31, 0,
                            f"Telemetry buffered: {db_writer.telem_buffer.size()} entries",
                            curses.A_STANDOUT)

            # ── Controls ─────────────────────────────────────────────────────
            safe_addstr(stdscr, 33, 0, "CONTROLS:", curses.A_UNDERLINE)
            safe_addstr(stdscr, 34, 2,
                        "[SPACE] Smart Recovery  [L] Re-tare Load Cell  [Q] Quit")

            # ── Status bar (always last row) ─────────────────────────────────
            status_row = h - 1
            if safety_active:
                safe_addstr(stdscr, status_row, 2,
                            f"!!! {safety_msg} !!!",
                            curses.A_BLINK | curses.A_STANDOUT)
            else:
                safe_addstr(stdscr, status_row, 2, "SYSTEM ACTIVE", curses.A_BOLD)

            stdscr.refresh()

    except KeyboardInterrupt:
        pass
    finally:
        # Clear the pairing session so the app sees paired=false immediately
        # and knows it must re-pair on the rover's next boot.
        try:
            supa_raw.table('pairing_sessions').update({
                'paired': False,
                'session_token': None,
                'confirmed_at': None,
            }).eq('robot_id', ROBOT_ID).execute()
            print('[SHUTDOWN] Pairing session cleared.', file=sys.stderr)
        except Exception as _e:
            print(f'[SHUTDOWN] Could not clear pairing session: {_e}', file=sys.stderr)
        try: pairing_manager.stop()
        except: pass
        try: avoid.stop()
        except: pass
        try: load_cell.stop()
        except: pass
        try: buzzer.stop()
        except: pass
        try:
            if cmd_listener: cmd_listener.stop()
        except: pass
        try:
            if uwb_listener: uwb_listener.stop()
        except: pass
        try:
            if heartbeat_enabled and heartbeat: heartbeat.stop()
        except: pass
        try: saber.close()
        except: pass


if __name__ == "__main__":
    # Redirect stderr so it doesn't corrupt the curses display
    
    _debug_log = open('/tmp/rover_debug.log', 'w')
    sys.stderr = _debug_log
    curses.wrapper(main)
