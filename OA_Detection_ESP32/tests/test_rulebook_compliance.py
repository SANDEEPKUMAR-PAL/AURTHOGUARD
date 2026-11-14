"""Comprehensive test suite for Rulebook Section 11 Working Conditions and Clinical Biomechanics.

Validates:
1. Working conditions truth table (Normal, Abnormal, Critical, Disconnected/Offline).
2. AAOS clinical knee goniometry limits (-10.0° hyperextension, 135.0° hyperflexion).
3. Plantar load distribution thresholds (FSR imbalance > 60% alert, > 40% warning).
4. Tendon stiffness index threshold (< 0.25 at > 45.0° flexion).
5. Frontal plane varus angular velocity (PVV >= 28.1 deg/s).
6. Heel strike transient impact threshold (>= 2.5g).
7. Universal WHO BMI classification and ACR age >= 45 prior.
8. 12-bit ADC sensor rail diagnostics (Flex <=15 / >=4085, FSR >=4088, IMU [0.25g, 4.5g]).
"""

import math
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.biomechanics import compute_foot_pressure_metrics
from backend.schemas import TelemetryIn, IMUSensorData
from backend.sensor_health import evaluate_sensor_health
from backend.ai_engine import GaitWindowAnalyzer, OARiskModel
from backend.analytics import compute_bmi_summary
from backend.main import create_app


def test_rulebook_section_11_truth_table_hardware_indicators(tmp_path: Path):
    """Rulebook Section 11 Truth Table:

    Normal:      Green LED = ON,  Red LED = OFF, Buzzer = OFF
    Abnormal:    Green LED = OFF, Red LED = ON,  Buzzer = ON
    Critical:    Green LED = OFF, Red LED = ON,  Buzzer = ON
    """
    app = create_app(db_path=tmp_path / "rb.db", model_path=tmp_path / "rb.joblib")
    with TestClient(app) as client:
        # 1. Normal condition: normal angle, balanced foot pressure, low roll
        norm_res = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 30.0,
            "thigh_pitch_deg": 15.0,
            "net_roll_deg": 1.0,
            "heel_fsr": 1800,
            "fore_fsr": 1750,
            "flex_stiffness": 0.85,
            "repeat": 1,
        })
        assert norm_res.status_code == 200
        norm_frame = norm_res.json()["frame"]
        assert norm_frame["condition"] == "normal"
        assert norm_frame["abnormal"] is False
        assert norm_frame["critical"] is False
        assert norm_frame["hardwareState"] == {
            "condition": "normal",
            "greenLed": "ON",
            "redLed": "OFF",
            "buzzer": "OFF",
        }

        # 2. Abnormal condition: severe plantar imbalance (>60%)
        abn_res = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 30.0,
            "thigh_pitch_deg": 15.0,
            "net_roll_deg": 1.0,
            "heel_fsr": 3100,
            "fore_fsr": 400,
            "flex_stiffness": 0.85,
            "repeat": 1,
        })
        assert abn_res.status_code == 200
        abn_frame = abn_res.json()["frame"]
        assert abn_frame["condition"] == "abnormal"
        assert abn_frame["abnormal"] is True
        assert abn_frame["hardwareState"] == {
            "condition": "abnormal",
            "greenLed": "OFF",
            "redLed": "ON",
            "buzzer": "ON",
        }

        # 3. Critical condition: severe hyperflexion (>135°) with high roll instability
        crit_res = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 145.0,
            "thigh_pitch_deg": 50.0,
            "net_roll_deg": 8.5,
            "heel_fsr": 3200,
            "fore_fsr": 300,
            "flex_stiffness": 0.3,
            "repeat": 1,
        })
        assert crit_res.status_code == 200
        crit_frame = crit_res.json()["frame"]
        assert crit_frame["condition"] == "critical"
        assert crit_frame["critical"] is True
        assert crit_frame["hardwareState"] == {
            "condition": "critical",
            "greenLed": "OFF",
            "redLed": "ON",
            "buzzer": "ON",
        }


def test_offline_imu_false_alarm_suppression(tmp_path: Path):
    """When IMUs or sensors disconnect, AI is paused and false alarm triggers are suppressed."""
    app = create_app(db_path=tmp_path / "offline.db", model_path=tmp_path / "offline.joblib")
    with TestClient(app) as client:
        # Disconnected IMU packet
        packet = {
            "type": "data",
            "timestamp": 1000,
            "calibrated": True,
            "calibrationStep": 2,
            "thigh": {"online": False, "ax": 0.0, "ay": 0.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0, "pitch": 0.0, "roll": 0.0, "pitchC": 0.0, "rollC": 0.0},
            "shin": {"online": False, "ax": 0.0, "ay": 0.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0, "pitch": 0.0, "roll": 0.0, "pitchC": 0.0, "rollC": 0.0},
            "flex": 0,
            "flexNorm": 0.0,
            "fsrHeel": 0,
            "fsrFore": 0,
            "kneeAngle": 0.0,
            "kneeAngleC": 0.0,
            "fsrImbalance": 0.0,
            "abnormal": False,
            "reason": "",
        }
        res = client.post("/api/telemetry", json=packet)
        assert res.status_code == 200
        data = res.json()
        assert data["sensorHealth"]["imu_ready"] is False
        assert data["aiRisk"]["ready"] is False
        assert any("Paused:" in m for m in data["aiRisk"]["markers"])


