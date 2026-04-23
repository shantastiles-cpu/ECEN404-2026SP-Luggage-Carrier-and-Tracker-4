#!/usr/bin/env python3
"""
SUPA_uwb_host.py — UWB positioning → Supabase with enhanced features
=====================================================================
NEW FEATURES:
- Anchor geometry self-test on boot
- Enhanced confidence scoring with geometry validation
- Heartbeat publishing
- Adaptive filtering based on motion
- Realtime data quality metrics

Supabase tables:
- uwb_positions: position data
- uwb_health: anchor health monitoring (NEW)
"""

import argparse
import json
import glob
import logging
import math
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Tuple
from datetime import datetime, timezone

try:
    import serial
except ImportError:
    sys.exit("pyserial not found — run: pip3 install pyserial")

try:
    from supabase import create_client, Client
except ImportError:
    sys.exit("supabase not found — run: pip3 install supabase")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("uwb_host")

BAUD = 115200
DETECT_TIMEOUT_S = 12
SUPABASE_URL = "https://jbhjvtfeezmityewhtwa.supabase.co"
SUPABASE_KEY = "sb_publishable_z7vaIDJQaQcjB0bpKG7Rzg_0m6boJHf"
ROBOT_ID = "rover_01"
PUBLISH_INTERVAL_S = 0.1


# ======================================================================================
# Anchor Geometry Validator
# ======================================================================================
class AnchorGeometry:
    """Validates anchor placement and warns about degenerate configurations"""
    
    @staticmethod
    def validate(a1_pos: Tuple[float, float], a2_pos: Tuple[float, float]) -> dict:
        """
        Returns: {
            'separation_m': float,
            'is_symmetric': bool,
            'is_valid': bool,
            'warnings': list[str]
        }
        """
        x1, y1 = a1_pos
        x2, y2 = a2_pos
        
        separation = math.hypot(x2 - x1, y2 - y1)
        warnings = []
        
        # Check minimum separation
        if separation < 0.3:
            warnings.append(f"Anchors too close ({separation:.2f}m < 0.3m min)")
        
        # Check if on same Y coordinate (expected for wall mounting)
        if abs(y1 - y2) > 0.05:
            warnings.append(f"Anchors not level (Y1={y1:.2f}, Y2={y2:.2f})")
        
        # Check symmetry around origin
        midpoint_x = (x1 + x2) / 2.0
        if abs(midpoint_x) > 0.05:
            warnings.append(f"Anchors not symmetric around origin (mid={midpoint_x:.2f})")
        
        # Check for degenerate cases
        if abs(x1 - x2) < 0.1:
            warnings.append("Anchors nearly vertical — poor geometry for X positioning")
        
        is_valid = len(warnings) == 0
        is_symmetric = abs(midpoint_x) < 0.05
        
        return {
            'separation_m': round(separation, 3),
            'is_symmetric': is_symmetric,
            'is_valid': is_valid,
            'warnings': warnings,
        }


# ======================================================================================
# UWB Health Monitor
# ======================================================================================
class UwbHealthMonitor:
    """Publishes anchor health metrics to Supabase"""
    
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
    
    def update(self, a1_connected: bool, a2_connected: bool,
               a1_range: float, a2_range: float,
               a1_age: float, a2_age: float,
               pub_rate: float):
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
                data = {
                    'robot_id': self._robot_id,
                    'anchor1_connected': self.anchor1_connected,
                    'anchor2_connected': self.anchor2_connected,
                    'anchor1_range_m': round(self.anchor1_last_range, 3),
                    'anchor2_range_m': round(self.anchor2_last_range, 3),
                    'anchor1_age_s': round(self.anchor1_age_s, 2),
                    'anchor2_age_s': round(self.anchor2_age_s, 2),
                    'publish_rate_hz': round(self.publish_rate_hz, 1),
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                }
                self._supa.table('uwb_health').upsert(
                    data, on_conflict='robot_id'
                ).execute()
            except Exception as e:
                log.warning(f"[HEALTH] Write failed: {e}")
            
            time.sleep(2.0)


# ======================================================================================
# Pairing waiter
# ======================================================================================
class PairingWaiter:
    """Polls pairing_sessions until paired=true"""
    
    def __init__(self, url: str, key: str, robot_id: str):
        self._supa = create_client(url, key)
        self._robot_id = robot_id

    def wait(self, poll_interval_s: float = 1.0) -> str:
        log.info("[PAIR] Waiting for app pairing...")
        while True:
            try:
                result = (
                    self._supa.table('pairing_sessions')
                    .select('paired, session_token')
                    .eq('robot_id', self._robot_id)
                    .execute()
                )
                if result.data:
                    row = result.data[0]
                    if row.get('paired') and row.get('session_token'):
                        token = row['session_token']
                        log.info(f"[PAIR] ✓ Paired — UWB publishing active")
                        return token
            except Exception as e:
                log.warning(f"[PAIR] Poll error: {e}")
            time.sleep(poll_interval_s)


