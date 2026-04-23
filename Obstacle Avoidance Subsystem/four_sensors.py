# 4x HC-SR04 on Raspberry Pi (lgpio)
# Adaptive-speed EMA streaming + 3-hit debounce + smart avoidance
# BCM/phys pin map kept the same as you wired:
# front: TRIG=23 (16), ECHO=12 (32)
# right: TRIG=25 (22), ECHO=16 (36)
# left : TRIG=8  (24), ECHO=20 (38)
# back : TRIG=7  (26), ECHO=21 (40)

import time, statistics
import lgpio as GPIO

SENSORS = {
    "front": {"trig": 23, "echo": 12},
    "right": {"trig": 25, "echo": 16},
    "left":  {"trig": 8,  "echo": 20},
    "back":  {"trig": 7,  "echo": 21},
}

# ---------------- Tunables (FAST profile) ----------------
THRESHOLD_CM     = 61
HYSTERESIS_CM    = 10            # re-arm when EMA > 71 cm
DEBOUNCE_HITS    = 3             # need 3 consecutive EMA <= threshold
EMA_ALPHA        = 0.42          # snappier EMA (0.25 smoother, 0.5 very quick)

NEAR_CM          = 120           # use "near" settings when EMA <= this
SAMPLES_NEAR     = 7             # raw pings if near
SAMPLES_FAR      = 3             # raw pings if far
PING_GAP_NEAR    = 0.010         # gap between raw pings (near)
PING_GAP_FAR     = 0.006         # gap between raw pings (far)
INTER_SENSOR_GAP = 0.003         # quiet time between sensors

# Echo timeouts (far vs near) — front/back a bit longer
ECHO_TO_FAR  = {"front": 0.12, "back": 0.12, "right": 0.08, "left": 0.08}
ECHO_TO_NEAR = {"front": 0.08, "back": 0.08, "right": 0.06, "left": 0.06}

SPEED_CM_S       = 34300
TRIG_PULSE_S     = 12e-6
MIN_CM, MAX_CM   = 2, 400
MISSES_TO_BLANK  = 3             # show "--" quickly if data disappears
POST_EVENT_PAUSE = 0.05          # settle echoes after avoidance
CLEAR_MARGIN     = 8             # extra cm to call a direction "clear"
# ---------------------------------------------------------

def motor_forward(): print("[MOTOR] FORWARD")
def motor_stop():    print("[MOTOR] STOP")
def motor_reverse(t=0.35):
    print(f"[MOTOR] REVERSE {t:.2f}s"); time.sleep(t); print("[MOTOR] REVERSE done")
def motor_pivot_left(t=0.35):
    print(f"[MOTOR] PIVOT LEFT {t:.2f}s"); time.sleep(t); print("[MOTOR] PIVOT done")
def motor_pivot_right(t=0.35):
    print(f"[MOTOR] PIVOT RIGHT {t:.2f}s"); time.sleep(t); print("[MOTOR] PIVOT done")

def wait_for(h, pin, level, timeout):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if GPIO.gpio_read(h, pin) == level:
            return time.perf_counter()
    return None

def ping_once(h, trig, echo, to):
    GPIO.gpio_write(h, trig, 0); time.sleep(5e-6)
    GPIO.gpio_write(h, trig, 1); time.sleep(TRIG_PULSE_S)
    GPIO.gpio_write(h, trig, 0)
    t0 = wait_for(h, echo, 1, to)
    if t0 is None: return None
    t1 = wait_for(h, echo, 0, to)
    if t1 is None: return None
    d = (t1 - t0) * SPEED_CM_S / 2.0
    return d if (MIN_CM <= d <= MAX_CM) else None

def robust_measure_adaptive(h, name, trig, echo, ema_prev):
    """Adaptive sampler: fewer pings + shorter timeouts when far; more when near."""
    near = (ema_prev is not None) and (ema_prev <= NEAR_CM)
    n    = SAMPLES_NEAR if near else SAMPLES_FAR
    to   = ECHO_TO_NEAR.get(name, 0.08) if near else ECHO_TO_FAR.get(name, 0.08)
    gap  = PING_GAP_NEAR if near else PING_GAP_FAR

    vals = []
    for _ in range(n):
        d = ping_once(h, trig, echo, to)
        if d is not None:
            vals.append(d)
        time.sleep(gap)

    if not vals:
        return None

    med  = statistics.median(vals)
    devs = [abs(v - med) for v in vals]
    mad  = statistics.median(devs) if devs else 0
    keep = [v for v, dv in zip(vals, devs) if dv <= 2.5*mad] if mad > 0 else vals
    if not keep: keep = vals
    return statistics.median(keep)