def test_aaos_clinical_knee_angles(tmp_path: Path):
    """AAOS clinical knee goniometry limits:

    - Hyperextension: < -10.0°
    - Normal adult flexion ROM: 0° to 135.0°
    - Hyperflexion: > 135.0°
    """
    app = create_app(db_path=tmp_path / "aaos.db", model_path=tmp_path / "aaos.joblib")
    with TestClient(app) as client:
        # Normal standing angle (0° to 5°)
        norm = client.post("/api/biomechanics/stimulate", json={"knee_angle_deg": 5.0, "heel_fsr": 1600, "fore_fsr": 1600, "repeat": 1}).json()["frame"]
        assert norm["condition"] == "normal"

        # Deep physiological flexion (90°)
        deep_norm = client.post("/api/biomechanics/stimulate", json={"knee_angle_deg": 90.0, "heel_fsr": 1600, "fore_fsr": 1600, "repeat": 1}).json()["frame"]
        assert deep_norm["condition"] == "normal"

        # Pathological hyperextension (< -10.0°)
        hyper_ext = client.post("/api/biomechanics/stimulate", json={"knee_angle_deg": -14.0, "heel_fsr": 1600, "fore_fsr": 1600, "repeat": 1}).json()["frame"]
        assert hyper_ext["abnormal"] is True

        # Pathological hyperflexion (> 135.0°)
        hyper_flex = client.post("/api/biomechanics/stimulate", json={"knee_angle_deg": 142.0, "heel_fsr": 1600, "fore_fsr": 1600, "repeat": 1}).json()["frame"]
        assert hyper_flex["abnormal"] is True


def test_plantar_pressure_imbalance_thresholds():
    """Plantar pressure imbalance zones and thresholds (>60% alert, >30% warning, <=30% normal)."""
    # Balanced (0% imbalance)
    bal = compute_foot_pressure_metrics(1500, 1500)
    assert bal["dominant_zone"] == "Balanced"
    assert bal["imbalance_pct"] == 0.0
    assert bal["status_level"] == "ok"

    # Mild asymmetry (<30%)
    mild = compute_foot_pressure_metrics(1600, 1400)
    assert mild["status_level"] == "ok"

    # Warning asymmetry (>30%)
    warn = compute_foot_pressure_metrics(2500, 1000)
    assert warn["imbalance_pct"] >= 30.0
    assert warn["status_level"] == "warn"

    # Severe asymmetry (>60% alert)
    severe = compute_foot_pressure_metrics(3200, 400)
    assert severe["imbalance_pct"] > 60.0
    assert severe["dominant_zone"] == "Heel Overload"
    assert severe["status_level"] == "fault"


def test_varus_thrust_and_heel_impact_thresholds(tmp_path: Path):
    """Frontal plane varus angular velocity (PVV >= 28.1 deg/s) and heel impact (>= 2.5g)."""
    model = OARiskModel(tmp_path / "thrust.joblib")
    model.ensure_ready()
    analyzer = GaitWindowAnalyzer(model=model, window_size=20, step_interval=5)

    snap = None
    for i in range(20):
        # Inject roll jump: 4.5 deg in 0.1s sample -> 45 deg/s > 28.1 deg/s threshold
        roll = 4.5 if i == 14 else 0.0
        accel = 2.8 if i == 14 else 1.0
        snap = analyzer.add_sample(
            knee_angle_c=25.0 + 15.0 * math.sin(i * 0.4),
            flex_norm=0.4,
            fsr_heel=2300,
            fsr_fore=1100,
            fsr_imbalance=35.0,
            net_roll_deg=roll,
            shin_accel_g=accel,
        )

    assert snap is not None
    assert snap.ready is True
    assert snap.features.varus_vel_dps >= 28.1
    assert snap.features.peak_impact_g >= 2.5
    assert any("Varus Thrust" in m for m in snap.markers)


