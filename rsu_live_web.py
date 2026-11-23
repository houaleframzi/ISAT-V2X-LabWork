from flask import Flask, jsonify
import socket, json, math, threading, time
import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)
LED_PIN = 17  # True (ON) = RED, False (OFF) = GREEN
GPIO.setwarnings(False)
GPIO.setup(LED_PIN, GPIO.OUT)

RSU_ID = 'RSU-GROUP-01'
RSU_X, RSU_Y = 400.0, 0.0
GEOFENCE_M = 200.0
SRM_TIMEOUT_S = 2.0
VEHICLE_STALE_S = 4.0

# User-configurable minimum hold duration (seconds) for manual RED / GREEN overrides
OVERRIDE_HOLD_S = 2.0  # Keep at least 2.0 seconds

vehicles = {}
state_lock = threading.Lock()
override_state = {'phase': None, 'until_ts': 0.0}
live_telemetry = {
    'rsu_id': RSU_ID, 'rsu_x': RSU_X, 'geofence_m': GEOFENCE_M,
    'override_hold_s': OVERRIDE_HOLD_S, 'override_remaining_s': 0.0,
    'vehicle_count': 0, 'multi_vehicle': False, 'vehicles': [],
    'status': 'IDLE (WAITING FOR CAM/SRM)', 'phase': 'RED',
    'cam_count': 0, 'srm_count': 0
}

def set_signal(phase):
    GPIO.output(LED_PIN, phase == 'RED')  # ON = RED, OFF = GREEN

def evaluate_corridor_locked(now):
    active_list = []
    any_green_grant = False
    for vid, entry in list(vehicles.items()):
        if entry['cam'] is None or (now - entry['last_seen']) > VEHICLE_STALE_S:
            continue
        cam = entry['cam']
        dist = math.hypot(RSU_X - cam['x'], RSU_Y - cam['y'])
        approaching = ((RSU_X - cam['x']) * cam['heading']) > 0
        srm_valid = entry['srm_active'] and ((now - entry['srm_ts']) <= SRM_TIMEOUT_S)
        grants = bool(srm_valid and dist <= GEOFENCE_M and approaching)
        if grants:
            any_green_grant = True
        active_list.append({
            'vehicle_id': vid, 'x': cam['x'], 'heading': cam['heading'],
            'dist_m': round(dist, 1), 'approaching': approaching,
            'srm_valid': srm_valid, 'grants_green': grants
        })

    rem_hold = max(0.0, override_state['until_ts'] - now)
    if rem_hold > 0.0 and override_state['phase'] in ('RED', 'GREEN'):
        phase = override_state['phase']
        status = f"MANUAL OVERRIDE HOLD ({phase}, {rem_hold:.1f}s left)"
    else:
        override_state['phase'] = None
        rem_hold = 0.0
        if any_green_grant:
            phase, status = 'GREEN', 'GRANTED (APPROACHING SRM)'
        elif len(active_list) > 1:
            phase, status = 'RED', f'MULTI-VEHICLE DETECTED ({len(active_list)} VEHICLES)'
        elif len(active_list) == 1:
            phase, status = 'RED', 'SINGLE VEHICLE TRACKED (NO PRIORITY)'
        else:
            phase, status = 'RED', 'IDLE (NO ACTIVE VEHICLES)'

    set_signal(phase)
    live_telemetry.update({
        'override_remaining_s': round(rem_hold, 1),
        'vehicle_count': len(active_list),
        'multi_vehicle': len(active_list) > 1,
        'vehicles': active_list,
        'status': status,
        'phase': phase
    })

def udp_listener_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', 5000))
    while True:
        raw, _ = sock.recvfrom(1024)
        pkt = json.loads(raw.decode())
        vid, mtype = pkt['vehicle_id'], pkt['msg_type']
        now = time.time()
        with state_lock:
            entry = vehicles.setdefault(vid, {'cam': None, 'srm_active': False, 'srm_ts': 0.0, 'last_seen': now})
            entry['last_seen'] = now
            if mtype == 'CAM':
                entry['cam'] = pkt
                live_telemetry['cam_count'] += 1
            elif mtype == 'SRM':
                entry['srm_active'] = pkt['siren_active'] and (pkt['requested_phase'] == 'GREEN')
                entry['srm_ts'] = now
                live_telemetry['srm_count'] += 1
            evaluate_corridor_locked(now)

