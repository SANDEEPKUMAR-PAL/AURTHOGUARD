"""Foot pressure meter telemetry and interactive joint/foot stimulation API endpoints."""

from fastapi import APIRouter, Request

from backend.biomechanics import (
    STIMULATION_PRESETS,
    build_stimulation_packet,
    compute_foot_pressure_metrics,
)
from backend.routers import get_manager
from backend.schemas import JointFootStimulationRequest

router = APIRouter(prefix="/api/biomechanics", tags=["biomechanics"])


@router.get("/foot-pressure")
async def get_foot_pressure_status(request: Request):
    """Return latest foot-pressure balance meter state and recent Center-of-Pressure history."""
    mgr = get_manager(request)
    latest = mgr.latest_foot_pressure or compute_foot_pressure_metrics(
        0, 0, imbalance_thresh=mgr.thresholds.fsr_imbalance_thresh
    )
    history = []
    for frame in list(mgr.recent_frames)[-30:]:
        fp = (frame.get("biomechanics") or {}).get("footPressure")
        if fp:
            history.append({
                "timestamp": frame.get("timestamp"),
                "heel_pct": fp.get("heel_pct", 50),
                "fore_pct": fp.get("fore_pct", 50),
                "imbalance_pct": fp.get("imbalance_pct", 0.0),
                "cop_ratio": fp.get("cop_ratio", 0.5),
            })
    return {"latest": latest, "history": history}


@router.get("/stimulation/presets")
async def list_stimulation_presets():
    """Return clinical joint-angle and plantar pressure presets for simulation."""
    return {"presets": STIMULATION_PRESETS}


@router.post("/stimulate")
async def stimulate_joint_and_foot(payload: JointFootStimulationRequest, request: Request):
    """Synthesize and ingest 1..40 joint/foot stimulation frames through the real backend pipeline."""
    mgr = get_manager(request)
    th = mgr.thresholds.model_dump()
    last_enriched = None
    for idx in range(payload.repeat):
        pkt = build_stimulation_packet(
            knee_angle_deg=payload.knee_angle_deg,
            heel_fsr=payload.heel_fsr,
            fore_fsr=payload.fore_fsr,
            thigh_pitch_deg=payload.thigh_pitch_deg,
            net_roll_deg=payload.net_roll_deg,
            flex_stiffness=payload.flex_stiffness,
            calibration=mgr.active_calibration,
            thresholds=th,
            step_idx=idx,
        )
        last_enriched = await mgr.handle_device_message(
            pkt, source="simulator", allow_auto_record=False
        )

    return {
        "ok": True,
        "injected": payload.repeat,
        "frame": last_enriched,
        "foot_pressure": (last_enriched.get("biomechanics") or {}).get("footPressure") if last_enriched else None,
        "aiRisk": last_enriched.get("aiRisk") if last_enriched else None,
        "sensorHealth": last_enriched.get("sensorHealth") if last_enriched else None,
    }

