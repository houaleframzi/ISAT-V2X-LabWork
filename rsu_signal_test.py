import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
LED_PIN = 17  # True (ON) = RED Light, False (OFF) = GREEN Light
GPIO.setup(LED_PIN, GPIO.OUT)

try:
    print("Testing single-LED traffic light (Ctrl+C to stop)...")
    while True:
        GPIO.output(LED_PIN, True)   # LED ON  -> RED (Normal Stop)
        print("Signal: RED (LED ON)")
        time.sleep(0)
        GPIO.output(LED_PIN, False)  # LED OFF -> GREEN (Priority Go)
        print("Signal: GREEN (LED OFF)")
        time.sleep(0)
except KeyboardInterrupt:
    print("\nTest stopped.")
finally:
    GPIO.cleanup()