app = Flask(__name__)

@app.route('/')
def index():
    return '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>V2I Multi-Vehicle Corridor</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; }
  .card { max-width: 940px; margin: auto; background: #1e293b; border-radius: 12px; padding: 20px; }
  .alert { padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; font-weight: bold; display: none; }
  .alert-multi { background: #78350f; border: 1px solid #f59e0b; color: #fef3c7; }
  svg { width: 100%; height: 220px; background: #0b1120; border-radius: 10px; border: 1px solid #334155; }
  .veh-group { transition: transform 0.45s linear; }
  .metrics { display: flex; gap: 12px; margin-top: 14px; font-family: monospace; font-size: 13px; flex-wrap: wrap; }
  .pill { background: #0f172a; padding: 10px 14px; border-radius: 8px; flex: 1; min-width: 190px; border: 1px solid #334155; }
  button { margin-top: 14px; margin-right: 8px; padding: 9px 16px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; }
</style></head><body>
<div class="card">
  <h2>Live V2I Multi-Vehicle Corridor (<span id="rsuTitle">RSU</span>)</h2>
  <div id="multiBanner" class="alert alert-multi"></div>
  <svg id="scene" viewBox="0 0 880 220">
    <rect x="30" y="125" width="820" height="34" rx="6" fill="#334155"/>
    <line x1="35" y1="142" x2="845" y2="142" stroke="#94a3b8" stroke-width="2" stroke-dasharray="12,10"/>
    <rect id="gfZone" x="340" y="113" width="200" height="58" rx="6" fill="#22c55e" fill-opacity="0.14" stroke="#22c55e" stroke-dasharray="5,4"/>
    <text x="440" y="105" fill="#86efac" font-size="11" text-anchor="middle">200 m Geofence</text>
    <g id="rsuGroup" transform="translate(440, 142)">
      <line x1="0" y1="0" x2="0" y2="-54" stroke="#cbd5e1" stroke-width="4"/>
      <circle id="lightBulb" cx="0" cy="-60" r="12" fill="#ef4444" stroke="#ffffff" stroke-width="2"/>
      <text id="rsuXLabel" x="0" y="36" fill="#e2e8f0" font-size="11" font-weight="bold" text-anchor="middle">RSU</text>
    </g>
    <g id="dynamicLayer"></g>
  </svg>
  <div class="metrics">
    <div class="pill" id="mVeh">Vehicles: 0</div>
    <div class="pill" id="mPkts">CAMs: 0 | SRMs: 0</div>
    <div class="pill" id="mHold">Hold Timer: 0.0s</div>
    <div class="pill" id="mState">Status: IDLE</div>
  </div>
  <button style="background:#22c55e;color:#052e16" onclick="fetch('/override/GREEN')">Force GREEN (min 2s)</button>
  <button style="background:#ef4444;color:#ffffff" onclick="fetch('/override/RED')">Force RED (min 2s)</button>
</div>
<script>
  const rsuPx = 440;
  function mapX(meters, rsuX) {
    return Math.max(55, Math.min(825, rsuPx + (meters - rsuX) * 0.5));
  }
  setInterval(() => {
    fetch('/telemetry', {cache: 'no-store'}).then(r => r.json()).then(d => {
      document.getElementById('rsuTitle').innerText = `${d.rsu_id} @ x=${d.rsu_x} m`;
      document.getElementById('rsuXLabel').textContent = `RSU (${d.rsu_x}m)`;
      document.getElementById('lightBulb').setAttribute('fill', d.phase === 'GREEN' ? '#22c55e' : '#ef4444');
      const banner = document.getElementById('multiBanner');
      if (d.multi_vehicle) {
        banner.style.display = 'block';
        banner.innerText = `MULTI-VEHICLE DETECTED: ${d.vehicle_count} active vehicles on corridor (${d.vehicles.map(v => v.vehicle_id + ' @ ' + v.x + 'm, d=' + v.dist_m + 'm').join(' | ')})`;
      } else {
        banner.style.display = 'none';
      }
      const layer = document.getElementById('dynamicLayer');
      layer.innerHTML = '';
      const ns = 'http://www.w3.org/2000/svg';
      d.vehicles.forEach((v, idx) => {
        const carPx = mapX(v.x, d.rsu_x);
        const lineY = 34 + idx * 24;
        const x1 = Math.min(carPx, rsuPx), x2 = Math.max(carPx, rsuPx), mid = (x1 + x2) / 2;
        const color = v.srm_valid ? '#f87171' : '#38bdf8';
        const ln = document.createElementNS(ns, 'line');
        ln.setAttribute('x1', x1); ln.setAttribute('y1', lineY);
        ln.setAttribute('x2', x2); ln.setAttribute('y2', lineY);
        ln.setAttribute('stroke', color); ln.setAttribute('stroke-width', '2');
        layer.appendChild(ln);
        const lbl = document.createElementNS(ns, 'text');
        lbl.setAttribute('x', mid); lbl.setAttribute('y', lineY - 5);
        lbl.setAttribute('fill', color); lbl.setAttribute('font-size', '11');
        lbl.setAttribute('font-weight', 'bold'); lbl.setAttribute('text-anchor', 'middle');
        lbl.textContent = `${v.vehicle_id}: d=${v.dist_m}m`;
        layer.appendChild(lbl);
        const g = document.createElementNS(ns, 'g');
        g.setAttribute('class', 'veh-group');
        g.setAttribute('transform', `translate(${carPx}, 142)`);
        const rect = document.createElementNS(ns, 'rect');
        rect.setAttribute('x', '-18'); rect.setAttribute('y', '-9');
        rect.setAttribute('width', '36'); rect.setAttribute('height', '18');
        rect.setAttribute('rx', '4'); rect.setAttribute('fill', v.srm_valid ? '#dc2626' : '#0284c7');
        rect.setAttribute('stroke', '#ffffff'); rect.setAttribute('stroke-width', '1.5');
        const t = document.createElementNS(ns, 'text');
        t.setAttribute('x', '0'); t.setAttribute('y', idx % 2 === 0 ? '-15' : '24');
        t.setAttribute('fill', '#f8fafc'); t.setAttribute('font-size', '10');
        t.setAttribute('font-weight', 'bold'); t.setAttribute('text-anchor', 'middle');
        t.textContent = `${v.vehicle_id} (${v.x}m)`;
        g.appendChild(rect); g.appendChild(t);
        layer.appendChild(g);
      });
      document.getElementById('mVeh').innerText = `Vehicles: ${d.vehicle_count}${d.multi_vehicle ? ' (MULTI!)' : ''}`;
      document.getElementById('mPkts').innerText = `CAMs: ${d.cam_count} | SRMs: ${d.srm_count}`;
      document.getElementById('mHold').innerText = `Hold (min ${d.override_hold_s}s): ${d.override_remaining_s}s`;
      document.getElementById('mState').innerText = `${d.status} -> ${d.phase}`;
    });
  }, 250);
</script></body></html>'''

@app.route('/telemetry')
def telemetry():
    with state_lock:
        evaluate_corridor_locked(time.time())
        return jsonify(dict(live_telemetry))

@app.route('/override/<phase>')
def override(phase):
    p = phase.upper()
    if p not in ('RED', 'GREEN'):
        return jsonify({'error': 'Invalid phase'}), 400
    now = time.time()
    with state_lock:
        override_state['phase'] = p
        override_state['until_ts'] = now + OVERRIDE_HOLD_S
        evaluate_corridor_locked(now)
    return jsonify({'override': p, 'hold_s': OVERRIDE_HOLD_S})

if __name__ == '__main__':
    set_signal('RED')
    threading.Thread(target=udp_listener_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, threaded=True)