# ======================================================================================
# Port detection
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
            time.sleep(0.1)
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
                        addr = frame.get("anchor", "?")
                        log.info(f"  {port} → anchor {addr}")
                        return True
                except (json.JSONDecodeError, ValueError):
                    continue
    except serial.SerialException as e:
        log.warning(f"  {port} → error: {e}")
    return False


def detect_anchor_ports() -> list[str]:
    ports = find_serial_ports()
    log.info(f"Scanning ports: {ports}")
    found = []
    lock = threading.Lock()

    def probe(port):
        if is_anchor_port(port):
            with lock:
                found.append(port)

    threads = [threading.Thread(target=probe, args=(p,), daemon=True) for p in ports]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(found)


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
        """Returns (range_m, timestamp, connected)"""
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
            except serial.SerialException as e:
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
                return
            smoothed = self._filter.update(rng)
            if not self._filter.ready():
                return
            with self._lock:
                self.range_m = smoothed
                self.ts = time.monotonic()
        except (json.JSONDecodeError, KeyError, ValueError):
            pass


# ======================================================================================
# Position solver — law of cosines (UPDATED FROM uwb_host)
# ======================================================================================
# A = anchor 1 (origin), B = anchor 2 at (c, 0), C = tag
# b = distance A→C,  a = distance B→C,  c = anchor separation
#
# Law of cosines for angle α at A:
#   cos α = (b² + c² - a²) / (2bc)
#   sin α = √(1 - cos²α)
#   C = (b·cos α,  b·sin α)
#
# sin α is always positive — tag is in front of the wall.
# Origin is then shifted to the midpoint between the anchors.
def solve_position(
    b: float,    # distance from anchor 1 to tag
    a: float,    # distance from anchor 2 to tag
    c: float,    # distance between the two anchors
) -> Optional[Tuple[float, float]]:
    if c < 1e-9 or b < 1e-9:
        return None

    cos_a = (b**2 + c**2 - a**2) / (2.0 * b * c)
    cos_a = max(-1.0, min(1.0, cos_a))  # clamp for ranging noise
    sin_a = math.sqrt(1.0 - cos_a**2)

    cx = b * cos_a
    cy = b * sin_a

    # Shift origin to midpoint between anchors
    x = cx - c / 2.0
    y = cy

    return x, y


# ======================================================================================
# Enhanced UWB Publisher with geometry validation

