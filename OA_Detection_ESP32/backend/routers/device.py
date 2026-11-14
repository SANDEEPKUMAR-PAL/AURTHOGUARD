"""Device control, thresholds, calibration commands, pull-bridge, alerts, and simulator API."""

from fastapi import APIRouter, Query, Request

from backend.routers import get_manager
from backend.schemas import (
    AlertOut,
    AutoCalibrationRequest,
    CalibrationApplyRequest,
    CalibrationCommandRequest,
    DeviceStatusOut,
    ESP32ConnectRequest,
    LegSelectRequest,
    SimulatorStartRequest,
    ThresholdsConfig,
)

router = APIRouter(prefix="/api", tags=["device"])


@router.get("/device/status", response_model=DeviceStatusOut)
async def get_device_status(request: Request):
    return get_manager(request).get_status()


@router.get("/device/thresholds", response_model=ThresholdsConfig)
async def get_thresholds(request: Request):
    return get_manager(request).thresholds


@router.put("/device/thresholds", response_model=ThresholdsConfig)
async def update_thresholds(payload: ThresholdsConfig, request: Request):
    mgr = get_manager(request)
    mgr.thresholds = payload
    await mgr.db.set_setting("thresholds", payload.model_dump())
    await mgr.send_thresholds_to_device()
    await mgr.broadcast_status()
    return mgr.thresholds


@router.post("/device/esp32/connect")
async def connect_esp32_pull_bridge(payload: ESP32ConnectRequest, request: Request):
    mgr = get_manager(request)
    await mgr.start_pull_bridge(payload.ip, payload.port)
    return {"status": "connecting", "target": f"ws://{payload.ip}:{payload.port}"}


@router.post("/device/esp32/disconnect")
async def disconnect_esp32_pull_bridge(request: Request):
    mgr = get_manager(request)
    await mgr.stop_pull_bridge()
    return {"status": "disconnected"}


@router.post("/device/calibrate")
async def trigger_calibration(payload: CalibrationCommandRequest, request: Request):
    mgr = get_manager(request)
    return await mgr.trigger_calibration(payload.step, patient_id=payload.patient_id)


@router.post("/device/calibration/apply")
async def apply_per_sensor_calibration(payload: CalibrationApplyRequest, request: Request):
    mgr = get_manager(request)
    return await mgr.apply_custom_calibration(
        payload.model_dump(exclude={"patient_id"}),
        patient_id=payload.patient_id,
    )


@router.post("/device/calibration/auto")
async def auto_calibrate_standing(payload: AutoCalibrationRequest, request: Request):
    mgr = get_manager(request)
    return await mgr.auto_calibrate_standing(
        patient_id=payload.patient_id,
        samples=payload.samples,
    )


@router.post("/device/leg")
async def select_monitored_leg(payload: LegSelectRequest, request: Request):
    mgr = get_manager(request)
    leg = await mgr.set_active_leg(payload.leg)
    return {"active_leg": leg}


@router.post("/device/simulator/start")
async def start_simulator(payload: SimulatorStartRequest, request: Request):
    mgr = get_manager(request)
    await mgr.start_simulator(profile=payload.profile, rate_hz=payload.rate_hz)
    return {"status": "running", "profile": payload.profile, "rate_hz": payload.rate_hz}


@router.post("/device/simulator/stop")
async def stop_simulator(request: Request):
    mgr = get_manager(request)
    await mgr.stop_simulator()
    return {"status": "stopped"}


@router.post("/device/buzzer/test")
async def test_hardware_buzzer(request: Request):
    mgr = get_manager(request)
    return await mgr.test_hardware_buzzer()


@router.post("/device/buzzer/mode")
async def set_hardware_buzzer_mode(payload: dict, request: Request):
    mgr = get_manager(request)
    return await mgr.set_hardware_buzzer_mode(bool(payload.get("passive", False)))


@router.get("/alerts", response_model=list[AlertOut])
async def list_recent_alerts(request: Request, limit: int = Query(default=100, ge=1, le=500)):
    return await get_manager(request).db.list_recent_alerts(limit=limit)


@router.delete("/alerts")
async def clear_alerts(request: Request, session_id: int | None = Query(default=None)):
    mgr = get_manager(request)
    deleted = await mgr.db.clear_alerts(session_id=session_id)
    await mgr.broadcast_dashboard({"type": "alertsCleared", "deleted": deleted})
    return {"deleted": deleted}

