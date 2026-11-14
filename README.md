# ARTHROGUARD: Wearable Knee Osteoarthritis (OA) Detection & Gait Biomechanics System

[![Build & Test Status](https://img.shields.io/badge/Verification-5%2F5%20Passed-10b981.svg?style=flat-square)](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32/verify_all.py)
[![Pytest Suite](https://img.shields.io/badge/Tests-36%2F36%20Passed-10b981.svg?style=flat-square)](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32/tests)
[![Firmware](https://img.shields.io/badge/ESP32-WROOM--32-blue.svg?style=flat-square)](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32/OA_Detection_ESP32.ino)
[![Backend](https://img.shields.io/badge/FastAPI-v2.1.0-0f766e.svg?style=flat-square)](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32/backend)
[![Dashboard](https://img.shields.io/badge/UI-Apple--Minimalist%20Tokens-3b82f6.svg?style=flat-square)](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32/dashboard.html)
[![Clinical Compliance](https://img.shields.io/badge/Clinical-AAOS%20%7C%20WHO%20%7C%20ACR-indigo.svg?style=flat-square)](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32/backend/schemas.py)

**ARTHROGUARD** is a multi-sensor wearable biomechanical sleeve and real-time clinical monitoring platform designed for early detection, gait analysis, and progressive monitoring of **Knee Osteoarthritis (OA)**.

Built on an **ESP32 DevKit WROOM-32** edge node paired with a **FastAPI** telemetry backend, SQLite clinical database, and an **Apple-inspired minimalist HTML5/Canvas dashboard**, the system continuously captures knee joint flexion/extension kinematics, dynamic varus/valgus thrust, tibial impact shock, and plantar foot pressure distribution at 10 Hz.

---

## Key System Architecture

```
                                 ┌─────────────────────────────────────────────────────────────┐
                                 │                   WEARABLE SENSOR SUITE                     │
                                 │  - Thigh MPU6050 (0x68) & Shin MPU6050 (0x69 via AD0 Pin)   │
                                 │  - 2.2" Conductive Flex Strip on Joint Line (GPIO 32 ADC)   │
                                 │  - Dual Plantar FSRs: Heel (GPIO 34), Forefoot (GPIO 35)    │
                                 │  - Visual/Audio Alerts: Green LED (26), Red (27), Buzzer(25)│
                                 └──────────────────────────────┬──────────────────────────────┘
                                                                │
                                        ┌───────────────────────┴───────────────────────┐
                                        ▼                                               ▼
                         ┌─────────────────────────────┐                 ┌─────────────────────────────┐
                         │      MODE A: ESP32 WiFi     │                 │   MODE B: Bluetooth SPP     │
                         │ - WebSocket: /ws/esp32      │                 │ - Classic BT SPP @ 10 Hz    │
                         │ - HTTP Fallback: /telemetry │                 │ - Native macOS SPP Bridge   │
                         │ - AP Hotspot @ 192.168.4.1  │                 │ - Python Relay (bt_bridge)  │
                         └──────────────┬──────────────┘                 └──────────────┬──────────────┘
                                        │                                               │
                                        └───────────────────────┬───────────────────────┘
                                                                ▼
                                 ┌─────────────────────────────────────────────────────────────┐
                                 │                    FASTAPI BACKEND SERVER                   │
                                 │  - Asynchronous WebSocket Dispatcher (10 Hz)               │
                                 │  - Biomechanical Feature Pipeline (Varus, Shock, Imbalance)│
                                 │  - Random Forest OA Risk Model (Kellgren-Lawrence Tiers)    │
                                 │  - Bilingual Exercise State Machine (Hindi + English)       │
                                 │  - SQLite Database (Sessions, Patients, Trials, Alerts)     │
                                 └──────────────────────────────┬──────────────────────────────┘
                                                                ▼
                                 ┌─────────────────────────────────────────────────────────────┐
                                 │             ARTHROGUARD CLINICAL WEB DASHBOARD              │
                                 │  - 60 FPS HTML5 Canvas Leg Kinematic Simulation Engine      │
                                 │  - Real-time Plantar Pressure Heatmap & Varus Gauge         │
                                 │  - 2-Point Sensor Calibration Protocol (0° Stand / 90° Sit) │
                                 │  - Offline Chart.js Waveforms & Automated PDF/HTML Reports  │
                                 └─────────────────────────────────────────────────────────────┘
```

---

## Features & Clinical Capabilities

### 1. Multi-Sensor Biomechanical Fusion
- **Dual MPU6050 6-DoF IMUs (Common I2C Bus):**
  - **Thigh IMU (0x68):** Strapped 8 cm above lateral femoral epicondyle (`AD0 -> GND`).
  - **Shin IMU (0x69):** Strapped 8 cm below tibial tuberosity (`AD0 -> 3.3V`).
  - Relative goniometric angle calculation: $\theta_{\text{knee}} = \text{Shin}_{\text{pitch}} - \text{Thigh}_{\text{pitch}}$ with orientation auto-polarity correction.
- **2.2" Conductive Flex Sensor (GPIO 32 ADC):**
  - Positioned along the posterior joint line in a 10 kΩ voltage divider.
  - Normalized flex ratio ($0.0 \to 1.0$) mapped across 0° straight to 90° bent knee postures.
  - Real-time detection of joint stiffness: triggers alert when flexion angle $> 45^\circ$ with flex ratio $< 0.25$.
- **Plantar Force-Sensitive Resistors (FSRs):**
  - **Heel FSR (GPIO 34 ADC):** Captures initial heel strike load and heel impact rate.
  - **Forefoot FSR (GPIO 35 ADC):** Captures push-off propulsion load.
  - Plantar Imbalance Index: $\text{Imbalance} = \frac{|\text{Heel} - \text{Forefoot}|}{\text{Heel} + \text{Forefoot}} \times 100\%$. Alerts when imbalance exceeds $60\%$.
- **Anti-Jitter Outlier Trimmed EMA:**
  - Firmware filters ADC noise using a 12-sample trimmed window and exponential moving average ($\alpha = 0.82$), eliminating spurious $\pm 5^\circ$ angle deflections.

### 2. Clinical Truth Table & Hardware Status Indicators
The wearable features zero-latency visual and acoustic biofeedback adhering to Rulebook Section 11:

| Clinical Condition | OA Risk Tier | Green LED (GPIO 26) | Red LED (GPIO 27) | Piezo Buzzer (GPIO 25) | System Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Normal Movement** | Low (< 35 pts) | **Bright Solid ON** | **OFF** | **OFF** | Smooth, balanced gait within safe ROM ($-10^\circ \text{ to } +135^\circ$). |
| **Moderate Warning**| Moderate (35–65 pts)| **Dim ("Less Green")** | **OFF** | **OFF** | Mild asymmetric load or early ROM restriction. Buzzer remains silent to avoid patient irritation. |
| **Critical / High Risk**| High (> 65 pts) | **OFF** | **Solid Red ON** | **Active Alert Tone** | Excessive varus thrust, stiff joint hold, hyperflexion, or severe loading asymmetry ($>60\%$). |
| **Stationary Rest** | Settled Rest | **Bright Solid ON** | **OFF** | **OFF** | Settled standing or seated rest ($>1.5\text{s}$, velocity $<6^\circ/\text{s}$). Dynamic alarms suppressed. |
| **Waiting / Setup** | Boot / Calibration | **Blinking (450ms)** | **OFF** | **OFF** | AP captive portal active or hardware waiting for initial calibration baseline. |

### 3. Dual Communication Topologies
1. **WiFi WebSocket Mode (`OA_Detection_ESP32.ino`):**
   - Direct WebSocket streaming (`ws://<host>:8000/ws/esp32`) at 10 Hz.
   - Transparent HTTP POST fallback (`/api/telemetry`) if WebSocket connection drops.
   - Built-in on-device captive portal (`OA-Wearable-Setup` @ `http://192.168.4.1`) for in-field WiFi credential reconfiguration without reflashing.
2. **Bluetooth Classic SPP Mode (`OA_Detection_ESP32_BT.ino`):**
   - RFCOMM Serial Port Profile broadcasting as `OA-Knee-Wearable`.
   - Native macOS Swift helper (`macos_bt_spp`) streams serial packets directly into Python bridge (`bt_bridge.py`), ideal for clinics or sports facilities without local WiFi access.

### 4. AI Risk Assessment & Clinical Analytics
- **Machine Learning Classifier:** Scikit-Learn Random Forest trained on simulated and clinical gait datasets incorporating Kellgren-Lawrence radiographic classifications (KL 0–4).
- **Longitudinal Patient History:** SQLite database tracks session progression, bilateral left vs. right knee asymmetry, and BMI joint-load correlations (WHO universal thresholds: Underweight $<18.5$, Normal $18.5\text{--}24.9$, Overweight $25\text{--}29.9$, Obese $\ge 30$).
- **Epidemiological Regional Registry:** Built-in regional tracking dashboard pre-configured for Northeastern India (NER) district health surveillance.
- **Bilingual Exercise Rehabilitation Module:** Step-by-step guided physical therapy (Seated Flexion-Extension, Sit-to-Stand, Mini Squats, Weight Shifts) with dual English/Hindi speech synthesizer coaching and automatic repetition counting.

---

## Hardware Pinout & Wiring

| Sensor / Component | Pin on Component | ESP32 GPIO Pin | Function & Electrical Notes |
| :--- | :--- | :--- | :--- |
| **MPU6050 #1 (Thigh)** | VCC | 3.3V | 3.3V Clean Power Rail |
| | GND | GND | Ground |
| | SDA | **GPIO 21** | I2C Data (Common bus) |
| | SCL | **GPIO 22** | I2C Clock (Common bus, 400 kHz) |
| | AD0 | **GND** | Hardware address select: sets I2C address to **`0x68`** |
| **MPU6050 #2 (Shin)** | VCC | 3.3V | 3.3V Clean Power Rail |
| | GND | GND | Ground |
| | SDA | **GPIO 21** | I2C Data (Common bus) |
| | SCL | **GPIO 22** | I2C Clock (Common bus, 400 kHz) |
| | AD0 | **3.3V** | Hardware address select: sets I2C address to **`0x69`** |
| **Flex Sensor (2.2")** | Terminal 1 | 3.3V | Voltage divider circuit with 10 kΩ resistor |
| | Terminal 2 | **GPIO 32** | ADC1 Channel 4 (12-bit, 0–4095) |
| **Heel FSR (0.5")** | Terminal 1 | 3.3V | Voltage divider circuit with 10 kΩ pull-down |
| | Terminal 2 | **GPIO 34** | ADC1 Channel 6 (Input-only pin, 12-bit) |
| **Forefoot FSR (0.5")** | Terminal 1 | 3.3V | Voltage divider circuit with 10 kΩ pull-down |
| | Terminal 2 | **GPIO 35** | ADC1 Channel 7 (Input-only pin, 12-bit) |
| **Normal Green LED** | Anode (+) | **GPIO 26** | In series with 220 Ω current-limiting resistor |
| | Cathode (-) | GND | Ground |
| **Alert Red LED** | Anode (+) | **GPIO 27** | In series with 220 Ω current-limiting resistor |
| | Cathode (-) | GND | Ground |
| **Piezo Buzzer** | Positive (+) | **GPIO 25** | Direct drive or NPN transistor buffered |
| | Negative (-) | GND | Ground |

---

## Quickstart & Installation

### 1. Prerequisites
- **Python:** 3.10, 3.11, 3.12, 3.13, or 3.14
- **Arduino IDE:** 2.x (with ESP32 board definitions installed)
- **Git**

### 2. Backend Setup
Clone this repository and create a Python virtual environment:

```bash
git clone https://github.com/<your-username>/ARTHROGUARD-OA-Detection.git
cd ARTHROGUARD-OA-Detection/OA_Detection_ESP32

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

Start the FastAPI backend server:

```bash
./run_backend.sh
```

- **Dashboard:** Open [http://localhost:8000](http://localhost:8000) in Google Chrome or Safari.
- **REST API Docs (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **WebSocket Inbound (ESP32):** `ws://<your-lan-ip>:8000/ws/esp32`

---

### 3. Firmware Upload

#### Option A: WiFi Mode (Recommended for Wireless Operation)
1. Open [OA_Detection_ESP32_Arduino/OA_Detection_ESP32/OA_Detection_ESP32.ino](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32_Arduino/OA_Detection_ESP32/OA_Detection_ESP32.ino) in Arduino IDE.
2. Ensure required Arduino libraries are installed:
   - `ArduinoJson` (v6 or v7)
   - `Adafruit MPU6050` & `Adafruit BusIO`
3. Edit `secrets.h` to set your local 2.4 GHz WiFi SSID, password, and your computer's LAN IP address (`CONFIG_BACKEND_HOST`).
4. Select board **ESP32 Dev Module** and flash via USB.
5. *Zero-Config Fallback:* If WiFi cannot connect, connect your phone or laptop to the hotspot **`OA-Wearable-Setup`** and visit `http://192.168.4.1` to set network credentials directly in browser.

#### Option B: Bluetooth Classic Mode (No WiFi Required)
1. Open [OA_Detection_ESP32_Bluetooth/OA_Detection_ESP32_BT/OA_Detection_ESP32_BT.ino](file:///Users/sandeepkumarpal/Antigravity/OA_Detection_ESP32_Bluetooth/OA_Detection_ESP32_BT/OA_Detection_ESP32_BT.ino) in Arduino IDE.
2. Flash to the ESP32.
3. On macOS, pair with **`OA-Knee-Wearable`** in System Settings -> Bluetooth.
4. Launch the local Bluetooth-to-WebSocket bridge:
   ```bash
   cd OA_Detection_ESP32
   ./run_bt_bridge.sh
   ```

---

## 2-Point Calibration Protocol

Before starting a clinical assessment or gait trial, execute the 2-point calibration wizard from the dashboard:

1. **Step 1: Straight Standing Baseline (0° Flexion)**
   - Wearer stands upright with knees locked straight.
   - Click **`Calibrate Step 1 (0° Standing)`**.
   - The system records thigh/shin pitch offsets and tares baseline plantar foot pressure.
2. **Step 2: Seated Right-Angle Calibration (90° Flexion)**
   - Wearer sits on a chair with knees bent at a 90° angle.
   - Click **`Calibrate Step 2 (90° Seated)`**.
   - The system captures bent flex sensor resistance, verifies IMU sign polarity, and writes calibration parameters into the ESP32's non-volatile NVS flash.

---

## Automated Test & Verification Suite

The repository contains a strict, zero-tolerance automated verification harness validating biomechanical equations, clinical safety thresholds, firmware resilience, and DOM integrity:

```bash
cd OA_Detection_ESP32
source .venv/bin/activate

# 1. Run full 36-test pytest suite
pytest tests/ -v

# 2. Run master 5-stage system verification runner
python verify_all.py

# 3. Run headless DOM & API endpoint audit
python e2e_dashboard_audit.py
```

### Verification Pipeline Breakdown:
1. `Full Pytest Automated Test Suite`: 36 unit/integration tests across AI models, analytics, kinematics, bilingual audio, and database lifecycles.
2. `Backend Configuration Validation`: Validates non-hardcoded environment overrides, default ports, and dynamic filesystem resolutions.
3. `Rulebook Section 11 Compliance`: Validates indicator truth tables, sensor rail short/open circuit bounds, and AAOS clinical thresholds.
4. `ESP32 Firmware De-Hardcoding & Hardening`: Validates I2C timeout protection, Task Watchdog Timer, and secrets template safety.
5. `End-to-End Headless DOM & API Route Audit`: Audits DOM elements (64 buttons, 7 selectors, 5 sliders, 4 navigation tabs, 7 modals, 5 canvases), verifies 0 decorative emojis, and validates 100% of frontend `fetch`/WebSocket calls against registered FastAPI endpoints.

---

## Repository Structure

```
├── .gitignore                          # Clean ignore rules (no venv, db, or secrets)
├── README.md                           # Master architectural and clinical documentation
├── OA_Detection_ESP32/                 # Main working project root
│   ├── OA_Detection_ESP32.ino          # Primary WiFi firmware (WebSocket + HTTP fallback)
│   ├── config.h                        # Central hardware pins, I2C addresses, clinical thresholds
│   ├── secrets.h.template              # Safe credentials template for version control
│   ├── dashboard.html                  # Apple-minimalist HTML5/Canvas clinical dashboard
│   ├── verify_all.py                   # Master 5-stage automated verification runner
│   ├── e2e_dashboard_audit.py          # Headless DOM, design token & route auditor
│   ├── run_backend.sh                  # One-command backend startup script
│   ├── run_bt_bridge.sh                # Bluetooth SPP bridge launcher
│   ├── bt_bridge.py                    # Python serial-to-WebSocket relay
│   ├── macos_bt_spp.swift              # Native macOS CoreBluetooth/IOBluetooth SPP client
│   ├── requirements.txt                # Python backend dependencies
│   ├── backend/                        # Modular FastAPI Application
│   │   ├── main.py                     # App entrypoint, lifespan manager, WS dispatcher
│   │   ├── config.py                   # Pydantic Settings & environment variable overrides
│   │   ├── schemas.py                  # Telemetry, calibration & clinical data models
│   │   ├── database.py                 # Asynchronous SQLite persistence engine
│   │   ├── ai_engine.py                # Machine learning Random Forest OA risk model
│   │   ├── biomechanics.py             # Plantar pressure, varus thrust & joint kinematics
│   │   ├── calibration.py              # 2-point normalization and angle wrap arithmetic
│   │   ├── exercises.py                # Physical therapy exercise tracker & rep counter
│   │   ├── i18n.py                     # Bilingual English/Hindi medical lexicon
│   │   ├── sensor_health.py            # Rail diagnostics, ADC limits & fault guards
│   │   ├── simulator.py                # Virtual ESP32 multi-profile kinematics emulator
│   │   ├── ws_manager.py               # Bi-directional WebSocket connection manager
│   │   ├── static/                     # Offline JavaScript bundles (Chart.js UMD)
│   │   └── routers/                    # REST API endpoints (telemetry, patients, etc.)
│   └── tests/                          # Automated Pytest Suite (36 tests)
│       ├── test_ai_engine.py           # ML model training, serialization & risk tiers
│       ├── test_analytics_and_health.py# Longitudinal BMI, NER registry & fault isolation
│       ├── test_api.py                 # REST lifecycle, patient CRUD & simulator endpoints
│       ├── test_biomechanics.py        # Foot pressure zones, needle math & varus thrust
│       ├── test_bt_bridge.py           # Bluetooth sketch presence & packet parsing
│       ├── test_calibration.py         # Polarity inversion & 2-point normalization
│       ├── test_config.py              # Pydantic default integrity & env overrides
│       ├── test_exercises.py           # Rep counting, ROM thresholds & trial export
│       ├── test_rulebook_compliance.py # Rulebook Section 11 truth table & rest guards
│       └── test_websocket_bridge.py    # Bidirectional client/device relay verification
├── OA_Detection_ESP32_Arduino/         # Arduino IDE package for WiFi deployment
│   └── OA_Detection_ESP32/
│       ├── OA_Detection_ESP32.ino
│       ├── config.h
│       └── secrets.h
└── OA_Detection_ESP32_Bluetooth/       # Arduino IDE package for Bluetooth SPP deployment
    └── OA_Detection_ESP32_BT/
        ├── OA_Detection_ESP32_BT.ino
        └── config.h
```

---

## Clinical Standards & Biomedical References

1. **American Academy of Orthopaedic Surgeons (AAOS):** *Joint Motion: Method of Measuring and Recording.* Normal physiological knee range of motion: $0^\circ \text{ (full extension) to } 135^\circ \text{ (full flexion)}$.
2. **Kellgren, J. H., & Lawrence, J. S. (1957):** *Radiological assessment of osteo-arthrosis.* Annals of the Rheumatic Diseases, 16(4), 494–502.
3. **Zeni, J. A., & Higginson, J. S. (2009):** *Differences in gait parameters between healthy subjects and persons with moderate and severe knee osteoarthritis.* Clinical Biomechanics, 24(9), 772–778.
4. **World Health Organization (WHO):** *Global Database on Body Mass Index (BMI).* Adult classification standards.
5. **American College of Rheumatology (ACR):** *Criteria for the Classification and Reporting of Osteoarthritis of the Knee.*

---

## License

This project is licensed under the **MIT License** — feel free to use and adapt for academic, clinical research, or wearable biomedical product development.

