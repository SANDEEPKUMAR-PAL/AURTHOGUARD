"""Tests for sensor health guard, Hindi/English i18n, bilateral & NER analytics, migration, and offline assets."""

import math
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.exercises import ExerciseTracker
from backend.main import create_app


def _sample_packet(
    ts: int,
    knee_c: float,
    flex: int = 1900,
    heel: int = 1200,
    fore: int = 1200,
    thigh_online: bool = True,
    shin_online: bool = True,
) -> dict:
    total = heel + fore
    imb = abs(heel - fore) / total * 100.0 if total > 100 else 0.0
    return {
        "type": "data",
        "timestamp": ts,
        "calibrated": True,
        "calibrationStep": 2,
        "thigh": {
            "online": thigh_online,
            "ax": 0.05, "ay": 0.98, "az": 0.02,
            "gx": 4.0, "gy": 1.0, "gz": 0.5,
            "pitch": 0.0, "roll": 1.0, "pitchC": 0.0, "rollC": 1.0,
        },
        "shin": {
            "online": shin_online,
            "ax": 0.08, "ay": 0.96, "az": 0.04,
            "gx": 8.0, "gy": 1.5, "gz": 0.5,
            "pitch": knee_c, "roll": 1.5, "pitchC": knee_c, "rollC": 1.5,
        },
        "flex": flex,
        "flexNorm": max(0.0, min(1.5, knee_c / 90.0)),
        "fsrHeel": heel,
        "fsrFore": fore,
        "kneeAngle": knee_c,
        "kneeAngleC": knee_c,
        "fsrImbalance": imb,
        "abnormal": False,
        "reason": "",
    }


def test_sensor_health_guard_pauses_ai_when_imu_offline(tmp_path: Path):
    app = create_app(db_path=tmp_path / "sh.db", model_path=tmp_path / "sh.joblib")
    with TestClient(app) as client:
        # Offline thigh IMU + disconnected flex should flag issues and pause AI
        bad = client.post(
            "/api/telemetry",
            json=_sample_packet(100, 10.0, flex=0, thigh_online=False, shin_online=True),
        )
        assert bad.status_code == 200
        body = bad.json()
        assert body["sensorHealth"]["imu_ready"] is False
        assert body["sensorHealth"]["all_ok"] is False
        assert len(body["sensorHealth"]["issues"]) >= 2
        assert body["aiRisk"]["ready"] is False
        assert any("Paused:" in m for m in body["aiRisk"]["markers"])

        # Feed 18 healthy gait frames to warm up AI
        last_ai = None
        for i in range(18):
            angle = 30.0 + 28.0 * math.sin(i * 0.45)
            r = client.post(
                "/api/telemetry",
                json=_sample_packet(200 + i * 100, angle, flex=1850),
            )
            assert r.status_code == 200
            last_ai = r.json()
        assert last_ai["sensorHealth"]["all_ok"] is True
        assert last_ai["aiRisk"]["ready"] is True
        feats = last_ai["aiRisk"]["features"]
        assert feats["cadence_spm"] > 0.0
        assert feats["stance_pct"] > 0.0
        assert feats["peak_impact_g"] >= 0.9
        prev_samples = last_ai["aiRisk"]["window_samples"]

        # Unplug shin IMU -> AI freezes sample count & marks ready=False
        dropped = client.post(
            "/api/telemetry",
            json=_sample_packet(5000, 125.0, flex=2000, thigh_online=True, shin_online=False),
        ).json()
        assert dropped["sensorHealth"]["imu_ready"] is False
        assert dropped["aiRisk"]["ready"] is False
        assert dropped["aiRisk"]["window_samples"] == prev_samples
        assert dropped["aiRisk"]["markers"][0].startswith("Paused:")


