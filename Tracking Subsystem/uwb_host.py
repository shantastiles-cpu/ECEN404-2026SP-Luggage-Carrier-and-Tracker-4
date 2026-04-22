#!/usr/bin/env python3
"""
uwb_host.py — Raspberry Pi 5 UWB positioning host
==================================================
Reads JSON range frames directly from two anchor ESP32s over USB serial
and computes the tag's 2D (X, Y) position via circle-circle intersection.

Wall assumption: anchors mounted on wall at Y=0, tag always at Y >= 0.
Origin is the midpoint between the two anchors.

  A1 (-0.25, 0) ─── origin ─── A2 (+0.25, 0)

Calibration: set ANCHOR1_OFFSET_M and ANCHOR2_OFFSET_M below.
  offset = reported_distance - true_distance
  e.g. reports 3.0 m at 1.0 m true → offset = 2.0

Usage:
  python3 uwb_host.py [--a1x -0.5] [--a2x 0.5] [--log out.csv] [--debug]

Requirements:
  pip3 install pyserial
"""

import argparse
import csv
import glob
import json
import logging
import math
import sys
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

try:
    import serial
except ImportError:
    sys.exit("pyserial not found — run: pip3 install pyserial")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("uwb_host")

BAUD             = 115200
DETECT_TIMEOUT_S = 12


# ── Port detection ─────────────────────────────────────────────────────────────
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
                        log.info(f"  {port} → anchor {frame.get('anchor', '?')}")
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
    lock  = threading.Lock()

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


# ── Median filter ─────────────────────────────────────────────────────────────
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


# ── Per-anchor reader thread ──────────────────────────────────────────────────
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

    def get(self) -> Tuple[Optional[float], float]:
        with self._lock:
            return self.range_m, self.ts

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
                log.warning(f"[Anchor {self.anchor_num}] {e} — retrying in 2 s")
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

            smoothed = self._filter.update(rng)
            if not self._filter.ready():
                return

            with self._lock:
                self.range_m = smoothed
                self.ts      = time.monotonic()

            log.debug(f"[Anchor {self.anchor_num}] raw={rng:.4f} m  filtered={smoothed:.4f} m")

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.debug(f"[Anchor {self.anchor_num}] Parse error: {e}")


# ── Position solver — law of cosines ──────────────────────────────────────────
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
    cos_a = max(-1.0, min(1.0, cos_a))   # clamp for ranging noise
    sin_a = math.sqrt(1.0 - cos_a**2)

    cx = b * cos_a
    cy = b * sin_a

    # Shift origin to midpoint between anchors
    x = cx - c / 2.0
    y = cy

    return x, y


# ── CSV logger ────────────────────────────────────────────────────────────────
class CsvLogger:
    def __init__(self, path: str):
        self._f = open(path, "w", newline="")
        self._w = csv.writer(self._f)
        self._w.writerow(["timestamp_utc", "d1_m", "d2_m", "x_m", "y_m", "dist_mid_m", "angle_deg"])
        log.info(f"Logging to {path}")

    def write(self, d1, d2, x, y, dist_origin, angle_deg):
        self._w.writerow([datetime.utcnow().isoformat(),
                          f"{d1:.4f}", f"{d2:.4f}", f"{x:.4f}", f"{y:.4f}",
                          f"{dist_origin:.4f}", f"{angle_deg:.2f}"])
        self._f.flush()

    def close(self):
        self._f.close()


# ── Main ──────────────────────────────────────────────────────────────────────
def run(args):
    log.info("Detecting anchors — please wait…")
    anchor_ports = detect_anchor_ports()

    if len(anchor_ports) < 2:
        sys.exit(
            f"Only found {len(anchor_ports)} anchor(s) — need 2. "
            "Check both are powered and USB cables are connected."
        )

    port1, port2 = anchor_ports[0], anchor_ports[1]
    log.info(f"Anchor 1 → {port1}")
    log.info(f"Anchor 2 → {port2}")

    readers = [
        AnchorReader(1, port1, median_window=args.window),
        AnchorReader(2, port2, median_window=args.window),
    ]
    for r in readers:
        r.start()

    logger = CsvLogger(args.log) if args.log else None
    anchor_sep = abs(args.a2x - args.a1x)

    log.info(f"Anchor 1 pos=({args.a1x:.2f}, 0.00) m")
    log.info(f"Anchor 2 pos=({args.a2x:.2f}, 0.00) m")
    log.info(f"Anchor separation={anchor_sep:.2f} m")
    log.info("Positioning started — Ctrl+C to stop")

    try:
        while True:
            now     = time.monotonic()
            d1, ts1 = readers[0].get()
            d2, ts2 = readers[1].get()

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

            c   = abs(args.a2x - args.a1x)   # anchor separation
            pos = solve_position(d1, d2, c)
            if pos is None:
                time.sleep(0.05)
                continue

            x, y = pos
            angle_deg    = math.degrees(math.atan2(x, y))
            dist_mid     = math.hypot(x, y)   # distance from midpoint between anchors
            print(
                f"  A1={d1:6.3f} m  A2={d2:6.3f} m  →"
                f"  X={x:+7.3f} m  Y={y:+7.3f} m"
                f"  D_mid={dist_mid:6.3f} m  θ={angle_deg:+7.2f}°",
                flush=True,
            )

            if logger:
                logger.write(d1, d2, x, y, dist_mid, angle_deg)

            time.sleep(0.05)

    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        for r in readers:
            r.stop()
        if logger:
            logger.close()


def main():
    p = argparse.ArgumentParser(
        description="UWB 2-anchor positioning host",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--a1x",   default=-0.25, type=float, help="Anchor 1 X pos (m)")
    p.add_argument("--a2x",   default=0.25,  type=float, help="Anchor 2 X pos (m)")
    p.add_argument("--stale", default=3.0,  type=float, help="Stale reading timeout (s)")
    p.add_argument("--window",default=7,    type=int,   help="Median filter window size")
    p.add_argument("--log",   default=None, help="CSV log file path")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    run(args)


if __name__ == "__main__":
    main()