def test_who_bmi_and_acr_age_classification():
    """Universal WHO BMI categories and ACR age >= 45 prior assessment."""
    # Underweight (< 18.5) -> height 175cm, weight 50kg -> BMI 16.3
    under = compute_bmi_summary(175.0, 50.0)
    assert under["category"] == "Underweight"
    assert under["oa_load_modifier"] == "Low"

    # Normal (18.5 - 24.9) -> height 175cm, weight 68kg -> BMI 22.2
    normal = compute_bmi_summary(175.0, 68.0)
    assert normal["category"] == "Normal"
    assert normal["oa_load_modifier"] == "Low"

    # Overweight (25.0 - 29.9) -> height 175cm, weight 82kg -> BMI 26.8
    over = compute_bmi_summary(175.0, 82.0)
    assert over["category"] == "Overweight"
    assert over["oa_load_modifier"] == "Moderate"

    # Obese (>= 30.0) -> height 175cm, weight 100kg -> BMI 32.7
    obese = compute_bmi_summary(175.0, 100.0)
    assert "Obese" in obese["category"]
    assert obese["oa_load_modifier"] == "Elevated"

    # ACR Age gate is 45 years
    assert settings.acr_age_threshold == 45


def test_sensor_rail_diagnostics():
    """Sensor rail bound detection (12-bit ADC [0..4095] and IMU rail [0.25g..4.5g])."""
    # 1. Normal sensors
    normal_telemetry = TelemetryIn(
        timestamp=100,
        thigh=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        shin=IMUSensorData(online=True, ax=0.0, ay=0.98, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        flex=1800,
        fsrHeel=1500,
        fsrFore=1400,
    )
    normal_health = evaluate_sensor_health(normal_telemetry, source="esp32")
    assert normal_health["all_ok"] is True
    assert len(normal_health["issues"]) == 0

    # 2. Flex open circuit / disconnected (flex <= 15)
    open_telemetry = TelemetryIn(
        timestamp=200,
        thigh=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        shin=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        flex=10,
        fsrHeel=1500,
        fsrFore=1400,
    )
    open_health = evaluate_sensor_health(open_telemetry, source="esp32")
    assert open_health["all_ok"] is False
    assert any("open circuit" in issue.lower() or "disconnected" in issue.lower() for issue in open_health["issues"])

    # 3. Flex 3.3V rail saturation (flex >= 4085)
    saturated_telemetry = TelemetryIn(
        timestamp=300,
        thigh=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        shin=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        flex=4090,
        fsrHeel=1500,
        fsrFore=1400,
    )
    sat_health = evaluate_sensor_health(saturated_telemetry, source="esp32")
    assert sat_health["all_ok"] is False
    assert any("saturated at 3.3v rail" in issue.lower() for issue in sat_health["issues"])

    # 4. FSR rail saturation (fsr >= 4088)
    fsr_telemetry = TelemetryIn(
        timestamp=400,
        thigh=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        shin=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        flex=1800,
        fsrHeel=4092,
        fsrFore=1400,
    )
    fsr_health = evaluate_sensor_health(fsr_telemetry, source="esp32")
    assert fsr_health["all_ok"] is False
    assert any("heel fsr" in issue.lower() and "saturated" in issue.lower() for issue in fsr_health["issues"])

    # 5. IMU rail bound (<0.25g freefall or >4.5g shock)
    imu_low_telemetry = TelemetryIn(
        timestamp=500,
        thigh=IMUSensorData(online=True, ax=0.0, ay=0.10, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        shin=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        flex=1800,
        fsrHeel=1500,
        fsrFore=1400,
    )
    imu_health = evaluate_sensor_health(imu_low_telemetry, source="esp32")
    assert imu_health["all_ok"] is False
    assert any("thigh imu" in issue.lower() and "out of range" in issue.lower() for issue in imu_health["issues"])


def test_seated_static_posture_and_deflection_tolerance(tmp_path: Path):
    """Static seated/standing rest must stay Low Risk (not false Critical), and minor +/-3.5 deg deflection is tolerated."""
    app = create_app(db_path=tmp_path / "seated.db", model_path=tmp_path / "seated.joblib")
    with TestClient(app) as client:
        # 20 calm seated frames (~90 deg bent, balanced feet) -> Low Risk + Seated category
        last = None
        for i in range(20):
            res = client.post("/api/biomechanics/stimulate", json={
                "knee_angle_deg": 90.0 + (0.4 if i % 2 == 0 else -0.4),
                "thigh_pitch_deg": 78.0,
                "net_roll_deg": 1.0,
                "heel_fsr": 900,
                "fore_fsr": 880,
                "flex_stiffness": 0.2,
                "repeat": 1,
            })
            assert res.status_code == 200
            last = res.json()["frame"]

        assert last is not None
        assert last["condition"] == "normal"
        assert last["movementCategory"] == "Seated / Bent Rest"
        assert last["aiRisk"]["ready"] is True
        assert last["aiRisk"]["oa_risk_tier"] == "Low"
        assert last["aiRisk"]["oa_risk_score"] < 35.0
        assert last["hardwareState"] == {
            "condition": "normal",
            "greenLed": "ON",
            "redLed": "OFF",
            "buzzer": "OFF",
        }

        # Minor -12.5 deg strap/sensor deflection within +/-3.5 deg tolerance stays Normal
        minor = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": -12.5,
            "thigh_pitch_deg": 0.0,
            "net_roll_deg": 1.0,
            "heel_fsr": 1600,
            "fore_fsr": 1550,
            "flex_stiffness": 0.85,
            "repeat": 1,
        }).json()["frame"]
        assert minor["condition"] == "normal"
        assert minor["abnormal"] is False
        assert minor["movementCategory"] == "Standing Neutral"

        # Moderate watch state (e.g. ~43% FSR asymmetry in 31-60% band) -> Dim ("less green") LED, Red & Buzzer OFF
        mod = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 30.0,
            "thigh_pitch_deg": 10.0,
            "net_roll_deg": 1.0,
            "heel_fsr": 2500,
            "fore_fsr": 1000,
            "flex_stiffness": 0.25,
            "repeat": 1,
        }).json()["frame"]
        assert mod["condition"] == "moderate"
        assert mod["abnormal"] is False
        assert mod["hardwareState"] == {
            "condition": "moderate",
            "greenLed": "DIM",
            "redLed": "OFF",
            "buzzer": "OFF",
        }