def test_varus_thrust_velocity_and_heel_impact_markers(tmp_path: Path):
    from backend.ai_engine import GaitWindowAnalyzer, OARiskModel

    model = OARiskModel(tmp_path / "vt.joblib")
    model.ensure_ready()
    analyzer = GaitWindowAnalyzer(model=model, window_size=20, step_interval=5)
    snap = None
    for i in range(20):
        # Sharp frontal roll spike (5 deg per 0.1s sample -> 50 deg/s >= 28.1 deg/s PVV threshold)
        net_roll = 5.0 if i == 12 else 0.0
        snap = analyzer.add_sample(
            knee_angle_c=22.0 + 18.0 * math.sin(i * 0.4),
            flex_norm=0.35,
            fsr_heel=2400,
            fsr_fore=900,
            fsr_imbalance=45.0,
            net_roll_deg=net_roll,
            shin_accel_g=2.6 if i == 12 else 1.05,
        )
    assert snap is not None and snap.ready is True
    assert snap.features.varus_vel_dps >= 28.1
    assert snap.features.peak_impact_g >= 2.5
    assert any("Varus Thrust" in m for m in snap.markers)


def test_bilingual_hindi_english_exercises_and_api(tmp_path: Path):
    tracker = ExerciseTracker()
    snap_init = tracker.start(trial_id=1, exercise_key="seated_flex_ext", lang="hi", monitored_leg="Right")
    assert snap_init["lang"] == "hi"
    assert snap_init["monitored_leg"] == "Right"
    assert "घुटना" in snap_init["exercise_name"]

    for i in range(50):
        angle = 55.0 - 52.0 * math.cos(i * 0.35)
        snap = tracker.update(angle, angle / 90.0, 1.0, 1.2, 500, 500, 0.0)
    assert snap["finding"] == "Normal"
    assert snap["finding_label"] == "सामान्य"
    assert snap["monitored_leg"] == "Right"

    app = create_app(db_path=tmp_path / "i18n.db", model_path=tmp_path / "i18n.joblib")
    with TestClient(app) as client:
        cat_hi = client.get("/api/exercises/catalog?lang=hi")
        assert cat_hi.status_code == 200
        assert cat_hi.json()[0]["lang"] == "hi"

        sw = client.post("/api/exercises/language", json={"lang": "hi"})
        assert sw.status_code == 200
        assert sw.json()["language"] == "hi"
        assert sw.json()["catalog"][1]["name"] != "Seated Flexion–Extension Sweep"

        start = client.post("/api/exercises/start", json={
            "exercise_key": "mini_squat",
            "monitored_leg": "Right",
            "lang": "hi",
            "auto_simulate": False,
        })
        assert start.status_code == 201
        assert start.json()["trial"]["monitored_leg"] == "Right"
        assert start.json()["snapshot"]["lang"] == "hi"
        client.post("/api/exercises/stop")


