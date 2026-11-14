"""Clinical recording session, telemetry query, alert history, and CSV/JSON export endpoints."""

import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response

from backend.analytics import build_session_clinical_report, render_printable_report_html
from backend.config import settings
from backend.routers import get_manager
from backend.schemas import (
    AlertOut,
    SessionOut,
    SessionStartRequest,
    SessionStopRequest,
    SessionUpdateRequest,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionOut])
async def list_sessions(request: Request, patient_id: Optional[int] = Query(default=None)):
    mgr = get_manager(request)
    return await mgr.db.list_sessions(patient_id=patient_id)


@router.post("/start", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def start_session(payload: SessionStartRequest, request: Request):
    mgr = get_manager(request)
    if payload.patient_id is not None:
        if not await mgr.db.get_patient(payload.patient_id):
            raise HTTPException(status_code=404, detail="Patient not found")
    return await mgr.start_session(
        patient_id=payload.patient_id,
        patient_name=payload.patient_name,
        clinician_name=payload.clinician_name,
        source=payload.source,
        monitored_leg=payload.monitored_leg,
        notes=payload.notes,
    )


@router.post("/{session_id}/stop", response_model=SessionOut)
async def stop_session(session_id: int, request: Request, payload: Optional[SessionStopRequest] = None):
    mgr = get_manager(request)
    if mgr.active_session_id == session_id:
        completed = await mgr.stop_session(notes=payload.notes if payload else None)
        if completed:
            return completed

    existing = await mgr.db.get_session(session_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Session not found")
    if existing["status"] == "completed":
        return existing
    stats = await mgr.db.compute_session_stats_from_db(session_id)
    return await mgr.db.complete_session(session_id, stats, notes=payload.notes if payload else None)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: int, request: Request):
    mgr = get_manager(request)
    session = await mgr.db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/{session_id}", response_model=SessionOut)
async def update_session_notes(session_id: int, payload: SessionUpdateRequest, request: Request):
    mgr = get_manager(request)
    updated = await mgr.db.update_session_notes(session_id, payload.notes)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    await mgr.broadcast_dashboard({"type": "sessionUpdated", "action": "updated", "session": updated})
    return updated


@router.delete("/{session_id}")
async def delete_session(session_id: int, request: Request):
    mgr = get_manager(request)
    if mgr.active_session_id == session_id:
        await mgr.stop_session()
    deleted = await mgr.db.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    await mgr.broadcast_dashboard({"type": "sessionUpdated", "action": "deleted", "session_id": session_id})
    return {"deleted": True, "id": session_id}


async def _build_single_session_report_payload(session_id: int, request: Request) -> dict:
    mgr = get_manager(request)
    if mgr.active_session_id == session_id:
        await mgr.flush_telemetry_buffer()
    session = await mgr.db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    patient = await mgr.db.get_patient(session["patient_id"]) if session.get("patient_id") else None
    calibrations = await mgr.db.list_calibrations(session["patient_id"]) if session.get("patient_id") else []
    bio = await mgr.db.get_session_biomechanics_summary(session_id)
    alerts = await mgr.db.get_session_alerts(session_id)
    return build_session_clinical_report(
        session=session,
        patient=patient,
        biomechanics_summary=bio,
        alerts=alerts,
        calibration=calibrations[0] if calibrations else None,
    )


@router.get("/{session_id}/report")
async def get_session_report_json(session_id: int, request: Request):
    return await _build_single_session_report_payload(session_id, request)


@router.get("/{session_id}/report/html", response_class=HTMLResponse)
async def get_session_report_html(session_id: int, request: Request):
    report = await _build_single_session_report_payload(session_id, request)
    return HTMLResponse(content=render_printable_report_html(report))


@router.get("/{session_id}/telemetry")
async def get_session_telemetry(
    session_id: int,
    request: Request,
    limit: int = Query(default=2000, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
):
    mgr = get_manager(request)
    if not await mgr.db.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    if mgr.active_session_id == session_id:
        await mgr.flush_telemetry_buffer()
    return await mgr.db.get_session_telemetry(session_id, limit=limit, offset=offset)


@router.get("/{session_id}/alerts", response_model=list[AlertOut])
async def get_session_alerts(session_id: int, request: Request):
    mgr = get_manager(request)
    if not await mgr.db.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return await mgr.db.get_session_alerts(session_id)


@router.get("/{session_id}/export/csv")
async def export_session_csv(session_id: int, request: Request):
    mgr = get_manager(request)
    session = await mgr.db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if mgr.active_session_id == session_id:
        await mgr.flush_telemetry_buffer()
    samples = await mgr.db.get_session_telemetry(session_id, limit=50000)

    output = io.StringIO()
    output.write(f"# SIH26004 OA Risk Detection Session Export\n")
    output.write(f"# Session ID: {session['id']} | Patient: {session['patient_name']} | Status: {session['status']}\n")
    output.write(f"# Timezone: {settings.display_timezone} ({settings.display_timezone_label})\n")
    output.write(
        f"# OA Risk Score: {session.get('oa_risk_score')} ({session.get('oa_risk_tier')}) | ROM: {session.get('rom_deg')} deg\n"
    )
    writer = csv.writer(output)
    writer.writerow([
        "timestamp_ms", "recorded_at", "knee_angle_raw", "knee_angle_cal",
        "flex_raw", "flex_norm", "fsr_heel", "fsr_fore", "fsr_imbalance_pct",
        "thigh_pitch_c", "shin_pitch_c", "oa_risk_score", "abnormal", "reason",
    ])
    for s in samples:
        writer.writerow([
            s["timestamp_ms"], settings.format_timestamp_tz(s["recorded_at"]), s["knee_angle"], s["knee_angle_c"],
            s["flex_raw"], s["flex_norm"], s["fsr_heel"], s["fsr_fore"], s["fsr_imbalance"],
            s["thigh_pitch_c"], s["shin_pitch_c"], s["oa_risk_score"],
            1 if s["abnormal"] else 0, s["reason"],
        ])

    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in session["patient_name"])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="OA_Session_{session_id}_{safe_name}.csv"'},
    )


@router.get("/{session_id}/export/json")
async def export_session_json(session_id: int, request: Request):
    mgr = get_manager(request)
    session = await mgr.db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if mgr.active_session_id == session_id:
        await mgr.flush_telemetry_buffer()
    samples = await mgr.db.get_session_telemetry(session_id, limit=50000)
    alerts = await mgr.db.get_session_alerts(session_id)
    patient = await mgr.db.get_patient(session["patient_id"]) if session.get("patient_id") else None

    payload = {
        "project": "SIH26004 - AI-Assisted Early Detection System for Osteoarthritis (OA) Risk Markers",
        "session": session,
        "patient": patient,
        "alerts": alerts,
        "telemetry": samples,
    }
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="OA_Session_{session_id}.json"'},
    )

