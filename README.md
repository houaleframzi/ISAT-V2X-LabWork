# ISAT V2X Lab Work — Smart Intersection for Ambulances

> **TP2 · AESM / Vehicle and Infrastructure Connectivity · ISAT – Université Bourgogne Europe**
> Raspberry Pi · CAM/SRM V2X messaging over UDP · LED GPIO control · Flask TMC dashboard

A hands-on lab in which an **ambulance (OBU)** broadcasts V2X messages and a **Raspberry Pi roadside unit (RSU)** grants it a green light **only when the ambulance is actually approaching** the intersection. A web-based **Traffic Management Center (TMC)** shows live telemetry and allows manual overrides.

- **Duration:** 3 hours, in pairs (2 computers per group)
- **Author / instructor:** Dr. A.-R. Houalef (Ahmed-ramzi.houalef@ube.fr)
- **Academic year:** 2026/2027

---

## Table of contents

1. [What you will learn](#1-what-you-will-learn)
2. [System architecture](#2-system-architecture)
3. [V2X messages: CAM and SRM](#3-v2x-messages-cam-and-srm)
4. [Priority logic](#4-priority-logic)
5. [Hardware and software requirements](#5-hardware-and-software-requirements)
6. [Repository structure](#6-repository-structure)
7. [Quick start](#7-quick-start)
8. [Part 1 — RSU network provisioning and headless access](#8-part-1--rsu-network-provisioning-and-headless-access)
9. [Part 2 — LED traffic signal control](#9-part-2--led-traffic-signal-control)
10. [Part 3 — CAM and SRM broadcast](#10-part-3--cam-and-srm-broadcast)
11. [Part 4 — Real-time TMC dashboard](#11-part-4--real-time-tmc-dashboard)
12. [Configuration reference](#12-configuration-reference)
13. [Troubleshooting](#13-troubleshooting)
14. [Checkpoint questions](#14-checkpoint-questions)
15. [Safety notes](#15-safety-notes)
16. [Possible extensions](#16-possible-extensions)

---

## 1. What you will learn

| # | Skill | Description |
|---|-------|-------------|
| 1 | **Headless Pi setup** | Configure a Raspberry Pi as a roadside controller, give it an mDNS hostname, and manage it remotely via SSH and VNC. |
| 2 | **GPIO control** | Wire an LED circuit on a breadboard and drive physical traffic signals with Python. |
| 3 | **V2I messaging** | Simulate an ambulance OBU that broadcasts CAM and SRM messages over UDP. |
| 4 | **Priority logic** | Program an RSU to compute vehicle distance and heading, and preempt to green only for an approaching emergency vehicle. |
| 5 | **Monitoring dashboard** | Build a Flask TMC web interface showing live telemetry with manual signal overrides. |

---

## 2. System architecture

Should every nearby traffic light turn green for an ambulance? No — only the intersections **along its route**. Responsibilities are split across three components:

| Component | Role | Simulated by |
|-----------|------|--------------|
| **OBU** (On-Board Unit) | Tracks position, speed and heading, then broadcasts V2X messages over the wireless channel. | **PC B** |
| **RSU** (Road-Side Unit) | Sits at a fixed position on the avenue. Receives messages, decides whether an emergency vehicle is approaching inside its geofence, stores telemetry in memory and drives the traffic light. | **Raspberry Pi 3B** |
| **TMC** (Traffic Management Center) | Web UI that reads live data from the RSU, displays vehicle positions and telemetry, and can force the signal. | **Flask app on the Pi**, viewed from **PC B** |

```
        PC B (OBU simulator)                         Raspberry Pi (RSU + TMC)
  ┌──────────────────────────────┐   UDP broadcast  ┌───────────────────────────────┐
  │  obu_broadcast.py            │ ───────────────► │  rsu_receiver.py / rsu_live_web.py │
  │  CAM + SRM @ 1–10 Hz         │   (Wi-Fi/Eth)    │  • parse CAM/SRM              │
  └──────────────────────────────┘                  │  • distance + heading check   │
                                                    │  • GPIO 17 → LED (red/green)  │
        PC B browser                                │  • Flask TMC on port 5000     │
  ┌──────────────────────────────┐   HTTP :5000     │                               │
  │  http://<rsu-hostname>.local │ ◄──────────────► │                               │
  │  :5000                       │                  └───────────────────────────────┘
  └──────────────────────────────┘
```

### Simulation environment

A straight **West–East avenue** is modeled along the X-axis, with `y = 0 m` for every vehicle and intersection. Each group places its RSU at a distinct `x` coordinate.

```
 Start        Ambulance →            Group 1 RSU      Group 2 RSU     Group 3 RSU
 x=0          x=260, heading=+1      x=400            x=700           x=1000
 |------------[■]=========(  🟢  )------------------(🔴)-------------(🔴)------► +X (East)
                 └── 200 m geofence (x = 200…400) ──┘
                 d = 140 m (approaching) → GREEN      d = 440 m → RED   d = 740 m → RED
```

**Suggested RSU coordinates:**

| Group | `RSU_X` (m) |
|-------|-------------|
| 1 | 400.0 |
| 2 | 700.0 |
| 3 | 1000.0 |
| 4 | 1300.0 |

---

## 3. V2X messages: CAM and SRM

| Message | Meaning | Key fields | Why it matters |
|---------|---------|-----------|----------------|
| **CAM** — Cooperative Awareness Message | *"Here I am, and here is where I'm going."* Periodic heartbeat at 1–10 Hz. | `vehicle_id`, `x`, `y` (m), `speed_kmh`, `heading` (`+1` East / `-1` West) | Lets the RSU compute the exact distance to the intersection and decide whether the vehicle is approaching or moving away. |
| **SRM** — Signal Request Message | *"I'm on an active emergency mission — please give me a green light."* | `role` (`'EMERGENCY'`), `siren_active` (`True`/`False`), `requested_phase` (`'GREEN'`) | Distinguishes an active emergency vehicle (sirens on) from ordinary traffic or an inactive unit. |

Both messages are sent over **UDP broadcast**, so one OBU script can reach every group's Raspberry Pi without knowing any IP address.

Illustrative payloads (check `obu_broadcast.py` for the exact format used in the repo):

```json
{ "type": "CAM", "vehicle_id": "AMB-01", "x": 260.0, "y": 0.0, "speed_kmh": 60, "heading": 1 }
{ "type": "SRM", "vehicle_id": "AMB-01", "role": "EMERGENCY", "siren_active": true, "requested_phase": "GREEN" }
```

---

## 4. Priority logic

The RSU grants green only when **all** conditions hold:

1. A valid **SRM** was received with `role == 'EMERGENCY'`, `siren_active == True` and `requested_phase == 'GREEN'`.
2. The vehicle is **inside the geofence** (default radius **200 m**): `d = |x_rsu − x_vehicle| ≤ 200`.
3. The vehicle is **approaching**, not leaving: the heading points toward the RSU, i.e. `heading × (x_rsu − x_vehicle) > 0`.

Otherwise the light stays (or returns to) **RED**.

**Signal convention (single LED on GPIO 17):**

| GPIO 17 | LED | Meaning |
|---------|-----|---------|
| `True` / HIGH | ON | 🔴 **RED** — normal stop |
| `False` / LOW | OFF | 🟢 **GREEN** — priority go |

**TMC overrides:** *Force GREEN* and *Force RED* buttons hold the chosen state for a **minimum of 2 seconds** (the "Hold (min 2s)" counter on the dashboard), preventing flicker.

---

## 5. Hardware and software requirements

### Per group (2 students)

| Item | Details |
|------|---------|
| RSU | 1× Raspberry Pi 3B, **Raspberry Pi OS Legacy 32-bit (Debian Bullseye)** |
| Pi accessories | microSD card, HDMI monitor, USB keyboard + mouse, Ethernet cable |
| LED circuit | 1× breadboard, 1× LED, 1× **220 Ω** resistor, jumper wires |
| Workstations | **PC A** (network inspection) and **PC B** (OBU simulator + browser/TMC client) |

> No second workstation? Use your **personal laptop with a smartphone hotspot**.

### Software

- Python 3 on the Pi and on PC B
- `RPi.GPIO` (preinstalled on Raspberry Pi OS)
- `Flask` (for the TMC; install with `pip3 install flask` if missing)
- `git`, an SSH client, and [VNC Viewer](https://www.realvnc.com/en/connect/download/viewer/)

---

## 6. Repository structure

> Adjust to match the actual repository contents.

```
ISAT-V2X-LabWork/
├── rsu_signal_test.py     # Part 2 – LED hardware validation (also given in the worksheet)
├── rsu_receiver.py        # Part 3 – RSU: receives CAM/SRM, applies priority logic, drives LED
├── obu_broadcast.py       # Part 3 – OBU: ambulance simulator (run on PC B)
├── rsu_live_web.py        # Part 4 – RSU + Flask TMC dashboard (port 5000)
└── README.md
```

---

## 7. Quick start

```bash
# On the Raspberry Pi (via SSH from PC B)
git clone https://github.com/houaleframzi/ISAT-V2X-LabWork.git
cd ISAT-V2X-LabWork

# 1) Edit the settings flagged in the comments (group ID, RSU_X, etc.)
nano rsu_live_web.py

# 2) Start the RSU + TMC
python3 rsu_live_web.py
```

```bash
# On PC B – start the ambulance
python obu_broadcast.py      # edit the commented settings first (nano / any editor)
```

Then open **`http://<your-rsu-hostname>.local:5000`** in a browser on PC B.

---

## 8. Part 1 — RSU network provisioning and headless access

> ⚠️ Handle the Pi by its edges (ESD). **Never insert or remove the microSD card while powered.**

### 1.1 Inspect lab network parameters (PC A)

1. On PC A run `ipconfig /all` (Windows) in a terminal.
2. Record the **IPv4 subnet** (e.g. `192.168.1.x / 255.255.255.0` → `/24`), the **default gateway** and the **DNS server**.
3. Turn PC A off — the Pi will reuse its network configuration.

### 1.2 Boot the RSU and set a unique hostname

1. Connect monitor, keyboard, mouse and Ethernet; insert the microSD; power on.
2. Complete the initial user setup (**write down the username and password**).
3. Check the DHCP-assigned address: `ifconfig`
4. Set a unique hostname (replace `XX` with your group number):

   ```bash
   sudo hostnamectl set-hostname rsu_interXX
   ```

5. *(Optional — only with an ISAT Ethernet cable; skip on Wi-Fi/hotspot)* set a static IP using the values recorded on PC A:

   ```bash
   sudo nano /etc/dhcpcd.conf
   ```

   ```conf
   # Add at the bottom
   interface eth0
   static ip_address=x.x.x.x/24
   static routers=x.x.x.x
   static domain_name_servers=x.x.x.x
   ```

### 1.3 Enable SSH and VNC and verify remote login

1. On the Pi: `sudo raspi-config` → **Interface Options** → enable **SSH** and **VNC** → `sudo reboot`.
2. From PC B: `ping rsu_interXX.local`
3. Open an SSH session and create a test directory:

   ```bash
   ssh <user>@rsu_interXX.local
   mkdir test
   ```

4. On PC B, install **VNC Viewer** and connect using the RSU IP or `<hostname>.local`.

---

## 9. Part 2 — LED traffic signal control

> 🖥️ **Switch to headless mode:** unplug the monitor, keyboard and mouse. Do everything else from PC B over SSH.

### 2.1 Wiring

One LED on **GPIO 17** with a 220 Ω current-limiting resistor:

```
GPIO 17 (physical pin 11) ─► 220 Ω ─► LED anode (+, long leg)
LED cathode (–, short leg) ─► GND (physical pin 6)
```

Run `pinout` on the Pi to confirm pin positions.

### 2.2 Hardware validation script — `rsu_signal_test.py`

```python
import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
LED_PIN = 17  # True (ON) = RED Light, False (OFF) = GREEN Light
GPIO.setup(LED_PIN, GPIO.OUT)
try:
    print("Testing single-LED traffic light (Ctrl+C to stop)...")
    while True:
        GPIO.output(LED_PIN, True)    # LED ON  -> RED (Normal Stop)
        print("Signal: RED (LED ON)")
        time.sleep(1.5)
        GPIO.output(LED_PIN, False)   # LED OFF -> GREEN (Priority Go)
        print("Signal: GREEN (LED OFF)")
        time.sleep(1.5)
except KeyboardInterrupt:
    print("\nTest stopped.")
finally:
    GPIO.cleanup()
```

Run it with `python rsu_signal_test.py`. The LED should blink (ON = red, OFF = green).

> The printed worksheet shows `time.sleep(0)` while Q4 refers to `time.sleep(1.5)`; the version above uses 1.5 s so the blinking is visible.

---

## 10. Part 3 — CAM and SRM broadcast

### 3.1 Choose your group's position

Pick your `RSU_X` from the [table above](#simulation-environment) (e.g. Group 1 → `400.0`, `y = 0.0`).

### 3.2 RSU receiver (on the Raspberry Pi)

```bash
git clone https://github.com/houaleframzi/ISAT-V2X-LabWork.git
cd ISAT-V2X-LabWork
nano rsu_receiver.py      # make the changes indicated in the comments (group ID, RSU_X, ...)
python3 rsu_receiver.py
```

### 3.3 Ambulance broadcaster (on PC B)

```bash
nano obu_broadcast.py     # make the changes indicated in the comments
python obu_broadcast.py
```

You should see the ambulance's CAM/SRM messages being sent (Q8), and the RSU console reporting receptions and light changes as the ambulance enters the geofence.

**Why broadcast?** A UDP broadcast datagram is delivered to every host on the subnet that listens on the target port, so a single OBU script can serve all groups' Pis without knowing their IP addresses.

---

## 11. Part 4 — Real-time TMC dashboard

### 4.1 Launch the TMC (on the Raspberry Pi)

```bash
nano rsu_live_web.py      # make the required modifications (group ID, RSU_X, ...)
python3 rsu_live_web.py
```

Open **`http://<your-rsu-hostname>.local:5000`** on PC B. Make sure `obu_broadcast.py` is running.

### Dashboard elements

| Element | Description |
|---------|-------------|
| Corridor view | Road, RSU marker, 200 m geofence and ambulance positions, each labelled with distance (e.g. `AMB-01: d=162.5m`). |
| **Vehicles** | Number of vehicles currently tracked. |
| **CAMs / SRMs** | Counters of received messages. |
| **Hold (min 2s)** | Remaining time of the minimum-hold timer after a state change/override. |
| Status box | Decision reason, e.g. `GRANTED (APPROACHING SRM) -> GREEN`. |
| **Force GREEN / Force RED** | Manual overrides, each held for at least 2 s. |

### Multi-ambulance test

Coordinate with another group to launch **two ambulances**, or run `obu_broadcast.py` in several terminals with different vehicle IDs.

---

## 12. Configuration reference

Exact variable names are in the comments of each script — this table lists the values you are expected to set or know.

| Setting | Where | Typical value |
|---------|-------|---------------|
| Group number / RSU ID | `rsu_receiver.py`, `rsu_live_web.py` | e.g. `RSU-GROUP-01` |
| `RSU_X` | RSU scripts | `400.0` / `700.0` / `1000.0` / `1300.0` m |
| RSU `y` | RSU scripts | `0.0` m |
| Geofence radius | RSU scripts | `200 m` |
| LED GPIO pin | RSU scripts | BCM **17** (physical pin 11) |
| Minimum hold time | `rsu_live_web.py` | `2 s` |
| UDP port | OBU and RSU scripts (must match) | see script comments |
| Web server | `rsu_live_web.py` | `host='0.0.0.0'`, port `5000` |
| CAM rate | `obu_broadcast.py` | 1–10 Hz |
| Vehicle ID | `obu_broadcast.py` | unique per ambulance (e.g. `AMB-01`, `AMB-02`) |

---

## 13. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `ping rsu_interXX.local` fails | mDNS (Avahi) not running or Pi/PC on different subnets. Check `ifconfig`, try the IP address directly. Hostnames with underscores are not valid under RFC 1123 — if resolution fails, use a hyphen (`rsu-interXX`). |
| SSH/VNC refused | Re-enable in `sudo raspi-config` → Interface Options, then reboot. |
| LED never lights | Check polarity (long leg = anode toward the resistor), confirm GPIO 17 / pin 11 and GND / pin 6 with `pinout`. |
| `RuntimeWarning: channel already in use` | A previous script exited without `GPIO.cleanup()`. Run it again or reboot. |
| `ModuleNotFoundError: flask` | `pip3 install flask`. |
| RSU shows no messages | OBU and RSU must be on the same subnet and use the **same UDP port**; some hotspots/switches block broadcast or isolate clients. |
| Dashboard unreachable | Script not running, wrong hostname, or Flask bound to `127.0.0.1` instead of `0.0.0.0`; port 5000 must be reachable. |
| Light stays RED although ambulance is in range | Check `siren_active`, `role`, `requested_phase`, `heading` sign and that the vehicle is approaching (not past the RSU). |
| Light flickers | Respect the minimum-hold timer; check that several OBUs are not sending conflicting requests. |

---

## 14. Checkpoint questions

**Part 1**
- **Q1.** Why are headless protocols (SSH/VNC) essential for roadside controllers in sealed street cabinets?
- **Q2.** What is the difference between a static and a dynamic IP address?
- **Q3.** What happens if two traffic lights have the same IP address?

**Part 2**
- **Q4.** What happens visually, and to CPU usage, if `time.sleep(1.5)` is removed from the `while True` loop?
- **Q5.** What happens if `GPIO.cleanup()` is omitted?
- **Q6.** How can you make the light change faster?

**Part 3**
- **Q7.** Why can one UDP broadcast script control four different Raspberry Pis without knowing their IP addresses?
- **Q8.** What do you see after executing `obu_broadcast.py`?

**Part 4**
- **Q10.** What do you notice when multiple ambulances cross the same RSU?
- **Q11.** Why bind Flask to `host='0.0.0.0'` instead of `'127.0.0.1'`?

---

## 15. Safety notes

- Handle the Raspberry Pi by its edges to avoid **electrostatic discharge**.
- **Never insert or remove the microSD card while power is connected.**
- Always use the **220 Ω resistor** in series with the LED to protect both the LED and the GPIO pin.
- Shut down cleanly (`sudo shutdown now`) before unplugging power.

---

## 16. Possible extensions

- Add a **three-LED** (red/amber/green) signal using three GPIO pins.
- Add an **amber phase** and a return-to-red delay after the ambulance clears the intersection.
- Log all CAM/SRM messages to a CSV file and plot trajectories.
- Add message authentication (e.g. HMAC) to prevent spoofed SRMs.
- Replace the JSON/UDP format with real **ETSI ITS** (CAM/DENM) or **SAE J2735** (BSM/SRM) encodings.
- Coordinate several RSUs to create a **green wave** along the full corridor.

---

## License and credits

Educational material for the AESM *Vehicle and Infrastructure Connectivity* course, ISAT — Université Bourgogne Europe. Add the license of your choice here (e.g. MIT) if you publish the repository publicly.

**Contact:** Ahmed-ramzi.houalef@ube.fr
