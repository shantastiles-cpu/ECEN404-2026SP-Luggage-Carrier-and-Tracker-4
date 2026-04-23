#!/usr/bin/env python3
"""
NAVIAPP_movementtest_merged.py
================================
ARCHITECTURE  : old2.py  (state machine, heartbeat, telemetry buffer,
                           UWB confidence scorer, pairing manager,
                           adaptive thresholds, enhanced GUI)
FOLLOW LOGIC  : movementtest.py  (UwbPoseListener with is_pose_good(),
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

import os
import json
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

import argparse

import lgpio as GPIO
from supabase import create_client, Client

try:
    import RPi.GPIO as RPIGPIO
    from hx711 import HX711
    HX711_AVAILABLE = True
except ImportError:
    HX711_AVAILABLE = False


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
    p.add_argument("--log-dir", default="/home/pi5-2/Desktop/Luggage/validation_logs",
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
LOAD_CELL_CALIBRATION_FILE = "/home/pi5-2/Desktop/Luggage/loadcell_calibration.json"
LOAD_CELL_DT_PIN   = 17                      # GPIO5  (BCM)
LOAD_CELL_SCK_PIN  = 27                      # GPIO6  (BCM)
LOAD_CELL_SAMPLES  = 5                      # readings averaged per measurement
LOAD_CELL_POLL_S   = 0.25                   # how often to read (seconds)
DEFAULT_FOLLOW_DISTANCE_CM = 100.0

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
        """Re-tare the load cell safely in a background thread."""
        def _do_tare():
            with self._lock:
                hx = self._hx
                hw_available = self.hw_available

            if not hw_available or hx is None:
                with self._lock:
                    self.last_error = "tare requested but HX711 is not available"
                print("[LOADCELL] tare skipped: HX711 not available", file=sys.stderr)
                return

            try:
                print("[LOADCELL] Re-taring...", file=sys.stderr)
                hx.tare(times=20)
                with self._lock:
                    self.tare_done = True
                    self.last_error = None
                print("[LOADCELL] Re-tare complete.", file=sys.stderr)
            except Exception as e:
                with self._lock:
                    self.last_error = str(e)
                print(f"[LOADCELL] tare failed: {e}", file=sys.stderr)

        threading.Thread(target=_do_tare, daemon=True).start()

    def start(self):
        if not HX711_AVAILABLE:
            print("[LOADCELL] hx711/RPi.GPIO not installed — load cell disabled", file=sys.stderr)
            return

        try:
            RPIGPIO.setwarnings(False)
            self._hx = HX711(LOAD_CELL_DT_PIN, LOAD_CELL_SCK_PIN)
            self._hx.set_reading_format("MSB", "MSB")

            ref = load_or_calibrate(self._hx)
            self._hx.set_reference_unit(ref)

            print("[LOADCELL] Taring with empty tray (5s settle)…", file=sys.stderr)
            time.sleep(5)
            self._hx.tare(times=20)

            with self._lock:
                self.tare_done = True
                self.hw_available = True

            print("[LOADCELL] Tare done. Reading started.", file=sys.stderr)

            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        except Exception as e:
            print(f"[LOADCELL] start() failed: {e}", file=sys.stderr)
            with self._lock:
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
                # get_weight(N) takes N samples and returns grams
                grams = self._hx.get_weight(LOAD_CELL_SAMPLES)
                self._hx.power_down()
                self._hx.power_up()

                kg = max(0.0, grams / 1000.0)  # clamp negative noise to 0

                with self._lock:
                    self.weight_kg = kg
                    # Only flag fallen if override is NOT active
                    if not self.weight_alarm_override:
                        self.luggage_fallen = (kg < self.threshold_kg)
                    else:
                        self.luggage_fallen = False
                    self.last_error = None

            except Exception as e:
                with self._lock:
                    self.last_error = str(e)
                print(f"[LOADCELL] read error: {e}", file=sys.stderr)

            time.sleep(LOAD_CELL_POLL_S)


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
            "obstacle_hold", "obstacle_avoid_active", "obstacle_reason",
            "uwb_live", "uwb_confidence",
            "loop_time_ms",                       # System Health Telemetry
            "cpu_temp_c",                         # System Health Telemetry
            "uptime_s",                           # System Health Telemetry
            "telem_buffer_size",                  # Telemetry Buffering
            "weight_kg", "luggage_fallen", "weight_alarm_override",
            "front_cm", "left_cm", "right_cm", "back_cm",
            "paired",                             # Secure Pairing Gate
            "uwb_override_enabled",               # UWB Override Recovery
            "anchor1_connected", "anchor2_connected",  # Anchor Disruption
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
class PairingManager:
    def __init__(self, supabase: Client, robot_id: str):
        self._supa     = supabase
        self._robot_id = robot_id
        self._lock     = threading.Lock()
        self._token    = None
        self._stop     = threading.Event()
        self._thread   = threading.Thread(target=self._poll, daemon=True)

    def start(self):
        self._clear_stale_session()
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

    def _clear_stale_session(self):
        try:
            self._supa.table('pairing_sessions').update({
                'paired': False, 'session_token': None, 'confirmed_at': None,
            }).eq('robot_id', self._robot_id).execute()
        except Exception as e:
            print(f"[PAIR] clear stale failed: {e}", file=sys.stderr)

    def _poll(self):
        print("[PAIR] Waiting for app pairing...", file=sys.stderr)
        while not self._stop.is_set():
            try:
                result = (
                    self._supa.table('pairing_sessions')
                    .select('session_token, paired, expires_at')
                    .eq('robot_id', self._robot_id)
                    .execute()
                )
                if result.data:
                    row            = result.data[0]
                    already_paired = row.get('paired', False)
                    token          = row.get('session_token')
                    expires_str    = row.get('expires_at', '')

                    if token and not already_paired and self._not_expired(expires_str):
                        self._confirm_pairing(token)
                    elif token and already_paired:
                        with self._lock:
                            if self._token != token:
                                self._token = token
                                print("[PAIR] Re-paired", file=sys.stderr)
                        # Also re-stamp confirmed_at so expiry logic stays fresh
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
# UWB Pose Listener  [movementtest.py — updated follow-user tracking logic]
# ======================================================================================
class UwbPoseListener:
    """
    Polls uwb_positions from Supabase every poll_s seconds.
    Exposes is_pose_good() so the main loop can decide whether to trust the
    measurement or fall back to sim — and stop safely if in AUTO mode.
    """
    def __init__(self, supabase_url, supabase_key, robot_id,
                 poll_s=0.10, stale_s=0.6, min_conf=30.0, use_health=False):
        self.supabase   = create_client(supabase_url, supabase_key)
        self.robot_id   = robot_id
        self.poll_s     = float(poll_s)
        self.stale_s    = float(stale_s)
        self.min_conf   = float(min_conf) if min_conf is not None else None
        self.use_health = bool(use_health)

        self._lock    = threading.Lock()
        self._stop    = threading.Event()
        self._thread  = threading.Thread(target=self._run, daemon=True)

        self.distance_cm      = None
        self.angle_deg        = None
        self.confidence       = None
        self.updated_at       = None
        self.last_error       = None
        self.anchor1_connected = None
        self.anchor2_connected = None
        self.anchor1_age_s    = None
        self.anchor2_age_s    = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._thread.join(timeout=1.0)
        except:
            pass

    def _parse_dt(self, v):
        if not v:
            return None
        try:
            if isinstance(v, str) and v.endswith("Z"):
                v = v[:-1] + "+00:00"
            return datetime.fromisoformat(v).astimezone(timezone.utc)
        except:
            return None

    def get(self) -> dict:
        with self._lock:
            age_s = None
            if self.updated_at is not None:
                age_s = (datetime.now(timezone.utc) - self.updated_at).total_seconds()
            return {
                "distance_cm": self.distance_cm,
                "angle_deg":   self.angle_deg,
                "confidence":  self.confidence,
                "age_s":       age_s,
                "updated_at":  self.updated_at,
                "a1_connected": self.anchor1_connected,
                "a2_connected": self.anchor2_connected,
                "a1_age_s":    self.anchor1_age_s,
                "a2_age_s":    self.anchor2_age_s,
                "last_error":  self.last_error,
            }

    def is_pose_good(self, pose: dict) -> bool:
        if pose.get("distance_cm") is None or pose.get("angle_deg") is None:
            return False
        age_s = pose.get("age_s")
        if age_s is None or age_s > self.stale_s:
            return False
        conf = pose.get("confidence")
        if self.min_conf is not None and conf is not None and conf < self.min_conf:
            return False
        if self.use_health:
            if pose.get("a1_connected") is False or pose.get("a2_connected") is False:
                return False
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                res = (
                    self.supabase.table("uwb_positions")
                    .select("distance_m, angle_deg, confidence, updated_at")
                    .eq("robot_id", self.robot_id)
                    .limit(1)
                    .execute()
                )
                row      = res.data[0] if (res.data and len(res.data) > 0) else None
                dist_cm  = ang = conf = upd = None

                if row:
                    dm  = row.get("distance_m")
                    ang = row.get("angle_deg")
                    conf = row.get("confidence")
                    upd = self._parse_dt(row.get("updated_at"))
                    if dm is not None:
                        dist_cm = float(dm) * 100.0
                    if ang is not None:
                        ang = float(ang)
                    if conf is not None:
                        conf = float(conf)

                a1c = a2c = a1age = a2age = None
                if self.use_health:
                    hr = (
                        self.supabase.table("uwb_health")
                        .select("anchor1_connected, anchor2_connected, anchor1_age_s, anchor2_age_s")
                        .eq("robot_id", self.robot_id)
                        .limit(1)
                        .execute()
                    )
                    hrow = hr.data[0] if (hr.data and len(hr.data) > 0) else None
                    if hrow:
                        a1c   = bool(hrow.get("anchor1_connected"))
                        a2c   = bool(hrow.get("anchor2_connected"))
                        a1age = float(hrow.get("anchor1_age_s") or 0.0)
                        a2age = float(hrow.get("anchor2_age_s") or 0.0)

                with self._lock:
                    self.distance_cm       = dist_cm
                    self.angle_deg         = ang
                    self.confidence        = conf
                    self.updated_at        = upd
                    self.anchor1_connected = a1c
                    self.anchor2_connected = a2c
                    self.anchor1_age_s     = a1age
                    self.anchor2_age_s     = a2age
                    self.last_error        = None

            except Exception as e:
                with self._lock:
                    self.last_error = str(e)

            time.sleep(self.poll_s)


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
        self.obstacle_threshold_cm  = 25
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
                    if self.pairing_manager.is_paired() and not self.pairing_manager.validate(incoming_token):
                        print(
                            f"[CMD] ignored due to session token mismatch "
                            f"incoming={incoming_token} expected={self.pairing_manager.get_token()}",
                            file=sys.stderr
                        )
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
                self.obstacle_threshold_cm  = int(cmd.get('obstacle_threshold_cm') or 25)
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
        self.supabase    = create_client(supabase_url, supabase_key)
        self.robot_id    = robot_id
        self.telem_buffer = TelemetryBuffer()

    def write_nav_state(self, left_speed, right_speed):
        try:
            data = {
                'robot_id':       self.robot_id,
                'left_speed_cmd': float(left_speed),
                'right_speed_cmd': float(right_speed),
                'updated_at':     datetime.now(timezone.utc).isoformat(),
            }
            self.supabase.table('nav_state').upsert(data, on_conflict='robot_id').execute()
        except:
            pass

    def write_obstacle_alert(self, reason: str):
        try:
            data = {
                'robot_id':   self.robot_id,
                'alert_type': 'obstacle',
                'message':    reason,
                'created_at': datetime.now(timezone.utc).isoformat(),
            }
            self.supabase.table('rover_alerts').insert(data).execute()
        except Exception as e:
            print(f"[DB] write_obstacle_alert failed: {e}", file=sys.stderr)

    def write_live_state(self, mode, state, weight_kg, obstacle_hold, arrived,
             obstacle_avoid_active=False, luggage_fallen=False,
             obstacle_reason=None):
        try:
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
            self.supabase.table('rover_live_state').upsert(
                data, on_conflict='robot_id'
            ).execute()
            print(f"[DB] live_state wrote weight_kg={data['weight_kg']}", file=sys.stderr)
        except Exception as e:
            print(f"[DB] write_live_state failed: {e}", file=sys.stderr)

    def write_telemetry(self, distance_m, weight_kg, obstacle_hold, arrived,
                    ultra=None, ultra_reason=None, obstacle_avoid_active=False,
                    uwb_live=False, uwb_confidence=None,
                    loop_time_ms=0.0, state=None,
                    luggage_fallen=False, weight_alarm_override=False,
                    luggage_weight_threshold_kg=LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT):
        ultra = ultra or {}

        def to_int(v):
            return None if v is None else int(v)

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
            'luggage_fallen': bool(luggage_fallen),
            'weight_alarm_override': bool(weight_alarm_override),
            'luggage_weight_threshold_kg': round(float(luggage_weight_threshold_kg), 3),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.supabase.table('telemetry_snapshots').insert(data).execute()
            print(f"[DB] telemetry wrote weight_kg={data['weight_kg']}", file=sys.stderr)
            if self.telem_buffer.size() > 0:
                self.telem_buffer.flush_to(self)
        except Exception as e:
            self.telem_buffer.add(data)
            print(f"[DB] Telemetry buffered ({self.telem_buffer.size()}): {e}", file=sys.stderr)


    def write_obstacle_sensor_debug(
            self,
            state,
            obstacle_reason,
            obstacle_hold,
            obstacle_avoid_active,
            ultra_for_db=None,
            ultra=None,
            ultra_fast=None,
        ):
        ultra_for_db = ultra_for_db or {}
        ultra = ultra or {}
        ultra_fast = ultra_fast or {}

        def to_int(v):
            return None if v is None else int(v)

        data = {
            "robot_id": self.robot_id,
            "state": state,
            "obstacle_reason": obstacle_reason,
            "obstacle_hold": bool(obstacle_hold),
            "obstacle_avoid_active": bool(obstacle_avoid_active),

            "ultra_front_cm": to_int(ultra_for_db.get("front")),
            "ultra_left_cm": to_int(ultra_for_db.get("left")),
            "ultra_right_cm": to_int(ultra_for_db.get("right")),
            "ultra_back_cm": to_int(ultra_for_db.get("back")),

            "ema_front_cm": to_int(ultra.get("front")),
            "ema_left_cm": to_int(ultra.get("left")),
            "ema_right_cm": to_int(ultra.get("right")),
            "ema_back_cm": to_int(ultra.get("back")),

            "fast_front_cm": to_int(ultra_fast.get("front")),
            "fast_left_cm": to_int(ultra_fast.get("left")),
            "fast_right_cm": to_int(ultra_fast.get("right")),
            "fast_back_cm": to_int(ultra_fast.get("back")),
        }

        try:
            self.supabase.table("obstacle_sensor_debug").insert(data).execute()
        except Exception as e:
            print(f"[DB] write_obstacle_sensor_debug failed: {e}", file=sys.stderr)

# ======================================================================================
# Adaptive Obstacle Threshold  [from old2.py]
# ======================================================================================
class AdaptiveThreshold:
    BASE_THRESHOLD_CM = 25
    MIN_THRESHOLD_CM  = 10
    MAX_THRESHOLD_CM  = 40

    @staticmethod
    def calculate(base_speed: float, max_speed: float = 100.0) -> int:
        speed_ratio = abs(base_speed) / max_speed
        if speed_ratio < 0.3:
            return AdaptiveThreshold.BASE_THRESHOLD_CM
        extra        = AdaptiveThreshold.MAX_THRESHOLD_CM - AdaptiveThreshold.BASE_THRESHOLD_CM
        scaled_extra = extra * ((speed_ratio - 0.3) / 0.7)
        return int(AdaptiveThreshold.BASE_THRESHOLD_CM + scaled_extra)


# ======================================================================================
# Ultrasonic Avoidance  [old2.py structure + movementtest.py speed values]
# ======================================================================================
SENSORS = {
    "front": {"trig": 23, "echo": 12},
    "right": {"trig": 25, "echo": 16},
    "left":  {"trig": 8,  "echo": 20},
    "back":  {"trig": 7,  "echo": 21},
}

EMA_ALPHA        = 0.65
SAMPLES          = 3
PING_GAP         = 0.008
SPEED_CM_S       = 34300
ECHO_TIMEOUTS    = {"front": 0.10, "back": 0.10, "right": 0.08, "left": 0.08}
TRIG_PULSE_S     = 12e-6
MIN_CM, MAX_CM   = 2, 400
MISSES_TO_BLANK  = 10
POST_EVENT_PAUSE = 0.08

# [movementtest.py] — gentler avoidance maneuver speeds
AV_STOP_DUR  = 0.10
AV_REV       = -15   # was -20 in old2.py, -45 in old.py
AV_PIVOT     = 20    # was 20 in old2.py, 55 in old.py
AV_REV_DUR   = 0.35
AV_PIVOT_DUR = 0.35


def clamp(x, lo=-100, hi=100):
    return max(lo, min(hi, x))


class UltrasonicAvoidance:
    def __init__(self):
        self._lock        = threading.Lock()
        self._stop        = threading.Event()
        self._ema         = {n: None for n in SENSORS}
        self._misses      = {n: 0    for n in SENSORS}
        self._fast        = {n: None for n in SENSORS}
        self._fast_misses = {n: 0    for n in SENSORS}
        self._override        = None
        self._override_reason = None
        self.threshold_cm     = 25
        self.clear_margin_cm  = 8
        self.action           = "avoid"
        self.hard_stop_cm     = max(5, min(15, int(self.threshold_cm) - 46))
        self._move_dir        = "stop"
        self._h               = None
        self._thread          = None

    def set_params(self, threshold_cm, clear_margin_cm, action):
        with self._lock:
            self.threshold_cm    = int(threshold_cm)
            self.clear_margin_cm = int(clear_margin_cm)
            self.action          = action if action in ("stop", "avoid") else "avoid"
            self.hard_stop_cm    = max(5, min(15, self.threshold_cm - 46))

    def set_move_dir(self, move_dir):
        with self._lock:
            self._move_dir = move_dir or "stop"

    def _wait_for(self, pin, level, timeout):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            if GPIO.gpio_read(self._h, pin) == level:
                return time.perf_counter()
            time.sleep(0.00005)
        return None

    def _ping_once(self, trig, echo, to):
        GPIO.gpio_write(self._h, trig, 0); time.sleep(5e-6)
        GPIO.gpio_write(self._h, trig, 1); time.sleep(TRIG_PULSE_S)
        GPIO.gpio_write(self._h, trig, 0)
        t0 = self._wait_for(echo, 1, to)
        if t0 is None:
            return None
        t1 = self._wait_for(echo, 0, to)
        if t1 is None:
            return None
        d = (t1 - t0) * SPEED_CM_S / 2.0
        return d if (MIN_CM <= d <= MAX_CM) else None

    def _robust_measure(self, name, trig, echo, n=SAMPLES):
        vals = []
        to   = ECHO_TIMEOUTS.get(name, 0.08)
        for _ in range(n):
            d = self._ping_once(trig, echo, to)
            if d is not None:
                vals.append(d)
            time.sleep(PING_GAP)
        if not vals:
            return None
        med  = statistics.median(vals)
        devs = [abs(v - med) for v in vals]
        mad  = statistics.median(devs) if devs else 0
        keep = [v for v, dv in zip(vals, devs) if dv <= 2.5 * mad] if mad > 0 else vals
        return statistics.median(keep or vals)

    def _fast_measure(self, name, trig, echo, retries=1):
        to = ECHO_TIMEOUTS.get(name, 0.08)
        for _ in range(max(1, retries)):
            d = self._ping_once(trig, echo, to)
            if d is not None:
                return d
            time.sleep(0.001)
        return None

    def get_distances(self):
        with self._lock:
            return dict(self._ema)

    def get_fast_distances(self):
        with self._lock:
            return dict(self._fast)

    def _set_override(self, l, r, dur, reason):
        until = time.perf_counter() + dur
        with self._lock:
            self._override        = (clamp(l), clamp(r), until)
            self._override_reason = reason

    def get_override(self):
        now = time.perf_counter()
        with self._lock:
            if self._override is None:
                return None, None
            l, r, until = self._override
            if now >= until:
                self._override        = None
                self._override_reason = None
                return None, None
            return (l, r), self._override_reason

    def clear_override(self):
        with self._lock:
            self._override        = None
            self._override_reason = None

    # [old2.py] — per-sensor avoidance decision with full sensor context
    def maybe_trigger_for_command_any_sensor(self, sensor_name, value, move_dir):
        ov, _ = self.get_override()
        if ov is not None:
            return

        with self._lock:
            threshold   = int(self.threshold_cm)
            hard_stop   = int(self.hard_stop_cm)
            clear_level = threshold + int(self.clear_margin_cm)
            action      = self.action
            fast        = dict(self._fast)

        if value <= hard_stop:
            reason = f"HARD STOP {sensor_name.upper()} ({int(value)} cm)"
            self._set_override(0, 0, max(AV_STOP_DUR, 0.15), reason)
            time.sleep(POST_EVENT_PAUSE)
            return

        def d(n):
            x = fast.get(n)
            return x if x is not None else 0.0

        f, r, l, b = d("front"), d("right"), d("left"), d("back")

        def pivot_left():
            self._set_override(-AV_PIVOT, +AV_PIVOT, AV_PIVOT_DUR,
                               f"TURN LEFT: OBSTACLE {sensor_name.upper()} ({int(value)} cm)")

        def pivot_right():
            self._set_override(+AV_PIVOT, -AV_PIVOT, AV_PIVOT_DUR,
                               f"TURN RIGHT: OBSTACLE {sensor_name.upper()} ({int(value)} cm)")

        def reverse():
            self._set_override(AV_REV, AV_REV, AV_REV_DUR,
                               f"REVERSE: OBSTACLE {sensor_name.upper()} ({int(value)} cm)")

        if action == "stop":
            self._set_override(0, 0, AV_STOP_DUR,
                               f"STOP: OBSTACLE {sensor_name.upper()} ({int(value)} cm)")
            time.sleep(POST_EVENT_PAUSE)
            return

        if sensor_name == "front":
            if b > clear_level:
                reverse()
            elif r >= l:
                pivot_right()
            else:
                pivot_left()
        elif sensor_name == "back":
            if r >= l:
                pivot_left()
            else:
                pivot_right()
        elif sensor_name == "left":
            pivot_right()
        elif sensor_name == "right":
            pivot_left()

        time.sleep(POST_EVENT_PAUSE)

    def start(self):
        self._h = GPIO.gpiochip_open(0)
        for _, cfg in SENSORS.items():
            GPIO.gpio_claim_output(self._h, cfg["trig"])
            GPIO.gpio_write(self._h, cfg["trig"], 0)
            GPIO.gpio_claim_input(self._h, cfg["echo"])
            try:
                GPIO.gpio_set_pull_up_down(self._h, cfg["echo"], GPIO.LGPIO_PULL_DOWN)
            except:
                pass
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._h is not None:
            GPIO.gpiochip_close(self._h)
            self._h = None

    def _run(self):
        FAST_RETRIES = 2
        UI_SAMPLES   = SAMPLES

        def sensor_order_for(move_dir):
            if move_dir == "fwd":   return ["front", "left", "right", "back"]
            if move_dir == "rev":   return ["back",  "left", "right", "front"]
            if move_dir == "left":  return ["left",  "front", "back", "right"]
            if move_dir == "right": return ["right", "front", "back", "left"]
            return ["front", "left", "right", "back"]

        ui_every = {"front": 2, "left": 3, "right": 3, "back": 4}
        tick     = 0

        while not self._stop.is_set():
            tick += 1
            with self._lock:
                move_dir = self._move_dir
            order = sensor_order_for(move_dir)

            # Fast safety scan for all sensors (priority order)
            for fast_name in order:
                fast_cfg = SENSORS[fast_name]
                retries  = FAST_RETRIES if fast_name == order[0] else 1
                fval     = self._fast_measure(fast_name, fast_cfg["trig"],
                                              fast_cfg["echo"], retries=retries)
                with self._lock:
                    if fval is not None:
                        self._fast_misses[fast_name] = 0
                        self._fast[fast_name] = fval
                    else:
                        self._fast_misses[fast_name] += 1
                        if self._fast_misses[fast_name] >= MISSES_TO_BLANK:
                            self._fast[fast_name] = None

            # UI smoothing at slower cadence
            for name in order:
                if tick % ui_every.get(name, 3) != 0:
                    continue
                cfgu = SENSORS[name]
                dval = self._robust_measure(name, cfgu["trig"], cfgu["echo"], n=UI_SAMPLES)
                with self._lock:
                    if dval is not None:
                        self._misses[name] = 0
                        self._ema[name] = dval if self._ema[name] is None else (
                            EMA_ALPHA * dval + (1 - EMA_ALPHA) * self._ema[name]
                        )
                    else:
                        self._misses[name] += 1
                        if self._misses[name] >= MISSES_TO_BLANK:
                            self._ema[name] = None

            time.sleep(0.001)


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
    def __init__(self, Kp, Ki, Kd, setpoint, integral_limit=50.0):
        self.Kp             = Kp
        self.Ki             = Ki
        self.Kd             = Kd
        self.setpoint       = setpoint
        self.integral_limit = integral_limit
        self.prev_error     = 0
        self.integral       = 0

    def update(self, current_value, is_heading_pid=False):
        if is_heading_pid:
            error = self.setpoint - current_value
            if error > 180:  error -= 360
            elif error < -180: error += 360
        else:
            error = current_value - self.setpoint

        self.integral += error
        self.integral  = max(-self.integral_limit, min(self.integral_limit, self.integral))
        derivative     = error - self.prev_error
        output         = (self.Kp * error) + (self.Ki * self.integral) + (self.Kd * derivative)
        self.prev_error = error
        return output

    def reset(self):
        self.integral   = 0
        self.prev_error = 0


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
# Main curses app  —  old2.py GUI + movementtest.py follow-tracking logic
# ======================================================================================
def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(50)

    SUPABASE_URL = "https://jbhjvtfeezmityewhtwa.supabase.co"
    SUPABASE_KEY = "sb_publishable_z7vaIDJQaQcjB0bpKG7Rzg_0m6boJHf"
    ROBOT_ID     = "rover_01"
    SERIAL_PORT  = "/dev/serial0"

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

    # UWB — [movementtest.py] UwbPoseListener with is_pose_good() gating
    uwb_listener = None
    uwb_enabled  = False
    if supabase_enabled:
        try:
            uwb_listener = UwbPoseListener(
                SUPABASE_URL, SUPABASE_KEY, ROBOT_ID,
                poll_s=0.10,
                stale_s=1.5,
                min_conf=30.0,
                use_health=False,
            )
            uwb_listener.start()
            uwb_enabled = True
        except Exception as e:
            print(f"[UWB] listener start failed: {e}", file=sys.stderr)

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
    HIT_CONFIRM = 2
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

    csv_logger     = CsvTelemetryLogger(ROVER_CSV_PATH)
    event_logger   = CsvEventLogger(ROVER_EVENT_PATH)
    latency_logger = CsvLatencyLogger(ROVER_LATENCY_PATH)

    # Log session start event
    event_logger.log("SESSION_START",
                     detail=f"test={CURRENT_TEST_NAME} session={CURRENT_SESSION_ID}")

    # PID  — [movementtest.py] reduced speed caps for safer UWB follow
    DIST_KP, DIST_KI, DIST_KD = 0.2, 0.5, 0.00
    HEAD_KP, HEAD_KI, HEAD_KD = 0.5, 0.1,  0.00
    SPEED_CAP      = 35.0
    TURN_CAP       = 25.0
    MAX_FOLLOW_DIST = 183.0  # 6 ft

    pid_dist = PIDController(
        DIST_KP, DIST_KI, DIST_KD,
        DEFAULT_FOLLOW_DISTANCE_CM,
        integral_limit=40.0
    )
    pid_head = PIDController(HEAD_KP, HEAD_KI, HEAD_KD,  0.0, integral_limit=20.0)

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

    try:
        while True:
            loop_start   = time.perf_counter()
            current_time = time.time()

            # --- keyboard ---
            key = stdscr.getch()
            if key == ord('q'):
                break
            elif key == ord('l'):
                # [L] triggers a tare/re-zero of the load cell
                print("[LOADCELL] Manual tare requested via [L]", file=sys.stderr)
                load_cell.request_tare()
            elif key == ord('l'):
                # [L] now triggers a tare/re-zero of the load cell (not a toggle)
                if load_cell.hw_available:
                    print("[LOADCELL] Manual tare requested via [L]", file=sys.stderr)
                    threading.Thread(
                        target=lambda: (load_cell._hx.tare() if load_cell._hx else None),
                        daemon=True
                    ).start()
                else:
                    # hw not present — toggle sim fallen flag for testing
                    pass
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
            lc = load_cell.get()
            luggage_fallen = lc["luggage_fallen"]
            live_weight_kg = lc["weight_kg"] if lc["hw_available"] else sim_weight_kg

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
                    # Re-tare the load cell so the baseline resets
                    load_cell.request_tare()
                    sim_weight_kg = 5.4
                    pid_dist.reset()
                    pid_head.reset()
                    cmd_listener.reset_luggage = False

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

                avoid.set_params(
                    cmd_listener.obstacle_threshold_cm,
                    cmd_listener.obstacle_clear_margin_cm,
                    cmd_listener.obstacle_action,
                )
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
                        if val is not None and val <= avoid.hard_stop_cm:
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

                arrival_threshold_cm = 5.0

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

                    base_speed_raw = pid_dist.update(effective_distance)
                    base_speed = max(-SPEED_CAP, min(SPEED_CAP, base_speed_raw))

                    if base_speed > 0:
                        base_speed *= forward_scale

                    turn_adjust_raw = pid_head.update(effective_heading, is_heading_pid=True)
                    turn_adjust = max(-TURN_CAP, min(TURN_CAP, turn_adjust_raw))

                    left_speed = max(-100, min(100, base_speed + turn_adjust))
                    right_speed = max(-100, min(100, base_speed - turn_adjust))
                    control_mode = "AUTO"


                    print(
                        f"[AUTO_PID] "
                        f"dist_cm={effective_distance:.1f} "
                        f"set_cm={pid_dist.setpoint:.1f} "
                        f"head_deg={effective_heading:.1f} "
                        f"base_raw={base_speed_raw:.2f} "
                        f"base={base_speed:.2f} "
                        f"turn_raw={turn_adjust_raw:.2f} "
                        f"turn={turn_adjust:.2f} "
                        f"L={left_speed:.2f} "
                        f"R={right_speed:.2f}",
                        file=sys.stderr,
                    )

                    # Stop cleanly when already at follow target
                    if abs(effective_distance - pid_dist.setpoint) <= arrival_threshold_cm and angle_error <= ALIGN_START_DEG:
                        left_speed = right_speed = 0.0
                        safety_msg = "AT FOLLOW TARGET"

            # --- ultrasonic override  [old2.py — adaptive threshold + hit confirmation] ---
            ultra      = avoid.get_distances()      if ultrasonic_enabled_local else {}
            ultra_fast = avoid.get_fast_distances() if ultrasonic_enabled_local else {}

            ultra_for_db = {
                "front": ultra.get("front") if ultra.get("front") is not None else ultra_fast.get("front"),
                "left":  ultra.get("left")  if ultra.get("left")  is not None else ultra_fast.get("left"),
                "right": ultra.get("right") if ultra.get("right") is not None else ultra_fast.get("right"),
                "back":  ultra.get("back")  if ultra.get("back")  is not None else ultra_fast.get("back"),
            }
            any_ultra  = any(v is not None for v in ultra_fast.values())

            if (not safety_active) and obstacle_avoid_enabled and ultrasonic_enabled_local:
                if current_state != RoverState.MANUAL_OVERRIDE:
                    avg_speed         = (abs(left_speed) + abs(right_speed)) / 2.0
                    adaptive_threshold = AdaptiveThreshold.calculate(avg_speed)
                    avoid.set_params(adaptive_threshold,
                                     cmd_listener.obstacle_clear_margin_cm,
                                     cmd_listener.obstacle_action)

                    move_dir  = command_direction(left_speed, right_speed)
                    avoid.set_move_dir(move_dir)
                    fast      = avoid.get_fast_distances()
                    threshold = int(avoid.threshold_cm)
                    clear_margin = int(avoid.clear_margin_cm)

                    def update_hit(name, val, thr, cm):
                        if val is None:
                            hit_count[name] = max(0, hit_count[name] - 1)
                            return False
                        if val <= thr:
                            hit_count[name] += 1
                        elif val >= (thr + cm):
                            hit_count[name] = 0
                        return hit_count[name] >= HIT_CONFIRM

                    fv = fast.get("front")
                    bv = fast.get("back")
                    lv = fast.get("left")
                    rv = fast.get("right")

                    front_hit = update_hit("front", fv, threshold, clear_margin)
                    back_hit  = update_hit("back",  bv, threshold, clear_margin)
                    left_hit  = update_hit("left",  lv, threshold, clear_margin)
                    right_hit = update_hit("right", rv, threshold, clear_margin)

                    if front_hit and back_hit:
                        avoid.maybe_trigger_for_command_any_sensor(
                            "front", min(fv, bv), move_dir)
                    elif front_hit:
                        avoid.maybe_trigger_for_command_any_sensor("front", fv, move_dir)
                    elif back_hit:
                        avoid.maybe_trigger_for_command_any_sensor("back", bv, move_dir)
                    elif left_hit and not right_hit:
                        avoid.maybe_trigger_for_command_any_sensor("left", lv, move_dir)
                    elif right_hit and not left_hit:
                        avoid.maybe_trigger_for_command_any_sensor("right", rv, move_dir)
                    elif left_hit and right_hit:
                        avoid.maybe_trigger_for_command_any_sensor("front", threshold, move_dir)

                    ov, ov_reason = avoid.get_override()
                    if ov is not None:
                        ov_left, ov_right = ov
                        obstacle_reason   = ov_reason
                        is_hold, is_avoid = classify_override(ov_reason, ov_left, ov_right)
                        obstacle_avoid_active = is_avoid

                        if is_hold:
                            left_speed = right_speed = 0.0
                            safety_active        = True
                            obstacle_hold_active = True
                            safety_msg           = ov_reason or "OBSTACLE HOLD"
                            current_state        = RoverState.HARD_STOP
                        else:
                            left_speed, right_speed = ov_left, ov_right
                            safety_msg  = ov_reason or "OBSTACLE AVOIDING"
                            current_state = RoverState.OBSTACLE_AVOID

                        if supabase_enabled and db_writer:
                            now = time.perf_counter()
                            if not _last_obstacle_active and (now - _last_alert_time > 2.0):
                                db_writer.write_obstacle_alert(obstacle_reason or "OBSTACLE")
                                _last_alert_time = now

                        _last_obstacle_active = True
                    else:
                        _last_obstacle_active = False

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

            # Anchor connect/disconnect events (edge-triggered — only fires on change)
            if uwb_enabled and uwb_listener:
                a1ok = uwb_listener.anchor1_connected
                a2ok = uwb_listener.anchor2_connected
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
                pose_ts = uwb_listener.updated_at  # UTC datetime of last UWB row
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
                if current_time - last_nav_write >= 0.10:
                    db_writer.write_nav_state(left_speed, right_speed)
                    last_nav_write = current_time

                if current_time - last_telemetry_write >= 0.10:
                    loop_time_ms  = (time.perf_counter() - loop_start) * 1000.0
                    distance_m    = effective_distance / 100.0

                    arrival_threshold_cm = 5.0
                    arrived = (
                        current_state == RoverState.AUTO
                        and abs(effective_distance - pid_dist.setpoint) <= arrival_threshold_cm
                    )

                    obstacle_hold = obstacle_hold_active

                    db_weight_kg = float(live_weight_kg)

                    print(
                        f"[WEIGHT_DEBUG] lc.weight_kg={db_weight_kg:.3f} "
                        f"live_weight_kg={live_weight_kg:.3f} "
                        f"hw={lc['hw_available']} "
                        f"tare_done={lc['tare_done']} "
                        f"err={lc['last_error']}",
                        file=sys.stderr,
                    )

                    db_writer.write_live_state(
                        mode=cmd_listener.mode if cmd_listener else "auto",
                        state=current_state.name,
                        weight_kg=db_weight_kg,
                        obstacle_hold=obstacle_hold,
                        arrived=arrived,
                        obstacle_avoid_active=obstacle_avoid_active,
                        luggage_fallen=luggage_fallen,
                        obstacle_reason=obstacle_reason or "",
                    )

                    print(
                        f"[ULTRA_DB] ema_front={ultra.get('front')} fast_front={ultra_fast.get('front')} "
                        f"ema_left={ultra.get('left')} fast_left={ultra_fast.get('left')} "
                        f"ema_right={ultra.get('right')} fast_right={ultra_fast.get('right')} "
                        f"ema_back={ultra.get('back')} fast_back={ultra_fast.get('back')}",
                        file=sys.stderr,
                    )

                    db_writer.write_telemetry(
                        distance_m,
                        db_weight_kg,
                        obstacle_hold,
                        False,
                        ultra=ultra_for_db,
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
                        obstacle_hold=obstacle_hold,
                        obstacle_avoid_active=obstacle_avoid_active,
                        ultra_for_db=ultra_for_db,
                        ultra=ultra,
                        ultra_fast=ultra_fast,
                    )

                    try:
                        verify = (
                            db_writer.supabase.table("rover_live_state")
                            .select("mode, state, weight_kg, obstacle_hold, obstacle_avoid_active, luggage_fallen, obstacle_reason, updated_at")
                            .eq("robot_id", ROBOT_ID)
                            .limit(1)
                            .execute()
                        )
                        print(f"[DB_VERIFY] rover_live_state={verify.data}", file=sys.stderr)
                    except Exception as e:
                        print(f"[DB_VERIFY] failed: {e}", file=sys.stderr)

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
                        "obstacle_hold": bool(obstacle_hold),
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
                        "anchor1_connected": uwb_listener.anchor1_connected if uwb_listener else None,
                        "anchor2_connected": uwb_listener.anchor2_connected if uwb_listener else None,
                    })

            # --- heartbeat  [old2.py] ---
            if heartbeat_enabled and heartbeat:
                loop_time_ms = (time.perf_counter() - loop_start) * 1000.0
                heartbeat.update(loop_time_ms, uwb_live, ultrasonic_enabled_local)

            # ==============================================================
            # GUI  [old2.py — system health dashboard with color-coded icons]
            # ==============================================================
            h, w = stdscr.getmaxyx()
            stdscr.clear()

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
            safe_addstr(stdscr, 17, 2,
                        f"Threshold: {avoid.threshold_cm} cm (adaptive)  "
                        f"Action: {(cmd_listener.obstacle_action if cmd_listener else 'avoid')}")

            if ultrasonic_enabled_local:
                def fmt(n):
                    v = ultra.get(n)
                    if v is None:
                        return "--"
                    if v < avoid.hard_stop_cm:
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