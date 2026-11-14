"""Tests for foot-pressure meter calculations and interactive joint/foot stimulation endpoints."""

from pathlib import Path

from fastapi.testclient import TestClient

from backend.biomechanics import STIMULATION_PRESETS, compute_foot_pressure_metrics
from backend.main import create_app


def test_foot_pressure_metrics_zones_tare_and_needle():
    unloaded = compute_foot_pressure_metrics(20, 30)
    assert unloaded["dominant_zone"] == "Unloaded"
    assert unloaded["meter_needle_deg"] == 0.0

    balanced = compute_foot_pressure_metrics(1700, 1700)
    assert balanced["dominant_zone"] == "Balanced"
    assert balanced["imbalance_pct"] == 0.0
    assert balanced["cop_ratio"] == 0.5
    assert balanced["meter_needle_deg"] == 0.0

    heel_overload = compute_foot_pressure_metrics(3300, 400, heel_tare=100, fore_tare=0, imbalance_thresh=60.0)
    assert heel_overload["heel_adj"] == 3200
    assert heel_overload["dominant_zone"] == "Heel Overload"
    assert heel_overload["status_level"] == "fault"
    assert heel_overload["meter_needle_deg"] < -45.0

    fore_overload = compute_foot_pressure_metrics(400, 3200, imbalance_thresh=60.0)
    assert fore_overload["dominant_zone"] == "Forefoot Overload"
    assert fore_overload["meter_needle_deg"] > 45.0


def test_biomechanics_stimulation_and_foot_pressure_api(tmp_path: Path):
    app = create_app(db_path=tmp_path / "bio.db", model_path=tmp_path / "bio.joblib")
    with TestClient(app) as client:
        presets = client.get("/api/biomechanics/stimulation/presets")
        assert presets.status_code == 200
        assert len(presets.json()["presets"]) == len(STIMULATION_PRESETS)

        # Single hyperflexion + heel overload stimulus
        res = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 142.0,
            "thigh_pitch_deg": 55.0,
            "net_roll_deg": 7.5,
            "heel_fsr": 3200,
            "fore_fsr": 400,
            "repeat": 1,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["frame"]["abnormal"] is True
        assert body["foot_pressure"]["dominant_zone"] == "Heel Overload"
        assert body["sensorHealth"]["imu_ready"] is True

        # Verify expected LED & Buzzer hardware states across Normal / Abnormal / Critical conditions
        norm = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 40.0, "thigh_pitch_deg": 10.0, "net_roll_deg": 1.0,
            "heel_fsr": 1700, "fore_fsr": 1680, "repeat": 1,
        }).json()["frame"]
        assert norm["condition"] == "normal"
        assert norm["hardwareState"] == {"condition": "normal", "greenLed": "ON", "redLed": "OFF", "buzzer": "OFF"}

        abn = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 40.0, "thigh_pitch_deg": 10.0, "net_roll_deg": 1.0,
            "heel_fsr": 3200, "fore_fsr": 300, "repeat": 1,
        }).json()["frame"]
        assert abn["condition"] == "abnormal"
        assert abn["hardwareState"] == {"condition": "abnormal", "greenLed": "OFF", "redLed": "ON", "buzzer": "ON"}

        crit = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 146.0, "thigh_pitch_deg": 40.0, "net_roll_deg": 9.0,
            "heel_fsr": 3300, "fore_fsr": 250, "repeat": 1,
        }).json()["frame"]
        assert crit["condition"] == "critical"
        assert crit["critical"] is True
        assert crit["hardwareState"] == {"condition": "critical", "greenLed": "OFF", "redLed": "ON", "buzzer": "ON"}

        # 16-frame burst immediately drives AI Risk evaluation & critical hardware alert
        burst = client.post("/api/biomechanics/stimulate", json={
            "knee_angle_deg": 22.0,
            "thigh_pitch_deg": 8.0,
            "net_roll_deg": 9.5,
            "heel_fsr": 3250,
            "fore_fsr": 380,
            "flex_stiffness": 0.72,
            "repeat": 16,
        })
        assert burst.status_code == 200
        b_data = burst.json()
        assert b_data["injected"] == 16
        assert b_data["aiRisk"]["ready"] is True
        assert b_data["frame"]["condition"] == "critical"
        assert b_data["frame"]["hardwareState"] == {"condition": "critical", "greenLed": "OFF", "redLed": "ON", "buzzer": "ON"}

        fp_status = client.get("/api/biomechanics/foot-pressure")
        assert fp_status.status_code == 200
        assert fp_status.json()["latest"]["dominant_zone"] == "Heel Overload"
        assert len(fp_status.json()["history"]) >= 16

        alerts = client.get("/api/alerts").json()
        assert any(a["severity"] == "critical" for a in alerts)