def choose_avoid_action(trigger_name, ema):
    def d(n): v = ema.get(n); return v if v is not None else 0.0
    f, r, l, b = d("front"), d("right"), d("left"), d("back")
    clear_level = THRESHOLD_CM + CLEAR_MARGIN
    f_clear, r_clear, l_clear, b_clear = (f > clear_level, r > clear_level, l > clear_level, b > clear_level)
    print(f"[DECIDE] f={int(f)} r={int(r)} l={int(l)} b={int(b)} (clear > {int(clear_level)})")

    if trigger_name == "front":
        if b_clear: motor_stop(); motor_reverse(0.35); motor_forward(); return
        if r >= l and r > THRESHOLD_CM: motor_stop(); motor_pivot_right(0.35); motor_forward()
        elif l > r and l > THRESHOLD_CM: motor_stop(); motor_pivot_left(0.35); motor_forward()
        else: motor_stop(); motor_reverse(0.25); (motor_pivot_right(0.35) if r >= l else motor_pivot_left(0.35)); motor_forward()
    elif trigger_name == "back":
        if f_clear: motor_stop(); motor_forward(); return
        if r >= l and r > THRESHOLD_CM: motor_stop(); motor_pivot_right(0.35); motor_forward()
        elif l > r and l > THRESHOLD_CM: motor_stop(); motor_pivot_left(0.35); motor_forward()
        else: motor_stop(); motor_forward(); (motor_pivot_right(0.35) if r >= l else motor_pivot_left(0.35)); motor_forward()
    elif trigger_name == "left":
        if r_clear: motor_stop(); motor_pivot_right(0.25); motor_forward()
        elif f_clear: motor_stop(); motor_forward()
        elif b_clear: motor_stop(); motor_reverse(0.35); motor_forward()
        else: motor_stop(); motor_pivot_right(0.35); motor_forward()
    elif trigger_name == "right":
        if l_clear: motor_stop(); motor_pivot_left(0.25); motor_forward()
        elif f_clear: motor_stop(); motor_forward()
        elif b_clear: motor_stop(); motor_reverse(0.35); motor_forward()
        else: motor_stop(); motor_pivot_left(0.35); motor_forward()

def main():
    h = GPIO.gpiochip_open(0)

    # Claim pins
    for name, cfg in SENSORS.items():
        GPIO.gpio_claim_output(h, cfg["trig"]); GPIO.gpio_write(h, cfg["trig"], 0)
        GPIO.gpio_claim_input(h,  cfg["echo"])
        try: GPIO.gpio_set_pull_up_down(h, cfg["echo"], GPIO.LGPIO_PULL_DOWN)
        except Exception: pass

    print("HC-SR04: adaptive-speed EMA stream + 3-hit debounce + smart avoidance. Ctrl+C to exit.")
    motor_stop(); time.sleep(0.1); motor_forward()

    # Per-sensor state
    ema     = {n: None for n in SENSORS}
    hits    = {n: 0    for n in SENSORS}
    latched = {n: False for n in SENSORS}
    misses  = {n: 0    for n in SENSORS}

    try:
        while True:
            for name, cfg in SENSORS.items():
                # Adaptive measurement uses last EMA as a hint
                d = robust_measure_adaptive(h, name, cfg["trig"], cfg["echo"], ema[name])

                if d is not None:
                    misses[name] = 0
                    ema[name] = d if ema[name] is None else (EMA_ALPHA*d + (1-EMA_ALPHA)*ema[name])
                else:
                    misses[name] += 1
                    if misses[name] >= MISSES_TO_BLANK:
                        ema[name] = None  # blank quickly if data missing

                sd = ema[name]
                if sd is None:
                    hits[name] = 0
                else:
                    if sd <= THRESHOLD_CM:
                        hits[name] += 1
                    elif sd > THRESHOLD_CM + 3:
                        hits[name] = 0

                    if (not latched[name]) and (hits[name] >= DEBOUNCE_HITS):
                        latched[name] = True
                        hits[name] = 0
                        print(f"[EVENT] {name.upper()} <= {THRESHOLD_CM} cm -> avoid")
                        choose_avoid_action(name, ema)
                        # After action, force reacquire fresh (prevents stickiness)
                        ema[name] = None
                        misses[name] = MISSES_TO_BLANK
                        time.sleep(POST_EVENT_PAUSE)

                    if latched[name] and (sd > THRESHOLD_CM + HYSTERESIS_CM):
                        latched[name] = False

                time.sleep(INTER_SENSOR_GAP)

            # Single-line EMA distances
            print(" | ".join(
                f"{n.capitalize()}: {('--' if ema[n] is None else f'{int(ema[n])} cm')}"
                for n in ["front","right","left","back"]
            ))

    except KeyboardInterrupt:
        pass
    finally:
        motor_stop()
        GPIO.gpiochip_close(h)
        print("GPIO closed. Done.")

if __name__ == "__main__":
    main()
