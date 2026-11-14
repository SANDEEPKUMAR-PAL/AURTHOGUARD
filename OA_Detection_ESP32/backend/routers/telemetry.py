"""Healthcheck, dashboard bootstrap, live telemetry buffer, and HTTP WiFi ingestion endpoints."""

import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from backend import __version__
from backend.biomechanics import STIMULATION_PRESETS, compute_foot_pressure_metrics
from backend.config import settings
from backend.exercises import get_localized_catalog
from backend.routers import get_manager
from backend.schemas import TelemetryIn

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/health")
async def healthcheck(request: Request):
    mgr = get_manager(request)
    status = mgr.get_status()
    return {
        "status": "ok",
        "version": __version__,
        "esp32_connected": status.esp32_connected,
        "esp32_source": status.esp32_source,
        "ai_model_ready": mgr.ai_model.pipeline is not None,
        "active_session_id": status.active_session_id,
        "live_buffer_count": len(mgr.recent_frames),
    }


@router.get("/dashboard/bootstrap")
async def dashboard_bootstrap(request: Request, lang: Optional[str] = Query(default=None)):
    """Single-call initial state payload for all dashboard widgets."""
    mgr = get_manager(request)
    patients = await mgr.db.list_patients()
    trials = await mgr.db.list_exercise_trials()
    alerts = await mgr.db.list_recent_alerts(limit=50)
    sessions = await mgr.db.list_sessions()
    fp_latest = mgr.latest_foot_pressure or compute_foot_pressure_metrics(
        0, 0, imbalance_thresh=mgr.thresholds.fsr_imbalance_thresh
    )
    return {
        "status": mgr.get_status().model_dump(),
        "patients": patients,
        "exercises": get_localized_catalog(lang or mgr.language),
        "stimulation_presets": STIMULATION_PRESETS,
        "foot_pressure": fp_latest,
        "exercise_trials": trials[:25],
        "recent_alerts": alerts,
        "sessions": sessions[:50],
        "session_count": len(sessions),
        "live_frames": list(mgr.recent_frames)[-150:],
        "live_sample_count": len(mgr.recent_frames),
    }


@router.post("/telemetry")
async def ingest_http_telemetry(payload: TelemetryIn, request: Request):
    """Alternative WiFi HTTP POST ingestion endpoint for ESP32."""
    mgr = get_manager(request)
    remote_ip = request.client.host if request.client else None
    enriched = await mgr.handle_device_message(
        payload.model_dump(), source="http", remote_ip=remote_ip
    )
    return {
        "ok": True,
        "kneeAngleC": enriched.get("kneeAngleC"),
        "biomechanics": enriched.get("biomechanics"),
        "abnormal": enriched.get("abnormal"),
        "reason": enriched.get("reason"),
        "aiRisk": enriched.get("aiRisk"),
        "sensorHealth": enriched.get("sensorHealth"),
    }


@router.get("/telemetry/live")
async def get_live_telemetry(request: Request, limit: int = Query(default=150, ge=1, le=1000)):
    mgr = get_manager(request)
    frames = list(mgr.recent_frames)
    return {"count": len(frames), "frames": frames[-limit:]}


@router.post("/telemetry/clear")
async def clear_live_telemetry(request: Request):
    mgr = get_manager(request)
    cleared = mgr.clear_live_buffer()
    await mgr.broadcast_dashboard({"type": "bufferCleared", "cleared": cleared})
    await mgr.broadcast_status()
    return {"cleared": cleared}


@router.get("/telemetry/export/csv")
async def export_live_csv(request: Request):
    mgr = get_manager(request)
    frames = list(mgr.recent_frames)
    output = io.StringIO()
    output.write(f"# SIH26004 OA Risk Detection Live Telemetry Export\n")
    output.write(f"# Active Patient: {mgr.active_patient_name} | Exported: {settings.format_timestamp_tz(settings.now_tz_iso())}\n")
    writer = csv.writer(output)
    writer.writerow([
        "timestamp_ms", "knee_flexion_deg", "flex_adc", "flex_pct",
        "fsr_heel", "fsr_fore", "heel_pct", "fore_pct", "imbalance_pct",
        "net_roll_deg", "oa_risk_score", "oa_risk_tier", "abnormal", "reason",
    ])
    for f in frames:
        bio = f.get("biomechanics") or {}
        ai = f.get("aiRisk") or {}
        writer.writerow([
            f.get("timestamp", 0),
            f.get("kneeAngleC", f.get("kneeAngle", 0)),
            f.get("flex", 0),
            bio.get("flexPct", ""),
            f.get("fsrHeel", 0),
            f.get("fsrFore", 0),
            bio.get("heelPct", ""),
            bio.get("forePct", ""),
            f.get("fsrImbalance", 0),
            bio.get("netRollDeg", ""),
            ai.get("oa_risk_score", "") if ai.get("ready") else "",
            ai.get("oa_risk_tier", "Pending"),
            1 if f.get("abnormal") else 0,
            f.get("reason", ""),
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="OA_Live_Telemetry.csv"'},
    )


@router.get("/telemetry/export/json")
async def export_live_json(request: Request):
    mgr = get_manager(request)
    alerts = await mgr.db.list_recent_alerts(limit=100)
    payload = {
        "project": "SIH26004 - AI-Assisted Early Detection System for Osteoarthritis",
        "patient_id": mgr.active_patient_id,
        "patient_name": mgr.active_patient_name,
        "calibration": mgr.active_calibration,
        "thresholds": mgr.thresholds.model_dump(),
        "ai_latest": mgr.analyzer.latest_snapshot.model_dump(),
        "alerts": alerts,
        "telemetry": list(mgr.recent_frames),
    }
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="OA_Live_Telemetry.json"'},
    )
