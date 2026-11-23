import socket, json, time

BROADCAST_IP = '255.255.255.255'  # Or lab subnet broadcast e.g. '192.168.1.255' #or the RSU IP
PORT = 5000
VEHICLE_ID = 'AMB-xx' # Replace xx with your group number
SEND_SRM = True    # Set False to test a normal car (CAM only -> light stays RED!)

x = 0.0            # Start at x = 0 m on horizontal avenue
y = 0.0
heading = 1        # +1 = Driving East (+x), -1 = Driving West (-x)
speed_mps = 25.0   # 90 km/h
dt = 0.5           # 2 Hz broadcast rate

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

print(f"[OBU] Sending separate CAM & SRM packets for {VEHICLE_ID} along y=0...")
while 0.0 <= x <= 1500.0:
    # 1. Broadcast CAM packet (Kinematics only)
    cam_pkt = {
        'msg_type': 'CAM',
        'vehicle_id': VEHICLE_ID,
        'x': round(x, 1),
        'y': round(y, 1),
        'heading': heading,
        'speed_kmh': round(speed_mps * 3.6, 1)
    }
    sock.sendto(json.dumps(cam_pkt).encode(), (BROADCAST_IP, PORT))

    # 2. Broadcast separate SRM packet (Priority Request only when siren active)
    if SEND_SRM:
        srm_pkt = {
            'msg_type': 'SRM',
            'vehicle_id': VEHICLE_ID,
            'role': 'EMERGENCY',
            'siren_active': True,
            'requested_phase': 'GREEN'
        }
        sock.sendto(json.dumps(srm_pkt).encode(), (BROADCAST_IP, PORT))

    print(f"Sent CAM (x={cam_pkt['x']}m, h={heading:+d})" + (" + SRM (GREEN)" if SEND_SRM else " [No SRM]"))
    x += heading * speed_mps * dt
    time.sleep(dt)

