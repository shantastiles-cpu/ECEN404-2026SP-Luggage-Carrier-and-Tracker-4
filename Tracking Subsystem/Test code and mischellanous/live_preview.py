from picamera2 import Picamera2
import cv2

# Initialize camera
picam2 = Picamera2()

# Use the IMX477 native resolution or scale down for speed
config = picam2.create_preview_configuration(main={"size": (1280, 720)})
picam2.configure(config)
picam2.start()

print("✅ IMX477 Live Stream started. Press 'q' to quit.")

while True:
    frame = picam2.capture_array()
    cv2.imshow("IMX477 Live Video", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

picam2.stop()
cv2.destroyAllWindows()
