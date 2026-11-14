"""WebSocket integration tests for ESP32 push -> AI Risk enrichment -> Dashboard relay."""

from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app


def test_websocket_esp32_to_dashboard_and_calibration_relay(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "ws_oa.db",
        model_path=tmp_path / "ws_model.joblib",
    )
    with TestClient(app) as client:
        # Create and select a patient so calibration saves into SQLite automatically
        pid = client.post("/api/patients", json={"name": "Ananya Baruah", "age": 48}).json()["id"]
        client.post(f"/api/patients/{pid}/select")

        with client.websocket_connect("/ws/dashboard") as dash_ws:
            initial = dash_ws.receive_json()
            assert initial["type"] == "status"

            with client.websocket_connect("/ws/esp32") as esp_ws:
                # ESP32 receives initial thresholds on connect
                init_cmd = esp_ws.receive_json()
                assert init_cmd["cmd"] == "setThresholds"

                # Status broadcast to dashboard should now show esp32_connected=True
                st_msg = dash_ws.receive_json()
                assert st_msg["type"] == "status"
                assert st_msg["data"]["esp32_connected"] is True

                # Trigger Step 1 calibration from dashboard WS -> forwarded to ESP32
                dash_ws.send_json({"cmd": "calibrate_step1", "patient_id": pid})
                cal_cmd = esp_ws.receive_json()
                assert cal_cmd["cmd"] == "calibrate_step1"

                # ESP32 returns calResult -> backend persists to SQLite & relays to dashboard
                esp_ws.send_json({
                    "type": "calResult",
                    "step": 2,
                    "isTwoPoint": True,
                    "thighPitchOffset": -6.5,
                    "thighRollOffset": 1.2,
                    "shinPitchOffset": -4.8,
                    "shinRollOffset": 0.8,
                    "thighPolarity": 1,
                    "shinPolarity": 1,
                    "flexStraight": 2150,
                    "flexBent": 1210,
                })
                relayed_cal = dash_ws.receive_json()
                assert relayed_cal["type"] == "calResult"
                assert relayed_cal["calibration"]["flex_straight"] == 2150

                # Drain status update
                status_after_cal = dash_ws.receive_json()
                assert status_after_cal["type"] == "status"
                assert status_after_cal["data"]["calibrated"] is True

                # Send telemetry packet from ESP32 -> enriched with AI & relayed to dashboard
                esp_ws.send_json({
                    "type": "data",
                    "timestamp": 1000,
                    "calibrated": True,
                    "thigh": {"ax": 0.0, "ay": 1.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0, "pitch": -6.5, "roll": 1.2, "pitchC": 0.0, "rollC": 0.0},
                    "shin": {"ax": 0.0, "ay": 1.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0, "pitch": 40.2, "roll": 0.8, "pitchC": 45.0, "rollC": 0.0},
                    "flex": 1680,
                    "fsrHeel": 1600,
                    "fsrFore": 1500,
                    "kneeAngle": 46.7,
                    "kneeAngleC": 45.0,
                    "fsrImbalance": 3.2,
                    "abnormal": False,
                    "reason": "",
                })
                frame = dash_ws.receive_json()
                assert frame["type"] == "data"
                assert frame["kneeAngleC"] == 45.0
                assert "aiRisk" in frame

        # Verify calibration persisted to SQLite on the patient record
        cals = client.get(f"/api/patients/{pid}/calibrations").json()
        assert len(cals) == 1
        assert cals[0]["is_two_point"] is True
        assert cals[0]["flex_bent"] == 1210

