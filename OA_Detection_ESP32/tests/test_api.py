"""REST API and SQLite persistence tests for patients, calibrations, sessions, thresholds, and simulator."""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app


def _make_sample_telemetry(i: int, abnormal: bool = False) -> dict:
    import math
    t = i * 0.1
    angle = 142.0 if abnormal else (30.0 + 28.0 * math.sin(2.5 * t))
    heel = 3400 if abnormal else int(1800 + 1200 * math.sin(2.5 * t))
    fore = 300 if abnormal else int(1800 - 1200 * math.sin(2.5 * t))
    return {
        "type": "data",
        "timestamp": i * 100,
        "calibrated": True,
        "calibrationStep": 2,
        "thigh": {"ax": 0.05, "ay": 0.98, "az": 0.02, "gx": 5.0, "gy": 1.0, "gz": 0.5, "pitch": -6.0, "roll": 1.0, "pitchC": -2.0, "rollC": 0.5},
        "shin": {"ax": 0.10, "ay": 0.95, "az": 0.05, "gx": 12.0, "gy": 2.0, "gz": 1.0, "pitch": angle - 6.0, "roll": 2.0, "pitchC": angle - 2.0, "rollC": 1.0},
        "flex": 1400,
        "flexNorm": round(angle / 90.0, 3),
        "fsrHeel": heel,
        "fsrFore": fore,
        "kneeAngle": angle,
        "kneeAngleC": angle,
        "fsrImbalance": round(abs(heel - fore) / (heel + fore) * 100.0, 1),
        "abnormal": abnormal,
        "reason": "Knee hyper-flexion (>135°)" if abnormal else "",
    }


def test_patient_calibration_session_and_export_lifecycle(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "test_oa.db",
        model_path=tmp_path / "test_model.joblib",
    )
    with TestClient(app) as client:
        # 1. Health & dashboard serving
        h = client.get("/api/health")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"
        assert h.json()["ai_model_ready"] is True

        dash = client.get("/")
        assert dash.status_code == 200
        assert "OA Risk Detection" in dash.text

        # 2. Patient CRUD
        p_res = client.post("/api/patients", json={
            "name": "Bikash Das",
            "age": 54,
            "gender": "Male",
            "monitored_leg": "Right",
            "height_cm": 170,
            "weight_kg": 72,
            "region_district": "Kamrup",
        })
        assert p_res.status_code == 201
        patient = p_res.json()
        pid = patient["id"]
        assert patient["name"] == "Bikash Das"
        assert patient["region_district"] == "Kamrup"

        assert client.patch(f"/api/patients/{pid}", json={"age": 55}).json()["age"] == 55
        assert len(client.get("/api/patients").json()) == 1

        # 3. Calibration creation & activation
        cal_res = client.post(f"/api/patients/{pid}/calibrations", json={
            "thigh_pitch_offset": -7.2,
            "thigh_roll_offset": 1.5,
            "shin_pitch_offset": -5.1,
            "shin_roll_offset": 0.9,
            "thigh_polarity": 1,
            "shin_polarity": 1,
            "flex_straight": 2120,
            "flex_bent": 1180,
            "is_two_point": True,
        })
        assert cal_res.status_code == 201
        assert cal_res.json()["is_two_point"] is True

        sel = client.post(f"/api/patients/{pid}/select")
        assert sel.status_code == 200
        assert sel.json()["active_calibration"]["flex_straight"] == 2120

        # 4. Threshold persistence
        t_put = client.put("/api/device/thresholds", json={
            "knee_angle_min": -8.0,
            "knee_angle_max": 130.0,
            "fsr_imbalance_thresh": 58.0,
            "flex_stiff_thresh": 0.28,
            "auto_record_on_stream": False,
        })
        assert t_put.status_code == 200
        assert client.get("/api/device/thresholds").json()["knee_angle_max"] == 130.0

        # 5. Start session -> HTTP telemetry ingestion -> Stop session -> Export
        s_start = client.post("/api/sessions/start", json={"patient_id": pid, "clinician_name": "Dr. Gogoi"})
        assert s_start.status_code == 201
        sid = s_start.json()["id"]

        for i in range(18):
            r = client.post("/api/telemetry", json=_make_sample_telemetry(i, abnormal=(i == 16)))
            assert r.status_code == 200

        s_stop = client.post(f"/api/sessions/{sid}/stop", json={"notes": "Walk test done"})
        assert s_stop.status_code == 200
        stopped = s_stop.json()
        assert stopped["status"] == "completed"
        assert stopped["sample_count"] == 18
        assert stopped["alert_count"] >= 1
        assert stopped["rom_deg"] is not None
        assert stopped["oa_risk_tier"] in ("Low", "Moderate", "High")

        # 6. Telemetry, alerts, CSV & JSON export
        assert len(client.get(f"/api/sessions/{sid}/telemetry").json()) == 18
        assert len(client.get(f"/api/sessions/{sid}/alerts").json()) >= 1

        csv_res = client.get(f"/api/sessions/{sid}/export/csv")
        assert csv_res.status_code == 200
        assert "knee_angle_cal" in csv_res.text

        json_res = client.get(f"/api/sessions/{sid}/export/json")
        assert json_res.status_code == 200
        assert len(json_res.json()["telemetry"]) == 18

        # 7. Dashboard bootstrap, live telemetry buffer, patient summary, live export, and alert clear
        boot = client.get("/api/dashboard/bootstrap")
        assert boot.status_code == 200
        boot_data = boot.json()
        assert boot_data["live_sample_count"] == 18
        assert len(boot_data["patients"]) == 1
        assert len(boot_data["recent_alerts"]) >= 1
        assert "risk_drivers" in boot_data["status"]["ai_latest"]

        summary = client.get(f"/api/patients/{pid}/summary")
        assert summary.status_code == 200
        assert len(summary.json()["sessions"]) == 1
        assert len(summary.json()["calibrations"]) == 1

        live_csv = client.get("/api/telemetry/export/csv")
        assert live_csv.status_code == 200
        assert "net_roll_deg" in live_csv.text

        live_json = client.get("/api/telemetry/export/json")
        assert live_json.status_code == 200
        assert len(live_json.json()["telemetry"]) == 18

        clr_alerts = client.delete("/api/alerts")
        assert clr_alerts.status_code == 200
        assert clr_alerts.json()["deleted"] >= 1
        assert client.get("/api/alerts").json() == []

        clr_buf = client.post("/api/telemetry/clear")
        assert clr_buf.status_code == 200
        assert clr_buf.json()["cleared"] == 18
        assert client.get("/api/telemetry/live").json()["count"] == 0


def test_virtual_simulator_endpoints(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "sim_oa.db",
        model_path=tmp_path / "sim_model.joblib",
    )
    with TestClient(app) as client:
        res = client.post("/api/device/simulator/start", json={"profile": "early_oa", "rate_hz": 20.0})
        assert res.status_code == 200
        time.sleep(1.0)
        st = client.get("/api/device/status").json()
        assert st["simulator_running"] is True
        assert st["esp32_connected"] is True
        assert st["ai_latest"]["ready"] is True

        stop = client.post("/api/device/simulator/stop")
        assert stop.status_code == 200
        assert client.get("/api/device/status").json()["simulator_running"] is False

        bz_test = client.post("/api/device/buzzer/test")
        assert bz_test.status_code == 200
        assert bz_test.json()["status"] in ("sent", "no_device")

        bz_mode = client.post("/api/device/buzzer/mode", json={"passive": True})
        assert bz_mode.status_code == 200
        assert bz_mode.json()["passive"] is True

