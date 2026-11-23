import socket, json, math, time
import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)
LED_PIN = 17  # True (ON) = RED, False (OFF) = GREEN
GPIO.setup(LED_PIN, GPIO.OUT)

RSU_ID = 'RSU-GROUP-01' # Replace with the Raspberry Pi hostname
RSU_X  = 400.0      # Change per group along horizontal X-axis: 400, 700, 1000, 1300...
RSU_Y  = 0.0
GEOFENCE_M = 200.0  # Activate green light within 200 m
SRM_TIMEOUT_S = 2.0 # SRM expires if no priority packet received for 2 s

vehicles = {}  # Indexed by vehicle_id -> stores latest 'cam' and 'srm'

def set_signal(phase):
    GPIO.output(LED_PIN, phase == 'RED')  # ON = RED, OFF = GREEN

set_signal('RED')
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('', 5000))
print(f"[{RSU_ID}] At (x={RSU_X}m, y={RSU_Y}) listening for separate CAM & SRM packets...")

try:
    while True:
        raw, _ = sock.recvfrom(1024)
        pkt = json.loads(raw.decode())
        vid = pkt['vehicle_id']
        mtype = pkt['msg_type']
        entry = vehicles.setdefault(vid, {'cam': None, 'srm_active': False, 'srm_ts': 0.0})

        if mtype == 'CAM':
            entry['cam'] = pkt
        elif mtype == 'SRM':
            entry['srm_active'] = pkt['siren_active'] and (pkt['requested_phase'] == 'GREEN')
            entry['srm_ts'] = time.time()

        # Evaluate preemption whenever CAM kinematics are available
        if entry['cam'] is not None:
            cam = entry['cam']
            dist = math.hypot(RSU_X - cam['x'], RSU_Y - cam['y'])
            approaching = ((RSU_X - cam['x']) * cam['heading']) > 0
            has_valid_srm = entry['srm_active'] and ((time.time() - entry['srm_ts']) <= SRM_TIMEOUT_S)

            if has_valid_srm and dist <= GEOFENCE_M and approaching:
                phase, status = 'GREEN', 'GRANTED (CAM+SRM VALID)'
            elif dist <= GEOFENCE_M and not approaching:
                phase, status = 'RED', 'IGNORED (PASSED / MOVING AWAY)'
            elif dist <= GEOFENCE_M and not has_valid_srm:
                phase, status = 'RED', 'NORMAL VEHICLE (CAM ONLY, NO SRM)'
            else:
                phase, status = 'RED', 'IDLE (OUT OF RANGE)'

            set_signal(phase)
            print(f"[{mtype:3s} | {vid}] x={cam['x']:6.1f}m | Dist={dist:5.1f}m | {status} -> {phase}")
finally:
    GPIO.cleanup()

