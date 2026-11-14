"""Clinical OA analytics endpoints: bilateral L/R, longitudinal trends, NER districts & printable report."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.analytics import (
    CLINICAL_REFERENCES,
    build_patient_clinical_report,
    compute_ner_district_analytics,
    render_printable_report_html,
)
from backend.routers import get_manager

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/references")
async def get_clinical_references():
    """Return peer-reviewed medical & biomechanics reference ranges used by the sensors and AI."""
    return {"references": CLINICAL_REFERENCES}


@router.get("/ner-summary")
async def get_ner_district_summary(request: Request):
    """Aggregate completed OA screening metrics across NER districts."""
    mgr = get_manager(request)
    patients = await mgr.db.list_patients()
    sessions = await mgr.db.list_sessions()
    return compute_ner_district_analytics(patients, sessions)


@router.get("/patients/{patient_id}")
async def get_patient_analytics(patient_id: int, request: Request):
    """Return longitudinal trend, bilateral L/R asymmetry, and BMI assessment for a patient."""
    mgr = get_manager(request)
    summary = await mgr.db.get_patient_summary(patient_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Patient not found")
    report = build_patient_clinical_report(summary)
    return {
        "patient": report["patient"],
        "bmi_summary": report["bmi_summary"],
        "bilateral_comparison": report["bilateral_comparison"],
        "longitudinal_trend": report["longitudinal_trend"],
        "latest_oa_risk_score": report["latest_oa_risk_score"],
        "latest_oa_risk_tier": report["latest_oa_risk_tier"],
        "recommendations": report["recommendations"],
    }


@router.get("/patients/{patient_id}/report")
async def get_patient_clinical_report(patient_id: int, request: Request):
    """Return the complete structured clinical screening report JSON."""
    mgr = get_manager(request)
    summary = await mgr.db.get_patient_summary(patient_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Patient not found")
    return build_patient_clinical_report(summary)


@router.get("/patients/{patient_id}/report/html", response_class=HTMLResponse)
async def get_patient_printable_report(patient_id: int, request: Request):
    """Render self-contained printable/PDF HTML clinical screening report."""
    mgr = get_manager(request)
    summary = await mgr.db.get_patient_summary(patient_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Patient not found")
    report = build_patient_clinical_report(summary)
    return HTMLResponse(content=render_printable_report_html(report))

