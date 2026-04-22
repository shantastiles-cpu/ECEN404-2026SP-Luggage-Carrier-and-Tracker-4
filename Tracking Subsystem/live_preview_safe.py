import sys
import time
import traceback

use_picamera2 = False
cap = None

# --- Try Picamera2 first ---
try:
    from picamera2 import Picamera2
    import numpy as np
    import cv2

    print("[INFO] Picamera2 available. Trying to configure camera...")

    cam = Picamera2()

    # Recommended: give a dict to create_preview_configuration
    preview_config = cam.create_preview_configuration({"main": {"size": (1280, 720)}})
    cam.configure(preview_config)

    print("[INFO] Starting Picamera2...")
    cam.start()
    use_picamera2 = True
    print("[INFO] Picamera2 started successfully.")
except Exception as e:
    print("[WARN] Picamera2 initialization failed.")
    traceback.print_exc()
    print()
    # We'll try OpenCV fallback below.

# --- Fallback to OpenCV V4L2 (/dev/video0) if Picamera2 failed ---
if not use_picamera2:
    try:
        import cv2
        print("[INFO] Attempting OpenCV VideoCapture(0) fallback (v4l2)...")
        cap = cv2.VideoCapture(0, cv2.CAP_V4L2)  # use V4L2 backend explicitly
        time.sleep(0.5)
        if not cap.isOpened():
            # try without explicit backend
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise RuntimeError("OpenCV could not open /dev/video0")
        print("[INFO] OpenCV VideoCapture opened /dev/video0 successfully.")
    except Exception as e:
        print("[ERROR] No camera available via Picamera2 or OpenCV.")
        traceback.print_exc()
        print("\nDiagnostic tips:")
        print(" - Run: libcamera-hello -t 3000")
        print(" - Check: ls /dev/video*")
        print(" - Ensure camera is enabled in: sudo raspi-config -> Interface Options -> Camera")
        sys.exit(1)

# --- Main loop: capture frames and show them ---
try:
    import cv2
    while True:
        if use_picamera2:
            # Picamera2 path (capture_array should return a usable numpy array)
            try:
                frame = cam.capture_array()
            except IndexError as idx_e:
                print("[ERROR] IndexError while calling cam.capture_array().")
                traceback.print_exc()
                print("This often means Picamera2 couldn't start a stream. Try libcamera-hello to test.")
                break
            except Exception:
                print("[ERROR] Unexpected exception while capturing with Picamera2.")
                traceback.print_exc()
                break
        else:
            # OpenCV path
            ret, frame = cap.read()
            if not ret or frame is None:
                print("[ERROR] OpenCV capture failed (no frame).")
                break

        # Show frame (resize so it fits nicely)
        h, w = frame.shape[:2]
        max_w = 1280
        if w > max_w:
            scale = max_w / w
            frame = cv2.resize(frame, (int(w*scale), int(h*scale)))

        cv2.imshow("Live Video", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("[INFO] Quit requested by user (q).")
            break

except KeyboardInterrupt:
    print("\n[INFO] Interrupted by user")

finally:
    print("[INFO] Cleaning up...")
    try:
        if use_picamera2:
            cam.stop()
    except Exception:
        pass
    try:
        if cap is not None:
            cap.release()
    except Exception:
        pass
    cv2.destroyAllWindows()
    print("[INFO] Exited.")