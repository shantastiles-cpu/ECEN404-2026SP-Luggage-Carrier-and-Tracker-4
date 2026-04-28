# 📡 UWB Tracking Subsystem

> Real-time 2D user positioning for the Luggage Carrier Rover using Ultra-Wideband ranging, trilateration, and EMA filtering.

---

## Table of Contents

- [Overview](#overview)
- [Hardware](#hardware)
- [Coordinate System](#coordinate-system)
- [Firmware](#firmware)
  - [Anchor Firmware](#anchor-firmware)
  - [Tag Firmware](#tag-firmware)
  - [Antenna Delay Calibration](#antenna-delay-calibration)
- [Software Pipeline](#software-pipeline)
  - [Stage 1 — Port Detection](#stage-1--port-detection)
  - [Stage 2 — Serial Ingestion](#stage-2--serial-ingestion)
  - [Stage 3 — Range Filtering](#stage-3--range-filtering)
  - [Stage 4 — Position Solving](#stage-4--position-solving)
  - [Stage 5 — EMA Smoothing](#stage-5--ema-smoothing)
  - [Stage 6 — Angle and Distance Computation](#stage-6--angle-and-distance-computation)
  - [Stage 7 — Control Distance Rejection](#stage-7--control-distance-rejection)
  - [Stage 8 — Quality Gating](#stage-8--quality-gating)
  - [Stage 9 — Confidence Scoring](#stage-9--confidence-scoring)
- [Configuration](#configuration)
- [Pose Output](#pose-output)
- [Known Limitations](#known-limitations)
- [Dependencies](#dependencies)
- [File Structure](#file-structure)

---

## Overview

The UWB tracking subsystem provides real-time 2D position of a user-carried tag relative to two anchors mounted on the rover. Each anchor independently measures its distance to the tag and reports the result to the Raspberry Pi 5 over USB serial. The Pi solves the tag's position using trilateration and passes distance, heading angle, and confidence to the navigation controller.

---

## Hardware

| Component | Details |
|---|---|
| Anchors | 2× Makerfabs ESP32 UWB (DW1000), USB → Raspberry Pi 5 |
| Tag | 1× Makerfabs ESP32 UWB (DW1000), carried by user |
| Host | Raspberry Pi 5 running `NAVIAPP_MattsUpdates4.py` |
| Anchor separation | 0.65 m (±0.325 m from rover centreline) |
| Ranging mode | `MODE_LONGDATA_RANGE_LOWPOWER` |
| Serial baud rate | 115200 |

---

## Coordinate System

```
             Tag (x, y)
                •
                |
         y      |       ← Y = depth in front of rover
         ↑      |
         |      |
A1 ──────┼──────────── A2      ← anchor wall  (Y = 0)
    (-0.325, 0)  →x  (0.325, 0)
              origin (0, 0)
```

| Axis | Description |
|---|---|
| **X** | Lateral offset from anchor midpoint. Negative = left, positive = right |
| **Y** | Depth in front of the anchor wall. Always positive in normal operation |
| **Distance** | Straight-line distance from rover reference point `(REF_OFFSET_X, REF_OFFSET_Y)` to tag |
| **Angle** | Heading from rover reference point to tag, measured from forward axis. Negative = left, positive = right |

---

## Firmware

### Anchor Firmware

> Files: `anchor1/anchor1.ino`, `anchor2/anchor2.ino`

Each anchor has a unique EUI-64 address and performs DS-TWR (Double-Sided Two-Way Ranging) with the tag. On each completed ranging exchange it streams a JSON frame over USB serial:

```json
{"anchor":"0100","range":1.2340,"dbm":-82.1}
```

On boot it emits an identification message used for auto-detection:

```json
{"event":"boot","anchor":"1"}
```

### Tag Firmware

> File: `tag/tag.ino`

The tag uses the library default antenna delay of `16384` and participates passively in DS-TWR ranging. It does **not** compute positions or transmit data to the Pi — all computation happens on the Pi side.

### Antenna Delay Calibration

The `ANTENNA_DELAY` constant in each anchor compensates for the board's physical hardware propagation delay. To calibrate:

1. Place the tag exactly **1.000 m** from the anchor with clear line of sight
2. Note the reported distance (`measured_m`)
3. Apply the correction formula:

```
new_delay = current_delay + round((measured_m − 1.000) / 0.0046)
```

4. Update `ANTENNA_DELAY` in the anchor firmware and reflash
5. Repeat until the reading is within a few centimetres of 1.000 m

> **Note:** The tag always uses `16384`. Only the anchors are calibrated.  
> **Important:** Calibrate with both anchors mounted on the rover and active simultaneously — the ranging environment affects the result.

Alternatively, flash `anchor_calibrate/anchor_calibrate.ino` which runs a binary search to converge on the correct delay automatically and prints the final value to Serial Monitor.

---

## Software Pipeline

All tracking logic lives in `LocalUwbTracker` inside `NAVIAPP_MattsUpdates4.py`.

```
USB Serial                     Pi Host
───────────────────────────────────────────────────────────────
Anchor 1 ──► AnchorReader 1 ──► bounds check
                                 height compensation           Stage 3
                                 median filter (window=3)
                                        │
Anchor 2 ──► AnchorReader 2 ──►        ▼
             (same filters)    solve_position()                Stage 4
                                 law of cosines → (x, y)
                                        │
                                        ▼
                                 EMA smoothing                 Stage 5
                                 alpha = 0.25
                                        │
                                        ▼
                               angle_deg, distance_m           Stage 6
                               from reference point
                                        │
                                        ▼
                               control drop rejection          Stage 7
                                        │
                                        ▼
                               is_pose_good() gate             Stage 8
                                        │
                                        ▼
                               confidence score                Stage 9
                                        │
                                        ▼
                               → Navigation Controller
```

---

### Stage 1 — Port Detection

At startup, `detect_anchor_ports()` probes USB ports **sequentially** with a 0.5 s gap between each. This avoids a DTR reset race condition where both ESP32s boot simultaneously and interfere with each other's UWB ranging. Each port is toggled via DTR to trigger an ESP32 reset and the boot message is read to confirm anchor presence.

---

### Stage 2 — Serial Ingestion

Two `AnchorReader` threads run in parallel, one per USB port. Each thread reads JSON frames continuously at 115200 baud. The anchor is marked `connected = True` on each valid reading and `connected = False` if no data is received for 3 seconds.

---

### Stage 3 — Range Filtering

Each raw range reading passes three operations in `AnchorReader._parse()`:

#### Bounds Check
```python
if rng < 0.05 or rng > 20.0:
    discard
```

#### Height Compensation

The DW1000 measures 3D slant range. If anchors and tag are at different heights, this is projected to the horizontal floor plane:

$$d_{horizontal} = \sqrt{range^2 - height\_diff^2}$$

where `height_diff = |ANCHOR_HEIGHT_M − TAG_HEIGHT_M|`.

If the slant range is shorter than the height difference (physically impossible noise floor), the reading is clamped to `0.01 m` rather than discarded.

#### Median Filter

A rolling window median filter (`window = 3`) suppresses impulsive multipath noise. Waits for at least 2 samples before outputting.

---

### Stage 4 — Position Solving

The two filtered distances `d1` (A1 → tag) and `d2` (A2 → tag), plus anchor separation `c`, form a triangle. The tag's 2D position is solved using the **law of cosines**:

$$\cos\alpha = \frac{d_1^2 + c^2 - d_2^2}{2 \cdot d_1 \cdot c}$$

$$\sin\alpha = \sqrt{1 - \cos^2\alpha}$$

$$x = d_1 \cdot \cos\alpha - \frac{c}{2}, \quad y = d_1 \cdot \sin\alpha$$

The `c/2` term shifts the origin from A1 to the anchor midpoint. `cos α` is clamped to `[−1, 1]` to absorb small ranging inconsistencies.

---

### Stage 5 — EMA Smoothing

An Exponential Moving Average (`EMA_ALPHA = 0.25`) is applied to raw `x` and `y` before computing angle and distance:

```python
ema_x = 0.25 * raw_x + 0.75 * ema_x
ema_y = 0.25 * raw_y + 0.75 * ema_y
```

Each update blends 25% new reading with 75% history, dampening high-frequency jitter from ranging inconsistencies.

| `EMA_ALPHA` | Behaviour |
|---|---|
| `0.1` | Very smooth, ~700 ms lag |
| `0.25` | Balanced — current setting, ~300–400 ms lag |
| `0.5` | Responsive, some residual jitter |
| `0.8` | Nearly raw output |

---

### Stage 6 — Angle and Distance Computation

From EMA-smoothed `x` and `y`, outputs are computed relative to the **rover reference point** `(REF_OFFSET_X, REF_OFFSET_Y)`:

```python
angle_deg          = atan2(x − REF_OFFSET_X,  y − REF_OFFSET_Y)
distance_control_m = hypot(x − REF_OFFSET_X,  y − REF_OFFSET_Y)
distance_display_m = hypot(x, y)
```

Using the reference point offset ensures `angle = 0°` aligns with the physical forward direction of the rover chassis rather than the anchor wall midpoint.

---

### Stage 7 — Control Distance Rejection

A secondary gate rejects implausible sudden distance drops that could cause an uncontrolled forward charge:

```python
if distance_control_m < MIN_VALID_CONTROL_M:       # 0.50 m
    if last_good − current > MAX_CONTROL_DROP_M:   # 0.35 m
        reject frame, hold last known good value
```

---

### Stage 8 — Quality Gating

Before the pose is passed to the follow controller, all four conditions must pass:

| Condition | Threshold |
|---|---|
| Reading age | < `stale_s` = 1.5 s |
| Anchor 1 connected | `True` |
| Anchor 2 connected | `True` |
| Confidence score | ≥ `min_conf` = 30% |

If any condition fails the rover holds position or transitions to `LOST_UWB` state.

---

### Stage 9 — Confidence Scoring

A 0–100 confidence score is computed from two components:

```
confidence = imbalance_score × 0.6 + distance_score × 0.4
```

- **Imbalance score** — penalises large differences between `d1` and `d2` (tag far off-centre)
- **Distance score** — penalises large Y values (ranging noise has greater angular effect at distance)

---

## Configuration

All tuning constants are defined near the top of `NAVIAPP_MattsUpdates4.py`:

| Constant | Default | Description |
|---|---|---|
| `ANCHOR_HEIGHT_M` | `0.29` | Anchor height above floor (m) |
| `TAG_HEIGHT_M` | `0.89` | Tag height above floor (m) |
| `REF_OFFSET_X` | `0.0` | Reference point X offset from anchor midpoint (m) |
| `REF_OFFSET_Y` | `0.15` | Reference point Y offset — distance in front of anchor wall (m) |
| `MIN_VALID_CONTROL_M` | `0.50` | Minimum believable control distance (m) |
| `MAX_CONTROL_DROP_M` | `0.35` | Maximum believable single-frame distance drop (m) |
| `EMA_ALPHA` | `0.25` | EMA smoothing factor |
| `median_window` | `3` | Median filter window size per anchor |
| `stale_s` | `1.5` | Maximum reading age before pose is stale (s) |
| `min_conf` | `30.0` | Minimum confidence score to act on pose (%) |

---

## Pose Output

`LocalUwbTracker.get()` returns:

```python
{
    "distance_cm":         float,   # control distance from rover reference point (cm)
    "distance_display_cm": float,   # display distance from anchor midpoint (cm)
    "angle_deg":           float,   # heading — negative = left, positive = right
    "confidence":          float,   # quality score 0–100
    "age_s":               float,   # seconds since last valid update
    "a1_connected":        bool,    # anchor 1 connection status
    "a2_connected":        bool,    # anchor 2 connection status
    "a1_age_s":            float,   # seconds since anchor 1 last reading
    "a2_age_s":            float,   # seconds since anchor 2 last reading
    "last_error":          str | None,
}
```

---

## Known Limitations

| Limitation | Detail |
|---|---|
| **Front/behind ambiguity** | With two anchors, `sin α ≥ 0` always — the system cannot distinguish front from behind. Assumes tag is always in front. A BNO055 compass would resolve this. |
| **Near-wall instability** | When tag is very close to anchor wall, small ranging errors produce large angular swings. Avoid operating below ~0.5 m depth. |
| **Multipath reflections** | Median window of 3 suppresses most spikes. Increasing to 5–7 improves rejection at the cost of additional lag. |
| **Antenna delay drift** | Effective delay shifts with temperature and two-anchor operation. Calibrate with both anchors mounted and active on the rover. |

---

## Dependencies

**Python (Pi host):**
```bash
pip3 install pyserial supabase
```

**Arduino (firmware):**
- `DW1000` by Thomas Trojer — install via Arduino Library Manager

---

## File Structure

```
anchor1/
└── anchor1.ino              # Anchor 1 firmware  (EUI-64: 01:00:5B:D5:A9:9A:E2:9C)
anchor2/
└── anchor2.ino              # Anchor 2 firmware  (EUI-64: 02:00:5B:D5:A9:9A:E2:9C)
tag/
└── tag.ino                  # Tag firmware       (antenna delay: 16384)
anchor_calibrate/
└── anchor_calibrate.ino     # Binary search antenna delay calibration sketch
NAVIAPP_MattsUpdates4.py     # Main application — contains LocalUwbTracker
```

---

*Tracking subsystem — Luggage Carrier and Tracker 4 | Team 37 | Texas A&M ECEN 404*
