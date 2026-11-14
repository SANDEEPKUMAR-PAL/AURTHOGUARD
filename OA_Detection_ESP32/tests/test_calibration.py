"""Unit tests for 2-point per-patient calibration collector and angle/flex normalization."""

from pathlib import Path

from backend.calibration import CalibrationCollector, normalize_telemetry_with_calibration
from backend.schemas import IMUSensorData, TelemetryIn


def _frame(thigh_pitch: float, shin_pitch: float, flex: int, heel: int = 1600, fore: int = 1500) -> TelemetryIn:
    return TelemetryIn(
        thigh=IMUSensorData(pitch=thigh_pitch, roll=2.0),
        shin=IMUSensorData(pitch=shin_pitch, roll=1.5),
        flex=flex,
        fsrHeel=heel,
        fsrFore=fore,
        kneeAngle=shin_pitch - thigh_pitch,
    )


def test_two_point_calibration_normalizes_inverted_polarity_and_flex_rom():
    collector = CalibrationCollector()
    collector.start(step=1, target_samples=5)

    # Step 1: Standing straight with skewed sensor mounting (-8° thigh, +11° shin, flex=2200)
    for _ in range(5):
        done, pct, res = collector.add_sample(_frame(-8.0, 11.0, 2200))
    assert done is True
    assert pct == 100
    assert res is not None
    assert res["thigh_pitch_offset"] == -8.0
    assert res["shin_pitch_offset"] == 11.0
    assert res["flex_straight"] == 2200
    assert res["is_two_point"] is False

    # Step 2: Seated 90° bend where shin happened to pitch in negative direction (-79° relative)
    collector.start(step=2, target_samples=5)
    for _ in range(5):
        done, pct, res2 = collector.add_sample(_frame(-8.0, -79.0, 1200))
    assert done is True
    assert res2 is not None
    assert res2["is_two_point"] is True
    assert res2["shin_polarity"] == -1
    assert res2["flex_bent"] == 1200

    # Verify straight posture normalizes to 0° flexion & 0.0 flex_norm
    straight_norm = normalize_telemetry_with_calibration(_frame(-8.0, 11.0, 2200), res2)
    assert straight_norm["knee_angle_c"] == 0.0
    assert straight_norm["flex_norm"] == 0.0

    # Verify 90° bend normalizes to +90° flexion & 1.0 flex_norm despite inverted shin mounting
    bent_norm = normalize_telemetry_with_calibration(_frame(-8.0, -79.0, 1200), res2)
    assert bent_norm["knee_angle_c"] == 90.0
    assert bent_norm["flex_norm"] == 1.0

    # Verify explicit unloaded FSR shoe/strap preload tare subtraction
    res_fsr = {**res2, "fsr_heel_tare": 250, "fsr_fore_tare": 100}
    fsr_norm = normalize_telemetry_with_calibration(_frame(-8.0, 11.0, 2200, heel=1250, fore=1100), res_fsr)
    assert fsr_norm["fsr_heel_adj"] == 1000
    assert fsr_norm["fsr_fore_adj"] == 1000
    assert fsr_norm["heel_pct"] == 50
    assert fsr_norm["fsr_imbalance_c"] == 0.0


def test_per_sensor_calibration_apply_endpoint_for_patient_and_walkin(tmp_path: Path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    app = create_app(db_path=tmp_path / "per_cal.db", model_path=tmp_path / "per_cal.joblib")
    with TestClient(app) as client:
        # 1) Walk-in per-sensor calibration works without selecting a patient
        w = client.post("/api/device/calibration/apply", json={
            "thigh_pitch_offset": -4.2, "thigh_roll_offset": 1.1, "thigh_polarity": 1,
            "shin_pitch_offset": 6.5, "shin_roll_offset": -0.8, "shin_polarity": -1,
            "flex_straight": 2250, "flex_bent": 1350,
            "fsr_heel_tare": 140, "fsr_fore_tare": 90,
            "is_two_point": True,
        })
        assert w.status_code == 200
        assert client.get("/api/device/status").json()["calibrated"] is True

        # 2) Registered patient per-sensor calibration persists into SQLite
        pid = client.post("/api/patients", json={"name": "Kiran Patel", "age": 49}).json()["id"]
        p_cal = client.post("/api/device/calibration/apply", json={
            "patient_id": pid,
            "thigh_pitch_offset": -3.5, "thigh_roll_offset": 0.5, "thigh_polarity": 1,
            "shin_pitch_offset": 4.0, "shin_roll_offset": 1.2, "shin_polarity": 1,
            "flex_straight": 2180, "flex_bent": 1240,
            "fsr_heel_tare": 120, "fsr_fore_tare": 80,
            "is_two_point": True,
        })
        assert p_cal.status_code == 200
        assert p_cal.json()["patient_id"] == pid
        cals = client.get(f"/api/patients/{pid}/calibrations").json()
        assert len(cals) == 1
        assert cals[0]["fsr_heel_tare"] == 120
        assert cals[0]["fsr_fore_tare"] == 80

        # 3) Instant 1-click Auto Calibrate (patient standing straight with skewed strap offsets)
        for i in range(6):
            client.post("/api/telemetry", json={
                "type": "data",
                "timestamp": 100 + i * 100,
                "thigh": {"online": True, "ax": 0.0, "ay": 1.0, "az": 0.0, "pitch": -9.5, "roll": 3.0},
                "shin": {"online": True, "ax": 0.0, "ay": 1.0, "az": 0.0, "pitch": 12.5, "roll": -2.0},
                "flex": 2140,
                "fsrHeel": 1700,
                "fsrFore": 1650,
                "kneeAngle": 22.0,
            })
        auto_res = client.post("/api/device/calibration/auto", json={"patient_id": pid, "samples": 6})
        assert auto_res.status_code == 200
        ab = auto_res.json()
        assert ab["status"] == "calibrated"
        assert ab["samples_used"] == 6
        assert ab["calibration"]["thigh_pitch_offset"] == -9.5
        assert ab["calibration"]["shin_pitch_offset"] == 12.5
        assert ab["calibration"]["flex_straight"] == 2140

        # Verify standing after auto-calibration reports 0.0° knee flexion
        post_stand = client.post("/api/telemetry", json={
            "type": "data",
            "timestamp": 900,
            "thigh": {"online": True, "ax": 0.0, "ay": 1.0, "az": 0.0, "pitch": -9.5, "roll": 3.0},
            "shin": {"online": True, "ax": 0.0, "ay": 1.0, "az": 0.0, "pitch": 12.5, "roll": -2.0},
            "flex": 2140,
            "fsrHeel": 1700,
            "fsrFore": 1650,
            "kneeAngle": 22.0,
        }).json()
        assert post_stand["kneeAngleC"] == 0.0

        # 4) Applying calibration while virtual simulator is running syncs camelCase offsets cleanly
        sim = app.state.manager.simulator
        sim.apply_calibration({
            "cmd": "applyCalibration",
            "thighPitchOffset": -5.5,
            "thighRollOffset": 1.2,
            "shinPitchOffset": 4.5,
            "shinRollOffset": -0.6,
            "thighPolarity": 1,
            "shinPolarity": -1,
            "flexStraight": 2210,
            "flexBent": 1310,
            "isTwoPoint": True,
        })
        assert sim.thigh_pitch_offset == -5.5
        assert sim.shin_polarity == -1
        assert sim.flex_straight == 2210
        assert sim.flex_bent == 1310
        assert sim.cal_step == 2

