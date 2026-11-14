"""Patient profile and per-patient calibration API endpoints."""

from fastapi import APIRouter, HTTPException, Request, status

from backend.routers import get_manager
from backend.schemas import (
    CalibrationOut,
    CalibrationPayload,
    PatientCreate,
    PatientOut,
    PatientUpdate,
)

router = APIRouter(prefix="/api/patients", tags=["patients"])


@router.get("", response_model=list[PatientOut])
async def list_patients(request: Request):
    mgr = get_manager(request)
    return await mgr.db.list_patients()


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
async def create_patient(payload: PatientCreate, request: Request):
    mgr = get_manager(request)
    return await mgr.db.create_patient(payload.model_dump())


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(patient_id: int, request: Request):
    mgr = get_manager(request)
    patient = await mgr.db.get_patient(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.get("/{patient_id}/summary")
async def get_patient_summary(patient_id: int, request: Request):
    mgr = get_manager(request)
    summary = await mgr.db.get_patient_summary(patient_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Patient not found")
    return summary


@router.patch("/{patient_id}", response_model=PatientOut)
async def update_patient(patient_id: int, payload: PatientUpdate, request: Request):
    mgr = get_manager(request)
    updated = await mgr.db.update_patient(patient_id, payload.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Patient not found")
    if mgr.active_patient_id == patient_id:
        mgr.active_patient_name = updated["name"]
        leg = updated.get("monitored_leg")
        mgr.active_leg = leg if leg in ("Left", "Right") else "Left"
        await mgr.broadcast_status()
    return updated


@router.delete("/{patient_id}")
async def delete_patient(patient_id: int, request: Request):
    mgr = get_manager(request)
    deleted = await mgr.db.delete_patient(patient_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Patient not found")
    if mgr.active_patient_id == patient_id:
        await mgr.select_patient(None)
    return {"deleted": True, "id": patient_id}


@router.post("/unselect")
async def unselect_active_patient(request: Request):
    mgr = get_manager(request)
    await mgr.select_patient(None)
    return {"selected": None}


@router.post("/{patient_id}/select", response_model=PatientOut)
async def select_active_patient(patient_id: int, request: Request):
    mgr = get_manager(request)
    patient = await mgr.select_patient(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.get("/{patient_id}/calibrations", response_model=list[CalibrationOut])
async def list_calibrations(patient_id: int, request: Request):
    mgr = get_manager(request)
    if not await mgr.db.get_patient(patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    return await mgr.db.list_calibrations(patient_id)


@router.post("/{patient_id}/calibrations", response_model=CalibrationOut, status_code=status.HTTP_201_CREATED)
async def save_calibration(patient_id: int, payload: CalibrationPayload, request: Request):
    mgr = get_manager(request)
    if not await mgr.db.get_patient(patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    cal = await mgr.db.create_calibration(patient_id, payload.model_dump(), activate=True)
    if mgr.active_patient_id == patient_id:
        mgr.active_calibration = cal
        mgr.calibration_step = 2 if cal["is_two_point"] else 1
        await mgr.send_calibration_to_device(cal)
        await mgr.broadcast_status()
    return cal


@router.post("/{patient_id}/calibrations/{cal_id}/activate", response_model=CalibrationOut)
async def activate_calibration(patient_id: int, cal_id: int, request: Request):
    mgr = get_manager(request)
    cal = await mgr.db.activate_calibration(patient_id, cal_id)
    if not cal:
        raise HTTPException(status_code=404, detail="Calibration not found")
    if mgr.active_patient_id == patient_id:
        mgr.active_calibration = cal
        mgr.calibration_step = 2 if cal["is_two_point"] else 1
        await mgr.send_calibration_to_device(cal)
        await mgr.broadcast_status()
    return cal

