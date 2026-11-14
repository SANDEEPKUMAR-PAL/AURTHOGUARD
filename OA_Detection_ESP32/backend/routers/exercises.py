"""Guided OA detection exercise catalog and trial recording endpoints."""

import csv
import io
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response

from backend.config import settings
from backend.exercises import get_localized_catalog
from backend.routers import get_manager
from backend.schemas import ExerciseStartRequest, ExerciseTrialOut, LanguageSelectRequest

router = APIRouter(prefix="/api/exercises", tags=["exercises"])


@router.get("/catalog")
async def get_exercise_catalog(request: Request, lang: Optional[str] = Query(default=None)):
    mgr = get_manager(request)
    return get_localized_catalog(lang or mgr.language)


@router.post("/language")
async def set_exercise_language(payload: LanguageSelectRequest, request: Request):
    mgr = get_manager(request)
    lang = await mgr.set_language(payload.lang)
    return {"language": lang, "catalog": get_localized_catalog(lang)}


@router.get("/trials", response_model=list[ExerciseTrialOut])
async def list_exercise_trials(request: Request, patient_id: Optional[int] = Query(default=None)):
    mgr = get_manager(request)
    return await mgr.db.list_exercise_trials(patient_id=patient_id)


@router.get("/trials/export/csv")
async def export_exercise_trials_csv(request: Request, patient_id: Optional[int] = Query(default=None)):
    mgr = get_manager(request)
    trials = await mgr.db.list_exercise_trials(patient_id=patient_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "trial_id", "patient_id", "session_id", "monitored_leg", "exercise_key", "exercise_name",
        "status", "completed_reps", "target_reps", "quality_score", "finding",
        "avg_rom_deg", "min_flexion_deg", "max_flexion_deg", "avg_imbalance_pct",
        "roll_wobble_deg", "flex_agreement_pct", "markers", "started_at", "ended_at",
    ])
    for t in trials:
        writer.writerow([
            t["id"], t["patient_id"], t["session_id"], t.get("monitored_leg", "Left"), t["exercise_key"], t["exercise_name"],
            t["status"], t["completed_reps"], t["target_reps"], t["quality_score"], t["finding"],
            t["avg_rom_deg"], t["min_flexion_deg"], t["max_flexion_deg"], t["avg_imbalance_pct"],
            t["roll_wobble_deg"], t["flex_agreement_pct"], "; ".join(t.get("markers", [])),
            settings.format_timestamp_tz(t["started_at"]),
            settings.format_timestamp_tz(t["ended_at"]),
        ])
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="OA_Exercise_Trials.csv"'},
    )


@router.post("/start", status_code=status.HTTP_201_CREATED)
async def start_exercise(payload: ExerciseStartRequest, request: Request):
    mgr = get_manager(request)
    if payload.patient_id is not None and not await mgr.db.get_patient(payload.patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    return await mgr.start_exercise(
        exercise_key=payload.exercise_key,
        patient_id=payload.patient_id,
        monitored_leg=payload.monitored_leg,
        lang=payload.lang,
        auto_simulate=payload.auto_simulate,
    )


@router.post("/stop", response_model=ExerciseTrialOut)
async def stop_exercise(request: Request):
    mgr = get_manager(request)
    completed = await mgr.stop_exercise()
    if not completed:
        raise HTTPException(status_code=404, detail="No active exercise trial")
    return completed


@router.delete("/trials/{trial_id}")
async def delete_exercise_trial(trial_id: int, request: Request):
    mgr = get_manager(request)
    deleted = await mgr.db.delete_exercise_trial(trial_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Exercise trial not found")
    return {"deleted": True, "id": trial_id}