def test_analytics_bilateral_longitudinal_bmi_ner_and_html_report(tmp_path: Path):
    app = create_app(db_path=tmp_path / "an.db", model_path=tmp_path / "an.joblib")
    with TestClient(app) as client:
        p1 = client.post("/api/patients", json={
            "name": "Lakshmi Devi",
            "age": 62,
            "gender": "Female",
            "monitored_leg": "Left",
            "height_cm": 160.0,
            "weight_kg": 80.0,
            "region_district": "Imphal West",
        }).json()["id"]
        client.post("/api/patients", json={
            "name": "Bikash Das",
            "age": 48,
            "gender": "Male",
            "monitored_leg": "Right",
            "region_district": "Kamrup",
        })

        # Visit 1: Left leg (restricted stiff gait)
        s1 = client.post("/api/sessions/start", json={"patient_id": p1, "monitored_leg": "Left"}).json()["id"]
        for i in range(20):
            angle = 18.0 + 10.0 * math.sin(i * 0.4)
            client.post("/api/telemetry", json=_sample_packet(100 + i * 100, angle, flex=1500, heel=2600, fore=400))
        client.post(f"/api/sessions/{s1}/stop", json={})

        # Visit 2: Left leg (improved healthier ROM)
        s2 = client.post("/api/sessions/start", json={"patient_id": p1, "monitored_leg": "Left"}).json()["id"]
        for i in range(20):
            angle = 32.0 + 30.0 * math.sin(i * 0.4)
            client.post("/api/telemetry", json=_sample_packet(3000 + i * 100, angle, flex=1900, heel=1300, fore=1250))
        client.post(f"/api/sessions/{s2}/stop", json={})

        # Visit 3: Switch active leg to Right via API & record Right leg session
        leg_res = client.post("/api/device/leg", json={"leg": "Right"})
        assert leg_res.status_code == 200
        assert leg_res.json()["active_leg"] == "Right"
        s3 = client.post("/api/sessions/start", json={"patient_id": p1}).json()
        assert s3["monitored_leg"] == "Right"
        for i in range(20):
            angle = 35.0 + 32.0 * math.sin(i * 0.4)
            client.post("/api/telemetry", json=_sample_packet(6000 + i * 100, angle, flex=1950, heel=1400, fore=1380))
        client.post(f"/api/sessions/{s3['id']}/stop", json={})

        an = client.get(f"/api/analytics/patients/{p1}").json()
        assert an["bmi_summary"]["category"].startswith("Obese")
        assert an["bmi_summary"]["oa_load_modifier"] == "Elevated"
        assert an["bilateral_comparison"]["available"] is True
        assert an["bilateral_comparison"]["left"]["session_count"] == 2
        assert an["bilateral_comparison"]["right"]["session_count"] == 1
        assert an["bilateral_comparison"]["rom_asymmetry_deg"] is not None
        assert an["longitudinal_trend"]["visit_count"] == 3

        rep_json = client.get(f"/api/analytics/patients/{p1}/report").json()
        assert rep_json["patient"]["name"] == "Lakshmi Devi"
        assert len(rep_json["recommendations"]) >= 1

        rep_html = client.get(f"/api/analytics/patients/{p1}/report/html")
        assert rep_html.status_code == 200
        assert "Lakshmi Devi" in rep_html.text
        assert "window.print()" in rep_html.text

        # WHO Universal BMI (Normal 24.0, Overweight 26.6) & ACR 45yr screening check
        p_norm = client.post("/api/patients", json={
            "name": "Meera Nair",
            "age": 45,
            "height_cm": 160.0,
            "weight_kg": 61.5,  # BMI 24.0 -> Normal under WHO universal cutoffs
            "region_district": "Shillong",
        }).json()["id"]
        norm_an = client.get(f"/api/analytics/patients/{p_norm}").json()
        assert norm_an["bmi_summary"]["category"] == "Normal"
        assert any("45 yrs" in r for r in norm_an["recommendations"])

        p_ow = client.post("/api/patients", json={
            "name": "Rameshwar Bora",
            "age": 52,
            "height_cm": 160.0,
            "weight_kg": 68.0,  # BMI 26.6 -> Overweight under WHO universal cutoffs
            "region_district": "Shillong",
        }).json()["id"]
        assert client.get(f"/api/analytics/patients/{p_ow}").json()["bmi_summary"]["category"] == "Overweight"

        refs = client.get("/api/analytics/references")
        assert refs.status_code == 200
        assert len(refs.json()["references"]) >= 8

        ner = client.get("/api/analytics/ner-summary").json()
        assert ner["total_patients"] == 4
        assert ner["total_completed_sessions"] == 3
        districts = {d["district"]: d for d in ner["districts"]}
        assert "Imphal West" in districts
        assert districts["Imphal West"]["session_count"] == 3
        assert "Kamrup" in districts


def test_sqlite_migration_adds_monitored_leg_and_static_chartjs(tmp_path: Path):
    legacy_db = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            patient_name TEXT,
            status TEXT,
            start_time TEXT,
            sample_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE exercise_trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            session_id INTEGER,
            exercise_key TEXT,
            status TEXT,
            started_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=legacy_db, model_path=tmp_path / "mig.joblib")
    with TestClient(app) as client:
        static_res = client.get("/static/chart.umd.min.js")
        assert static_res.status_code == 200
        assert len(static_res.content) > 50000
        assert "Chart" in static_res.text

        boot = client.get("/api/dashboard/bootstrap")
        assert boot.status_code == 200
        assert "active_leg" in boot.json()["status"]
        assert "language" in boot.json()["status"]

    conn2 = sqlite3.connect(legacy_db)
    sess_cols = {r[1] for r in conn2.execute("PRAGMA table_info(sessions)").fetchall()}
    ex_cols = {r[1] for r in conn2.execute("PRAGMA table_info(exercise_trials)").fetchall()}
    conn2.close()
    assert "monitored_leg" in sess_cols
    assert "monitored_leg" in ex_cols


