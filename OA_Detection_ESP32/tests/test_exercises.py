"""Tests for guided OA detection exercises, multi-sensor rep counting, and SQLite trial persistence."""

import math
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.exercises import EXERCISE_CATALOG, ExerciseTracker
from backend.main import create_app


def test_exercise_tracker_counts_reps_for_flexion_and_weight_shift():
    tracker = ExerciseTracker()
    tracker.start(trial_id=1, exercise_key="seated_flex_ext")

    # Simulate 3 full 0° -> 105° -> 0° reps
    for i in range(60):
        angle = 55.0 - 52.0 * math.cos(i * 0.35)
        snap = tracker.update(
            knee_angle_c=angle,
            flex_norm=angle / 90.0,
            thigh_roll_c=1.5,
            shin_roll_c=2.0,
            fsr_heel=500,
            fsr_fore=500,
            fsr_imbalance=0.0,
        )
    assert snap["completed_reps"] >= 2
    assert snap["finding"] == "Normal"
    assert snap["quality_score"] >= 72.0

    # Weight shift rep counting
    ws_tracker = ExerciseTracker()
    ws_tracker.start(trial_id=2, exercise_key="weight_shift")
    for i in range(40):
        heel = 3000 if (i % 10) < 5 else 400
        fore = 400 if (i % 10) < 5 else 3000
        snap_ws = ws_tracker.update(
            knee_angle_c=4.0,
            flex_norm=0.05,
            thigh_roll_c=1.0,
            shin_roll_c=1.0,
            fsr_heel=heel,
            fsr_fore=fore,
            fsr_imbalance=75.0,
        )
    assert snap_ws["completed_reps"] >= 3


def test_exercises_api_and_sqlite_persistence(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "ex_oa.db",
        model_path=tmp_path / "ex_model.joblib",
    )
    with TestClient(app) as client:
        cat = client.get("/api/exercises/catalog")
        assert cat.status_code == 200
        assert len(cat.json()) == len(EXERCISE_CATALOG)

        pid = client.post("/api/patients", json={"name": "Rohan Gogoi", "age": 60}).json()["id"]

        start = client.post("/api/exercises/start", json={
            "exercise_key": "sit_to_stand",
            "patient_id": pid,
            "auto_simulate": True,
        })
        assert start.status_code == 201
        time.sleep(1.2)

        stop = client.post("/api/exercises/stop")
        assert stop.status_code == 200
        trial = stop.json()
        assert trial["status"] == "completed"
        assert trial["patient_id"] == pid
        assert trial["avg_rom_deg"] > 20.0
        assert trial["finding"] in ("Normal", "Mild Deficit", "High Risk Marker")

        trials = client.get(f"/api/exercises/trials?patient_id={pid}").json()
        assert len(trials) == 1
        assert trials[0]["id"] == trial["id"]

        csv_trials = client.get("/api/exercises/trials/export/csv")
        assert csv_trials.status_code == 200
        assert "sit_to_stand" in csv_trials.text

        client.post("/api/device/simulator/stop")