def test_static_standing_and_bent_rest_with_heel_rail_not_false_critical(tmp_path: Path):
    """Calm standing or ~46.5 deg seated bend with near-rail Heel FSR must stay Moderate (never false Critical)."""
    app = create_app(db_path=tmp_path / "rail.db", model_path=tmp_path / "rail.joblib")
    with TestClient(app) as client:
        for i in range(18):
            pkt = {
                "type": "data",
                "timestamp": 1000 + i * 100,
                "calibrated": True,
                "calibrationStep": 2,
                "thigh": {"online": True, "ax": 0.0, "ay": 0.99, "az": 0.05, "gx": 0.1, "gy": 0.1, "gz": 0.0, "pitch": 1.0, "roll": 0.0, "pitchC": 1.0, "rollC": 0.0},
                "shin": {"online": True, "ax": 0.0, "ay": 0.99, "az": 0.05, "gx": 0.1, "gy": 0.1, "gz": 0.0, "pitch": 2.0, "roll": -2.0, "pitchC": 2.0, "rollC": -2.0},
                "flex": 410,
                "flexNorm": 0.0,
                "fsrHeel": 4010,
                "fsrFore": 220,
                "kneeAngle": 1.0,
                "kneeAngleC": 1.0 + (0.2 if i % 2 == 0 else -0.2),
                "fsrImbalance": 89.6,
                "abnormal": True,
                "reason": "Gait imbalance (>60%)",
            }
            assert client.post("/api/telemetry", json=pkt).status_code == 200
        last_stand = client.get("/api/telemetry/live?limit=1").json()["frames"][-1]
        assert last_stand["critical"] is False
        assert last_stand["condition"] == "moderate"
        assert last_stand["aiRisk"]["oa_risk_tier"] != "High"
        assert last_stand["hardwareState"]["redLed"] == "OFF"

        # Calm ~46.5 deg seated hold with uncalibrated flex (0.20) + heel rail
        for i in range(42):
            pkt = {
                "type": "data",
                "timestamp": 3000 + i * 100,
                "calibrated": True,
                "calibrationStep": 2,
                "thigh": {"online": True, "ax": 0.5, "ay": 0.8, "az": 0.05, "gx": 0.2, "gy": 0.1, "gz": 0.0, "pitch": 38.0, "roll": -6.0, "pitchC": 38.0, "rollC": -6.0},
                "shin": {"online": True, "ax": 0.1, "ay": 0.98, "az": 0.05, "gx": 0.2, "gy": 0.1, "gz": 0.0, "pitch": 8.0, "roll": -19.0, "pitchC": 84.5, "rollC": -19.0},
                "flex": 201,
                "flexNorm": 0.20,
                "fsrHeel": 3945,
                "fsrFore": 8,
                "kneeAngle": 46.5,
                "kneeAngleC": 46.5 + (0.2 if i % 2 == 0 else -0.2),
                "fsrImbalance": 99.6,
                "abnormal": True,
                "reason": "Knee stiffness detected | Gait imbalance (>60%)",
            }
            assert client.post("/api/telemetry", json=pkt).status_code == 200
        last_bent = client.get("/api/telemetry/live?limit=1").json()["frames"][-1]
        assert last_bent["critical"] is False
        assert last_bent["condition"] == "moderate"
        assert last_bent["aiRisk"]["oa_risk_tier"] != "High"
        assert last_bent["hardwareState"]["redLed"] == "OFF"