def test_session_history_auto_maintenance_notes_and_enhanced_reports(tmp_path: Path):
    import time

    db_path = tmp_path / "hist.db"
    model_path = tmp_path / "hist.joblib"
    app = create_app(db_path=db_path, model_path=model_path)
    with TestClient(app) as client:
        # 1. Auto-record defaults to True: running simulator automatically creates & stops a completed session
        assert client.get("/api/device/thresholds").json()["auto_record_on_stream"] is True
        pid = client.post("/api/patients", json={
            "name": "Asha Devi",
            "age": 58,
            "gender": "Female",
            "monitored_leg": "Left",
            "height_cm": 156.0,
            "weight_kg": 70.0,
            "region_district": "Kamrup",
        }).json()["id"]
        client.post(f"/api/patients/{pid}/select")

        assert client.post("/api/device/simulator/start", json={"profile": "early_oa", "rate_hz": 20.0}).status_code == 200
        time.sleep(0.9)
        assert client.post("/api/device/simulator/stop").status_code == 200

        sessions = client.get(f"/api/sessions?patient_id={pid}").json()
        assert len(sessions) == 1
        sid = sessions[0]["id"]
        assert sessions[0]["status"] == "completed"
        assert sessions[0]["sample_count"] >= 5

        # 2. Updating clinician notes & inspecting single-session report works for registered + walk-in
        patched = client.patch(f"/api/sessions/{sid}", json={"notes": "Observed mild varus thrust"})
        assert patched.status_code == 200
        assert patched.json()["notes"] == "Observed mild varus thrust"

        sess_rep = client.get(f"/api/sessions/{sid}/report/html")
        assert sess_rep.status_code == 200
        assert "Observed mild varus thrust" in sess_rep.text
        assert "<svg" in sess_rep.text

        # 3. Patient report JSON + HTML contain full biomechanics, sessions, alerts & SVG charts
        pat_rep = client.get(f"/api/analytics/patients/{pid}/report").json()
        assert len(pat_rep["sessions"]) == 1
        assert pat_rep["biomechanics_summary"]["sample_count"] >= 5
        assert "heel_pct" in pat_rep["biomechanics_summary"]

        pat_html = client.get(f"/api/analytics/patients/{pid}/report/html")
        assert pat_html.status_code == 200
        assert "Longitudinal OA Risk" in pat_html.text
        assert "Recorded Session History" in pat_html.text
        assert "Critical &amp; Abnormal Threshold Alerts" in pat_html.text
        assert "Clinician / Physiotherapist Signature" in pat_html.text

        # 4. Walk-in session auto-save & single-session report works without patient_id
        client.post("/api/patients/unselect")
        client.post("/api/telemetry", json=_sample_packet(9000, 145.0, heel=3300, fore=300))
        all_sessions = client.get("/api/sessions").json()
        walkin_s = [s for s in all_sessions if s["patient_id"] is None][0]
        assert walkin_s["status"] == "active"

    # Simulate abrupt crash leaving session active with sample_count=0 before restart
    c_crash = sqlite3.connect(db_path)
    c_crash.execute("UPDATE sessions SET status = 'active', end_time = NULL, sample_count = 0 WHERE id = ?", (walkin_s["id"],))
    c_crash.commit()
    c_crash.close()

    # 5. Reopening app reconciles any previously active session cleanly as completed
    app2 = create_app(db_path=db_path, model_path=model_path)
    with TestClient(app2) as client2:
        reconciled = client2.get(f"/api/sessions/{walkin_s['id']}").json()
        assert reconciled["status"] == "completed"
        assert reconciled["sample_count"] == 1
        assert client2.get(f"/api/sessions/{walkin_s['id']}/report/html").status_code == 200