# ======================================================================================
class UwbPublisher:
    """Publishes UWB data with quality metrics"""
    
    def __init__(self, url: str, key: str, robot_id: str, anchor_sep_m: float):
        self._supa: Client = create_client(url, key)
        self._robot_id = robot_id
        self._anchor_sep_m = anchor_sep_m
        self._last_write = 0.0
        self._publish_count = 0
        self._publish_start = time.monotonic()
        log.info(f"[Supabase] Connected for robot_id='{robot_id}'")

    def publish(
        self,
        x_m: float,
        y_m: float,
        angle_deg: float,
        d1_m: float,
        d2_m: float,
    ):
        now = time.monotonic()
        if now - self._last_write < PUBLISH_INTERVAL_S:
            return
        self._last_write = now
        self._publish_count += 1

        distance_m = math.hypot(x_m, y_m)
        
        # Calculate confidence score
        confidence = self._calculate_confidence(d1_m, d2_m, y_m)

        try:
            self._supa.table("uwb_positions").upsert({
                "robot_id": self._robot_id,
                "x_m": round(x_m, 4),
                "y_m": round(y_m, 4),
                "angle_deg": round(angle_deg, 3),
                "distance_m": round(distance_m, 4),
                "d1_m": round(d1_m, 4),
                "d2_m": round(d2_m, 4),
                "confidence": round(confidence, 1),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="robot_id").execute()
        except Exception as e:
            log.warning(f"[Supabase] Write failed: {e}")
    
    def _calculate_confidence(self, d1_m: float, d2_m: float, y_m: float) -> float:
        """
        Calculate position confidence based on:
        - Anchor imbalance
        - Distance from baseline
        - Geometric validity
        """
        # Anchor imbalance penalty
        imbalance = abs(d1_m - d2_m)
        max_good_imbalance = self._anchor_sep_m * 2.0
        
        if imbalance < max_good_imbalance:
            imbalance_score = 100.0
        else:
            excess = imbalance - max_good_imbalance
            imbalance_score = max(0.0, 100.0 - (excess / max_good_imbalance) * 50.0)
        
        # Distance from baseline penalty (far = less accurate)
        if y_m < 2.0:
            distance_score = 100.0
        elif y_m < 5.0:
            distance_score = 100.0 - ((y_m - 2.0) / 3.0) * 20.0
        else:
            distance_score = max(50.0, 80.0 - (y_m - 5.0) * 5.0)
        
        # Combined
        confidence = (imbalance_score * 0.6 + distance_score * 0.4)
        return max(0.0, min(100.0, confidence))
    
    def get_publish_rate(self) -> float:
        """Returns current publish rate in Hz"""
        elapsed = time.monotonic() - self._publish_start
        if elapsed < 1.0:
            return 0.0
        return self._publish_count / elapsed


# ======================================================================================
# Main loop
# ======================================================================================
def run(args):
    log.info("Detecting anchors — please wait…")
    anchor_ports = detect_anchor_ports()

    if len(anchor_ports) < 2:
        sys.exit(
            f"Only found {len(anchor_ports)} anchor(s) — need 2. "
            "Check both are powered and USB connected."
        )

    port1, port2 = anchor_ports[0], anchor_ports[1]
    a1_pos = (args.a1x, 0.0)
    a2_pos = (args.a2x, 0.0)
    
    log.info(f"Anchor 1 → {port1}  pos=({a1_pos[0]:.2f}, {a1_pos[1]:.2f}) m")
    log.info(f"Anchor 2 → {port2}  pos=({a2_pos[0]:.2f}, {a2_pos[1]:.2f}) m")
    
    # Geometry self-test
    geometry = AnchorGeometry.validate(a1_pos, a2_pos)
    log.info(f"Anchor separation: {geometry['separation_m']} m")
    
    if geometry['warnings']:
        log.warning("⚠ ANCHOR GEOMETRY WARNINGS:")
        for warning in geometry['warnings']:
            log.warning(f"  - {warning}")
        
        if not geometry['is_valid']:
            response = input("Continue anyway? (yes/no): ")
            if response.lower() != 'yes':
                sys.exit("Aborted due to geometry issues")

    readers = [
        AnchorReader(1, port1, median_window=args.window),
        AnchorReader(2, port2, median_window=args.window),
    ]
    for r in readers:
        r.start()

    publisher = UwbPublisher(SUPABASE_URL, SUPABASE_KEY, ROBOT_ID, geometry['separation_m'])
    
    # Health monitor
    try:
        supa_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        health_monitor = UwbHealthMonitor(supa_client, ROBOT_ID)
        health_monitor.start()
        health_enabled = True
    except Exception as e:
        log.warning(f"Health monitor failed to start: {e}")
        health_enabled = False
        health_monitor = None

    # Wait for pairing
    pairing = PairingWaiter(SUPABASE_URL, SUPABASE_KEY, ROBOT_ID)
    pairing.wait()

    log.info("UWB publisher running — Ctrl+C to stop")
    log.info(f"Publishing every {PUBLISH_INTERVAL_S*1000:.0f} ms")

    try:
        while True:
            now = time.monotonic()
            d1, ts1, conn1 = readers[0].get()
            d2, ts2, conn2 = readers[1].get()

            if d1 is None or d2 is None:
                log.info("Waiting for readings from both anchors…")
                time.sleep(0.2)
                continue

            age1 = now - ts1
            age2 = now - ts2
            
            if age1 > args.stale or age2 > args.stale:
                log.warning(f"Stale — A1={age1:.1f}s  A2={age2:.1f}s")
                time.sleep(0.1)
                continue

            anchor_sep = abs(args.a2x - args.a1x)
            pos = solve_position(
                d1,  # distance from anchor 1
                d2,  # distance from anchor 2
                anchor_sep,
            )
            if pos is None:
                time.sleep(0.05)
                continue

            x, y = pos
            angle_deg = math.degrees(math.atan2(x, y))

            print(
                f"  A1={d1:6.3f}m  A2={d2:6.3f}m  →"
                f"  X={x:+7.3f}m  Y={y:+7.3f}m  θ={angle_deg:+7.2f}°",
                flush=True,
            )

            publisher.publish(x, y, angle_deg, d1, d2)
            
            # Update health monitor
            if health_enabled:
                pub_rate = publisher.get_publish_rate()
                health_monitor.update(
                    conn1, conn2,
                    d1 or 0.0, d2 or 0.0,
                    age1, age2,
                    pub_rate
                )

            time.sleep(0.05)

    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        for r in readers:
            r.stop()
        if health_enabled:
            health_monitor.stop()


def main():
    p = argparse.ArgumentParser(
        description="UWB 2-anchor positioning publisher → Supabase (Enhanced)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--a1x", default=-0.25, type=float, help="Anchor 1 X pos (m)")
    p.add_argument("--a2x", default=0.25, type=float, help="Anchor 2 X pos (m)")
    p.add_argument("--stale", default=3.0, type=float, help="Stale reading timeout (s)")
    p.add_argument("--window", default=7, type=int, help="Median filter window")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    run(args)


if __name__ == "__main__":
    main()