def test_ist_timezone_conversion_reports_csv_and_status(tmp_path: Path):
    from backend.config import settings

    # Verify helper converts UTC (+00:00 / Z), naive legacy UTC, and already-IST strings accurately
    assert settings.display_timezone == "Asia/Kolkata"
    assert settings.display_timezone_label == "IST"
    assert settings.format_timestamp_tz("2026-11-12T00:00:00+00:00") == "2026-11-12 05:30:00 IST"
    assert settings.format_timestamp_tz("2026-11-11T19:45:10Z") == "2026-11-12 01:15:10 IST"
    assert settings.format_timestamp_tz("2026-11-12T05:30:00") == "2026-11-12 11:00:00 IST"
    assert settings.format_timestamp_tz("2026-11-12T11:30:00+05:30") == "2026-11-12 11:30:00 IST"
    assert settings.now_tz_iso().endswith("+05:30")

    db_path = tmp_path / "ist.db"
    model_path = tmp_path / "ist.joblib"
    app = create_app(db_path=db_path, model_path=model_path)
    with TestClient(app) as client:
        st = client.get("/api/device/status")
        assert st.status_code == 200
        st_json = st.json()
        assert st_json["display_timezone"] == "Asia/Kolkata"
        assert st_json["display_timezone_label"] == "IST"

        pid = client.post("/api/patients", json={
            "name": "IST Verification Patient",
            "age": 60,
            "gender": "Male",
            "monitored_leg": "Right",
            "height_cm": 170.0,
            "weight_kg": 72.0,
            "region_district": "Kamrup",
        }).json()["id"]
        client.post(f"/api/patients/{pid}/select")
        sid = client.post("/api/sessions/start", json={"patient_id": pid, "source": "test"}).json()["id"]
        assert client.post("/api/telemetry", json=_sample_packet(1000, 142.0, heel=3100, fore=400)).status_code == 200
        stop_res = client.post(f"/api/sessions/{sid}/stop")
        assert stop_res.status_code == 200
        sess_obj = stop_res.json()
        assert sess_obj["start_time"].endswith("+05:30")

        # Simulate a legacy UTC row already in DB to confirm runtime UTC->IST conversion
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE sessions SET start_time = '2026-11-11T20:00:00Z' WHERE id = ?", (sid,))
        conn.execute("UPDATE telemetry_samples SET recorded_at = '2026-11-11T20:00:05+00:00' WHERE session_id = ?", (sid,))
        conn.execute("UPDATE alerts SET created_at = '2026-11-11T20:00:06Z' WHERE session_id = ?", (sid,))
        conn.commit()
        conn.close()

        rep_json = client.get(f"/api/analytics/patients/{pid}/report").json()
        assert rep_json["generated_at"].endswith("+05:30")
        assert rep_json["timezone_label"] == "IST"

        pat_html = client.get(f"/api/analytics/patients/{pid}/report/html")
        assert pat_html.status_code == 200
        assert "Date (IST)" in pat_html.text
        assert "Timestamp (IST)" in pat_html.text
        assert "2026-11-12 01:30:00 IST" in pat_html.text
        assert "2026-11-12 01:30:06 IST" in pat_html.text
        assert "Generated" in pat_html.text and "UTC" not in pat_html.text

        sess_html = client.get(f"/api/sessions/{sid}/report/html")
        assert sess_html.status_code == 200
        assert "Date (IST)" in sess_html.text
        assert "UTC" not in sess_html.text

        sess_csv = client.get(f"/api/sessions/{sid}/export/csv")
        assert sess_csv.status_code == 200
        assert "Asia/Kolkata (IST)" in sess_csv.text
        assert "2026-11-12 01:30:05 IST" in sess_csv.text

        live_csv = client.get("/api/telemetry/export/csv")
        assert live_csv.status_code == 200
        assert "IST" in live_csv.text
