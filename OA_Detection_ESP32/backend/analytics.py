"""Clinical OA analytics: BMI modifier, bilateral L/R asymmetry, longitudinal trends, NER district stats & HTML report."""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from backend.config import settings


CLINICAL_REFERENCES: list[dict[str, str]] = [
    {
        "parameter": "Wearable IMU Acquisition Settings",
        "healthy": "100 Hz sampling, ±4g accel, ±500 °/s gyro, ~20 Hz DLPF",
        "oa_threshold": "Avoids ±2g heel-strike & ±250 °/s swing saturation",
        "source": "MPU6050 Ambulatory Gait Kinematics Research Standards",
    },
    {
        "parameter": "Static Knee Extension / Hyperextension Limit",
        "healthy": "0° extension (universal physiological hyperextension to -10°)",
        "oa_threshold": "< -10° (hyperextension) or > +5° extension deficit",
        "source": "AAOS Goniometry & Knee OA Appropriate Use Criteria (AUC)",
    },
    {
        "parameter": "Static Deep Flexion Limit",
        "healthy": "Up to 135° universal active adult flexion",
        "oa_threshold": "< 110° restricted flexion or > 135° hyperflexion spike",
        "source": "AAOS Knee ROM & Surgical/Non-Arthroplasty Guidelines",
    },
    {
        "parameter": "Walking Gait Sagittal ROM",
        "healthy": "~58°–65° total sagittal excursion per stride",
        "oa_threshold": "< 48° reduced excursion; < 36° stiff-knee OA gait",
        "source": "Zeni & Higginson (Clin. Biomech., 2009); PubMed KOA Gait Studies",
    },
    {
        "parameter": "Spatiotemporal Gait (Cadence & Stance Phase)",
        "healthy": "100–120 steps/min cadence · ~60% Stance / ~40% Swing",
        "oa_threshold": "Reduced cadence (< 95 spm) & prolonged stance (> 64%)",
        "source": "PubMed KOA Spatiotemporal Gait Analysis Meta-Studies",
    },
    {
        "parameter": "Frontal-Plane Varus/Valgus Stability & Velocity",
        "healthy": "2°–4° coronal excursion; < 28.1 °/s frontal roll velocity",
        "oa_threshold": "≥ 28.1 °/s Peak Varus Velocity (PVV) or > 6.5° RMS wobble",
        "source": "Costello et al. (JMIR, 2020) & Mobile 9-Axis IMU Varus Thrust Studies",
    },
    {
        "parameter": "Plantar Pressure (FSR) Weight-Invariant Symmetry",
        "healthy": "< 10% bilateral SI; balanced heel-to-forefoot transfer",
        "oa_threshold": "> 60% sustained unilateral rearfoot/forefoot offloading",
        "source": "Clinical Gait Symmetry Index (SI) & Plantar FSR OA Cohorts",
    },
    {
        "parameter": "Bilateral Knee ROM Asymmetry",
        "healthy": "< 3° normal side-to-side difference",
        "oa_threshold": "≥ 8° inter-limb ROM asymmetry",
        "source": "Bilateral Knee Kinematics & Goniometric Symmetry Literature",
    },
    {
        "parameter": "BMI Joint-Load Classification (Universal Standard)",
        "healthy": "18.5–24.9 kg/m² (Normal adult range worldwide)",
        "oa_threshold": "25.0–29.9 Overweight (Moderate); ≥ 30.0 Obese (Elevated load)",
        "source": "World Health Organization (WHO) Universal Adult BMI Standards",
    },
    {
        "parameter": "OA Clinical Screening Age & Morning Stiffness",
        "healthy": "Symptom-free joint articulation",
        "oa_threshold": "Age ≥ 45 yrs screening threshold · Morning stiffness ≤ 30 min",
        "source": "American College of Rheumatology (ACR) & Epidemiological OA Guidelines",
    },
]


def compute_bmi_summary(height_cm: Optional[float], weight_kg: Optional[float]) -> dict[str, Any]:
    """Compute BMI and OA joint-load modifier using WHO universal adult standards."""
    if not height_cm or not weight_kg or height_cm <= 0 or weight_kg <= 0:
        return {
            "bmi": None,
            "category": "Not Recorded",
            "oa_load_modifier": "Unknown",
            "note": "Record patient height & weight to compute WHO universal BMI joint-load assessment.",
        }

    h_m = height_cm / 100.0
    bmi = round(weight_kg / (h_m * h_m), 1)
    if bmi < 18.5:
        cat, mod, note = "Underweight", "Low", "Low mechanical joint load (<18.5 WHO universal cutoff)."
    elif bmi < 25.0:
        cat, mod, note = "Normal", "Low", "WHO universal healthy BMI (18.5–24.9) — normal tibiofemoral load (Asian guidance also monitors ≥23.0)."
    elif bmi < 30.0:
        cat, mod, note = "Overweight", "Moderate", "WHO Overweight (25.0–29.9) — elevated knee compressive load (~3–4× body weight during gait)."
    elif bmi < 35.0:
        cat, mod, note = "Obese (Class I)", "Elevated", "WHO Obese Class I (30.0–34.9) — high medial tibiofemoral stress and OA progression risk."
    elif bmi < 40.0:
        cat, mod, note = "Obese (Class II)", "Elevated", "WHO Obese Class II (35.0–39.9) — very high knee joint compressive overload."
    else:
        cat, mod, note = "Obese (Class III)", "Elevated", "WHO Obese Class III (≥40.0) — severe mechanical knee joint overload."

    return {
        "bmi": bmi,
        "category": cat,
        "oa_load_modifier": mod,
        "note": note,
    }


def compute_bilateral_comparison(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare completed Left vs Right leg sessions for inter-limb asymmetry."""
    completed = [s for s in sessions if s.get("status") == "completed" and s.get("sample_count", 0) > 0]
    sides: dict[str, dict[str, Any]] = {}

    for leg in ("Left", "Right"):
        leg_sessions = [s for s in completed if (s.get("monitored_leg") or "Left") == leg]
        if not leg_sessions:
            sides[leg] = {"session_count": 0, "avg_rom_deg": None, "avg_risk_score": None, "latest_tier": None}
            continue
        roms = [s["rom_deg"] for s in leg_sessions if s.get("rom_deg") is not None]
        risks = [s["oa_risk_score"] for s in leg_sessions if s.get("oa_risk_score") is not None]
        latest = leg_sessions[0]  # sessions ordered newest-first
        sides[leg] = {
            "session_count": len(leg_sessions),
            "avg_rom_deg": round(sum(roms) / len(roms), 1) if roms else None,
            "avg_risk_score": round(sum(risks) / len(risks), 1) if risks else None,
            "latest_rom_deg": latest.get("rom_deg"),
            "latest_risk_score": latest.get("oa_risk_score"),
            "latest_tier": latest.get("oa_risk_tier"),
        }

    left_rom = sides["Left"]["avg_rom_deg"]
    right_rom = sides["Right"]["avg_rom_deg"]
    left_risk = sides["Left"]["avg_risk_score"]
    right_risk = sides["Right"]["avg_risk_score"]

    rom_diff = round(abs(left_rom - right_rom), 1) if (left_rom is not None and right_rom is not None) else None
    risk_diff = round(abs(left_risk - right_risk), 1) if (left_risk is not None and right_risk is not None) else None

    available_both = rom_diff is not None or risk_diff is not None
    significant = bool(
        (rom_diff is not None and rom_diff >= settings.bilateral_rom_asymmetry_thresh)
        or (risk_diff is not None and risk_diff >= settings.bilateral_risk_diff_thresh)
    )
    if not available_both:
        summary_msg = "Record sessions on both Left and Right legs to measure bilateral asymmetry."
    elif significant:
        weaker = "Left" if ((left_risk or 0) > (right_risk or 0) or (left_rom or 999) < (right_rom or 999)) else "Right"
        summary_msg = f"Significant inter-limb asymmetry detected ({weaker} knee shows greater deficit)."
    else:
        summary_msg = "Balanced bilateral knee ROM and OA risk across Left and Right legs."

    return {
        "available": available_both,
        "left": sides["Left"],
        "right": sides["Right"],
        "rom_asymmetry_deg": rom_diff,
        "risk_score_diff": risk_diff,
        "significant_asymmetry": significant,
        "summary": summary_msg,
    }


def compute_longitudinal_trend(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build chronological visit progression series and compute trend direction."""
    completed = [s for s in sessions if s.get("status") == "completed" and s.get("sample_count", 0) > 0]
    ordered = sorted(completed, key=lambda s: s.get("start_time", ""))

    points = [
        {
            "session_id": s["id"],
            "date": s["start_time"],
            "leg": s.get("monitored_leg") or "Left",
            "rom_deg": s.get("rom_deg"),
            "oa_risk_score": s.get("oa_risk_score"),
            "oa_risk_tier": s.get("oa_risk_tier") or "Pending",
            "alert_count": s.get("alert_count", 0),
        }
        for s in ordered
    ]

    scored = [p for p in points if p["oa_risk_score"] is not None]
    direction = "Insufficient Data"
    delta_score = None
    if len(scored) >= 2:
        delta_score = round(scored[-1]["oa_risk_score"] - scored[0]["oa_risk_score"], 1)
        if delta_score <= -6.0:
            direction = "Improving"
        elif delta_score >= 6.0:
            direction = "Worsening"
        else:
            direction = "Stable"

    return {
        "visit_count": len(points),
        "direction": direction,
        "risk_delta": delta_score,
        "points": points,
    }


def _duration_label(start_iso: Optional[str], end_iso: Optional[str]) -> str:
    """Compute human-readable mm:ss duration between two ISO-8601 timestamps."""
    if not start_iso:
        return "—"
    if not end_iso:
        return "Recording"
    try:
        t0 = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        if t1.tzinfo is None:
            t1 = t1.replace(tzinfo=timezone.utc)
        secs = max(0, int((t1 - t0).total_seconds()))
        return f"{secs // 60:02d}:{secs % 60:02d}"
    except Exception:
        return "—"


def _enrich_session_rows(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**s, "duration_label": _duration_label(s.get("start_time"), s.get("end_time"))} for s in sessions]


def _build_recommendations(
    latest_tier: str,
    patient: dict[str, Any],
    bmi_info: dict[str, Any],
    bilateral: dict[str, Any],
    trials: list[dict[str, Any]],
    bio: dict[str, Any],
) -> list[str]:
    recommendations: list[str] = []
    if latest_tier == "High":
        recommendations.append("Priority orthopedic & radiographic (X-ray Kellgren-Lawrence) knee evaluation recommended.")
    elif latest_tier == "Moderate":
        recommendations.append("Early OA markers detected — start guided quadriceps strengthening & low-impact ROM physiotherapy; re-screen in 4 weeks.")
    elif latest_tier == "Low":
        recommendations.append("Current gait biomechanics are within healthy limits. Maintain regular joint mobility exercises.")
    else:
        recommendations.append("Complete at least one walking or guided exercise recording to generate AI OA risk grading.")

    age = patient.get("age")
    if age and age >= settings.acr_age_threshold:
        recommendations.append(
            f"ACR Screening Age Cofactor ({age} yrs ≥ {settings.acr_age_threshold}): check for morning stiffness (≤30 min), crepitus, and track WOMAC/KOOS scores every 3 months."
        )
    if bmi_info.get("oa_load_modifier") in ("Moderate", "Elevated"):
        recommendations.append(f"BMI {bmi_info['bmi']} ({bmi_info['category']}): weight-offloading lifestyle guidance advised to reduce medial knee load.")
    if bilateral.get("significant_asymmetry"):
        recommendations.append(bilateral["summary"])
    if bio.get("min_knee_angle") is not None and float(bio["min_knee_angle"]) > 5.0 and (bio.get("sample_count") or 0) >= 10:
        recommendations.append(
            f"Minimum knee extension stayed at {bio['min_knee_angle']}° (>5° flexion contracture screening cut-off); assess terminal knee extension flexibility."
        )
    if bio.get("mean_imbalance_pct") is not None and float(bio["mean_imbalance_pct"]) >= 45.0:
        recommendations.append(
            f"Elevated plantar pressure asymmetry ({bio['mean_imbalance_pct']}% mean heel/forefoot differential) indicates antalgic offloading."
        )
    if any(t.get("finding") == "High Risk Marker" for t in trials[:5]):
        recommendations.append("Recent exercise trial flagged high-risk stiffness or instability — review exercise trial markers below.")
    return recommendations


def build_patient_clinical_report(summary: dict[str, Any]) -> dict[str, Any]:
    """Construct complete structured clinical screening report for a patient."""
    patient = summary["patient"]
    sessions = _enrich_session_rows(summary.get("sessions", []))
    trials = summary.get("exercise_trials", [])
    calibrations = summary.get("calibrations", [])
    alerts = summary.get("alerts", [])
    bio = summary.get("biomechanics_summary") or {}

    bmi_info = compute_bmi_summary(patient.get("height_cm"), patient.get("weight_kg"))
    bilateral = compute_bilateral_comparison(sessions)
    trend = compute_longitudinal_trend(sessions)

    completed_sessions = [s for s in sessions if s.get("status") == "completed"]
    scored = [s for s in completed_sessions if s.get("oa_risk_score") is not None]
    latest_session = completed_sessions[0] if completed_sessions else (sessions[0] if sessions else None)
    latest_risk = scored[0]["oa_risk_score"] if scored else None
    latest_tier = scored[0]["oa_risk_tier"] if scored else "Pending"

    recommendations = _build_recommendations(latest_tier, patient, bmi_info, bilateral, trials, bio)

    return {
        "report_scope": "patient",
        "generated_at": settings.now_tz_iso(),
        "timezone": settings.display_timezone,
        "timezone_label": settings.display_timezone_label,
        "project": "SIH26004 — AI-Assisted Early Detection System for Osteoarthritis (NER)",
        "patient": patient,
        "bmi_summary": bmi_info,
        "calibrated": len(calibrations) > 0,
        "active_calibration": calibrations[0] if calibrations else None,
        "latest_session": latest_session,
        "latest_oa_risk_score": latest_risk,
        "latest_oa_risk_tier": latest_tier,
        "total_sessions": len(sessions),
        "completed_sessions": len(completed_sessions),
        "total_samples": sum(int(s.get("sample_count") or 0) for s in sessions),
        "total_alerts": sum(int(s.get("alert_count") or 0) for s in sessions),
        "biomechanics_summary": bio,
        "bilateral_comparison": bilateral,
        "longitudinal_trend": trend,
        "sessions": sessions[:25],
        "recent_trials": trials[:10],
        "alerts": alerts[:25],
        "recommendations": recommendations,
    }


def build_session_clinical_report(
    session: dict[str, Any],
    patient: Optional[dict[str, Any]] = None,
    biomechanics_summary: Optional[dict[str, Any]] = None,
    alerts: Optional[list[dict[str, Any]]] = None,
    calibration: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble structured clinical report for a single session (registered or Walk-in)."""
    resolved_patient = patient or {
        "id": session.get("patient_id") or "Walk-in",
        "name": session.get("patient_name") or "Walk-in / Unassigned",
        "age": None,
        "gender": "—",
        "monitored_leg": session.get("monitored_leg") or "Left",
        "height_cm": None,
        "weight_kg": None,
        "region_district": "Clinical Assessment",
        "notes": session.get("notes") or "",
    }
    enriched_sessions = _enrich_session_rows([session])
    bio = biomechanics_summary or {}
    bmi_info = compute_bmi_summary(resolved_patient.get("height_cm"), resolved_patient.get("weight_kg"))
    bilateral = compute_bilateral_comparison(enriched_sessions)
    trend = compute_longitudinal_trend(enriched_sessions)
    score = session.get("oa_risk_score")
    tier = session.get("oa_risk_tier") or "Pending"
    recommendations = _build_recommendations(tier, resolved_patient, bmi_info, bilateral, [], bio)

    return {
        "report_scope": "session",
        "generated_at": settings.now_tz_iso(),
        "timezone": settings.display_timezone,
        "timezone_label": settings.display_timezone_label,
        "project": "SIH26004 — AI-Assisted Early Detection System for Osteoarthritis (NER)",
        "patient": resolved_patient,
        "bmi_summary": bmi_info,
        "calibrated": bool(calibration or session.get("calibration_id")),
        "active_calibration": calibration,
        "latest_session": enriched_sessions[0],
        "latest_oa_risk_score": score,
        "latest_oa_risk_tier": tier,
        "total_sessions": 1,
        "completed_sessions": 1 if session.get("status") == "completed" else 0,
        "total_samples": int(session.get("sample_count") or bio.get("sample_count") or 0),
        "total_alerts": int(session.get("alert_count") or 0),
        "biomechanics_summary": bio,
        "bilateral_comparison": bilateral,
        "longitudinal_trend": trend,
        "sessions": enriched_sessions,
        "recent_trials": [],
        "alerts": (alerts or [])[:25],
        "recommendations": recommendations,
    }


def _tier_badge_html(tier: Optional[str]) -> str:
    t = str(tier or "Pending")
    cls = {
        "Low": "badge-low",
        "Moderate": "badge-mod",
        "High": "badge-high",
        "critical": "badge-high",
        "warning": "badge-mod",
        "recovery": "badge-low",
    }.get(t, "badge-pending")
    return f'<span class="badge {cls}">{html.escape(t)}</span>'


def _svg_risk_scale(score: Optional[float], tier: str) -> str:
    """Self-contained SVG OA Risk severity bar (Low 0-34.9, Moderate 35-65, High >65)."""
    pct = max(0.0, min(100.0, float(score))) if score is not None else 0.0
    pin_x = 12 + pct * 3.16
    color = "#e11d48" if tier == "High" else ("#d97706" if tier == "Moderate" else ("#059669" if tier == "Low" else "#64748b"))
    label = f"{pct:.1f}/100 ({tier})" if score is not None else "Pending"
    return (
        f'<svg viewBox="0 0 340 48" width="100%" height="48" style="display:block;max-width:340px">'
        f'<rect x="12" y="18" width="110" height="10" rx="5" fill="#d1fae5" stroke="#10b981" stroke-width="0.6"/>'
        f'<rect x="124" y="18" width="95" height="10" fill="#fef3c7" stroke="#f59e0b" stroke-width="0.6"/>'
        f'<rect x="221" y="18" width="107" height="10" rx="5" fill="#ffe4e6" stroke="#f43f5e" stroke-width="0.6"/>'
        f'<text x="12" y="42" font-size="9" fill="#059669" font-weight="700">0 Low</text>'
        f'<text x="124" y="42" font-size="9" fill="#d97706" font-weight="700">35 Moderate</text>'
        f'<text x="235" y="42" font-size="9" fill="#e11d48" font-weight="700">65 High 100</text>'
        + (
            f'<circle cx="{pin_x:.1f}" cy="23" r="6" fill="{color}" stroke="#ffffff" stroke-width="2"/>'
            f'<text x="{min(285.0, max(12.0, pin_x - 18)):.1f}" y="12" font-size="10" fill="{color}" font-weight="800">{html.escape(label)}</text>'
            if score is not None else
            '<text x="12" y="12" font-size="10" fill="#64748b" font-weight="700">Pending Score</text>'
        )
        + "</svg>"
    )


def _svg_trend_chart(points: list[dict[str, Any]]) -> str:
    """Self-contained SVG dual-line longitudinal progression chart (OA Risk & Knee ROM)."""
    if not points:
        return '<div class="empty-note">Complete at least one session to plot longitudinal OA Risk &amp; ROM progression.</div>'
    pts = points[-12:]
    w, h = 760, 170
    pad_l, pad_r, pad_t, pad_b = 42, 42, 20, 32
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    n = len(pts)

    def _x(i: int) -> float:
        return pad_l + (plot_w / 2.0 if n == 1 else i * (plot_w / (n - 1)))

    def _y_risk(v: float) -> float:
        return pad_t + plot_h - (max(0.0, min(100.0, v)) / 100.0) * plot_h

    def _y_rom(v: float) -> float:
        return pad_t + plot_h - (max(0.0, min(140.0, v)) / 140.0) * plot_h

    grid = "".join(
        f'<line x1="{pad_l}" y1="{pad_t + plot_h * frac:.1f}" x2="{w - pad_r}" y2="{pad_t + plot_h * frac:.1f}" stroke="#e2e8f0" stroke-dasharray="3,3"/>'
        f'<text x="6" y="{pad_t + plot_h * frac + 3:.1f}" font-size="9" fill="#e11d48">{int(100 * (1 - frac))}</text>'
        f'<text x="{w - pad_r + 6}" y="{pad_t + plot_h * frac + 3:.1f}" font-size="9" fill="#0d9488">{int(140 * (1 - frac))}°</text>'
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0)
    )

    risk_coords = [(i, _x(i), _y_risk(float(p["oa_risk_score"]))) for i, p in enumerate(pts) if p.get("oa_risk_score") is not None]
    rom_coords = [(i, _x(i), _y_rom(float(p["rom_deg"]))) for i, p in enumerate(pts) if p.get("rom_deg") is not None]

    risk_line = (
        f'<polyline fill="none" stroke="#e11d48" stroke-width="2.4" points="{" ".join(f"{x:.1f},{y:.1f}" for _, x, y in risk_coords)}"/>'
        if len(risk_coords) >= 2 else ""
    )
    rom_line = (
        f'<polyline fill="none" stroke="#0d9488" stroke-width="2.4" points="{" ".join(f"{x:.1f},{y:.1f}" for _, x, y in rom_coords)}"/>'
        if len(rom_coords) >= 2 else ""
    )
    risk_dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#e11d48" stroke="#fff" stroke-width="1.5"/>'
        f'<text x="{x:.1f}" y="{max(12.0, y - 7):.1f}" text-anchor="middle" font-size="9" fill="#be123c" font-weight="700">{pts[i]["oa_risk_score"]:.0f}</text>'
        for i, x, y in risk_coords
    )
    rom_dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#0d9488" stroke="#fff" stroke-width="1.5"/>'
        f'<text x="{x:.1f}" y="{min(pad_t + plot_h - 4, y + 12):.1f}" text-anchor="middle" font-size="9" fill="#0f766e" font-weight="700">{pts[i]["rom_deg"]:.0f}°</text>'
        for i, x, y in rom_coords
    )
    x_labels = "".join(
        f'<text x="{_x(i):.1f}" y="{h - 10}" text-anchor="middle" font-size="9" fill="#475569">#{pts[i]["session_id"]} ({html.escape(str(pts[i].get("leg", "L"))[:1])})</text>'
        for i in range(n)
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" style="display:block;background:#f8fbfa;border:1px solid #dce6e3;border-radius:10px">'
        f'{grid}{risk_line}{rom_line}{risk_dots}{rom_dots}{x_labels}'
        f'<text x="{pad_l}" y="12" font-size="9.5" fill="#e11d48" font-weight="700">● OA Risk Score (0-100, Left)</text>'
        f'<text x="{pad_l + 170}" y="12" font-size="9.5" fill="#0d9488" font-weight="700">● Knee ROM (0-140°, Right)</text>'
        "</svg>"
    )


def compute_ner_district_analytics(
    patients: list[dict[str, Any]], sessions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Aggregate OA screening metrics by North Eastern Region (NER) district."""
    patient_district = {
        p["id"]: (p.get("region_district") or "").strip() or "Unspecified"
        for p in patients
    }

    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "district": "",
        "patient_ids": set(),
        "session_count": 0,
        "risk_scores": [],
        "roms": [],
        "tiers": {"Low": 0, "Moderate": 0, "High": 0, "Pending": 0},
    })

    for p in patients:
        d = (p.get("region_district") or "").strip() or "Unspecified"
        buckets[d]["district"] = d
        buckets[d]["patient_ids"].add(p["id"])

    for s in sessions:
        if s.get("status") != "completed":
            continue
        pid = s.get("patient_id")
        d = patient_district.get(pid, "Unspecified") if pid else "Walk-in"
        buckets[d]["district"] = d
        buckets[d]["session_count"] += 1
        if s.get("oa_risk_score") is not None:
            buckets[d]["risk_scores"].append(float(s["oa_risk_score"]))
        if s.get("rom_deg") is not None:
            buckets[d]["roms"].append(float(s["rom_deg"]))
        tier = s.get("oa_risk_tier") or "Pending"
        buckets[d]["tiers"][tier] = buckets[d]["tiers"].get(tier, 0) + 1

    rows = []
    for d, b in sorted(buckets.items(), key=lambda kv: (-len(kv[1]["patient_ids"]), kv[0])):
        rs = b["risk_scores"]
        roms = b["roms"]
        rows.append({
            "district": d,
            "patient_count": len(b["patient_ids"]),
            "session_count": b["session_count"],
            "avg_risk_score": round(sum(rs) / len(rs), 1) if rs else None,
            "avg_rom_deg": round(sum(roms) / len(roms), 1) if roms else None,
            "tiers": b["tiers"],
        })

    all_scores = [s["oa_risk_score"] for s in sessions if s.get("status") == "completed" and s.get("oa_risk_score") is not None]
    return {
        "total_patients": len(patients),
        "total_completed_sessions": sum(1 for s in sessions if s.get("status") == "completed"),
        "overall_avg_risk": round(sum(all_scores) / len(all_scores), 1) if all_scores else None,
        "districts": rows,
    }


def render_printable_report_html(report: dict[str, Any]) -> str:
    """Render a comprehensive, self-contained print/PDF-ready clinical OA screening report."""
    p = report["patient"]
    bmi = report["bmi_summary"]
    bil = report["bilateral_comparison"]
    trend = report["longitudinal_trend"]
    bio = report.get("biomechanics_summary") or {}
    cal = report.get("active_calibration") or {}
    sessions = report.get("sessions") or []
    alerts = report.get("alerts") or []
    latest_sess = report.get("latest_session") or {}
    e = lambda v: html.escape(str(v if v is not None and v != "" else "—"))

    scope_title = (
        f"Session #{latest_sess.get('id')} Screening Report"
        if report.get("report_scope") == "session" and latest_sess.get("id")
        else "Osteoarthritis (OA) Early Screening Report"
    )
    cal_label = (
        "2-Point + Per-Sensor Tare"
        if cal.get("is_two_point")
        else ("Standing Zero Calibration" if report.get("calibrated") else "Uncalibrated (Default)")
    )

    recs_li = "".join(f"<li>{e(r)}</li>" for r in report["recommendations"])
    trials_rows = "".join(
        f"<tr><td>{e(t['exercise_name'])}</td><td>{e(t.get('monitored_leg','Left'))}</td>"
        f"<td>{e(t['completed_reps'])}/{e(t['target_reps'])}</td><td>{e(t.get('avg_rom_deg'))}°</td>"
        f"<td>{e(t.get('roll_wobble_deg'))}°</td><td>{e(t.get('flex_agreement_pct'))}%</td>"
        f"<td>{e(t.get('quality_score'))}%</td><td>{_tier_badge_html(t.get('finding'))}</td></tr>"
        for t in report.get("recent_trials", [])
    ) or "<tr><td colspan='8' class='empty-td'>No exercise trials recorded yet.</td></tr>"

    def _format_session_detail(sess: dict[str, Any]) -> str:
        parts = []
        markers = [str(m) for m in (sess.get("risk_markers") or []) if m]
        if markers:
            parts.append("<div>" + e("; ".join(markers)) + "</div>")
        note = str(sess.get("notes") or "").strip()
        if note:
            parts.append(f"<div style='color:#0f766e'><strong>Note:</strong> {e(note)}</div>")
        return "".join(parts) or "—"

    sessions_rows = "".join(
        f"<tr>"
        f"<td><strong>#{e(s['id'])}</strong></td>"
        f"<td>{e(settings.format_timestamp_tz(s.get('start_time')))}</td>"
        f"<td>{e(s.get('duration_label'))}</td>"
        f"<td>{e(s.get('monitored_leg','Left'))}</td>"
        f"<td>{e(s.get('source'))}</td>"
        f"<td>{e(s.get('sample_count'))}</td>"
        f"<td>{e(s.get('min_knee_angle'))}° – {e(s.get('max_knee_angle'))}° (<strong>{e(s.get('rom_deg'))}°</strong>)</td>"
        f"<td>{e(s.get('alert_count', 0))}</td>"
        f"<td>{e(s.get('oa_risk_score'))} {_tier_badge_html(s.get('oa_risk_tier'))}</td>"
        f"<td style='font-size:11px'>{_format_session_detail(s)}</td>"
        f"</tr>"
        for s in sessions
    ) or "<tr><td colspan='10' class='empty-td'>No recorded sessions yet.</td></tr>"

    alerts_rows = "".join(
        f"<tr>"
        f"<td>{e(settings.format_timestamp_tz(a.get('created_at')))}</td>"
        f"<td>{_tier_badge_html(a.get('severity'))}</td>"
        f"<td>{e(a.get('marker_type'))}</td>"
        f"<td>{e(a.get('knee_angle'))}°</td>"
        f"<td>{e(a.get('oa_risk_score'))}</td>"
        f"<td>{e(a.get('message'))}</td>"
        f"</tr>"
        for a in alerts[:15]
    ) or "<tr><td colspan='6' class='empty-td'>No abnormal/critical alerts recorded.</td></tr>"

    heel_pct = bio.get("heel_pct") if bio.get("heel_pct") is not None else 50.0
    fore_pct = bio.get("fore_pct") if bio.get("fore_pct") is not None else 50.0
    svg_scale = _svg_risk_scale(report.get("latest_oa_risk_score"), str(report.get("latest_oa_risk_tier") or "Pending"))
    svg_trend = _svg_trend_chart(trend.get("points") or [])

    cal_detail = (
        f"Thigh P:{cal.get('thigh_pitch_offset',0):.1f}° R:{cal.get('thigh_roll_offset',0):.1f}° · "
        f"Shin P:{cal.get('shin_pitch_offset',0):.1f}° R:{cal.get('shin_roll_offset',0):.1f}° · "
        f"Flex {cal.get('flex_straight',2048)}→{cal.get('flex_bent',1200)} · "
        f"FSR Tare H:{cal.get('fsr_heel_tare',0)} F:{cal.get('fsr_fore_tare',0)}"
        if cal else "Standard AAOS zeroed device baseline"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{e(scope_title)} — {e(p['name'])}</title>
<style>
  @page {{ size: A4; margin: 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; color: #1e2d2f; background: #eef4f2; margin: 0; padding: 20px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ max-width: 960px; margin: 0 auto; background: #fff; border: 1px solid #d5e2df; border-radius: 14px; padding: 26px 32px; box-shadow: 0 8px 24px rgba(15,45,40,0.06); }}
  .top {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2.5px solid #0d9488; padding-bottom: 14px; margin-bottom: 16px; gap: 16px; }}
  .proj-pill {{ display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: 0.6px; text-transform: uppercase; background: #ccfbf1; color: #0f766e; padding: 3px 8px; border-radius: 999px; margin-bottom: 6px; }}
  h1 {{ margin: 0; font-size: 21px; color: #0f766e; }}
  .sub {{ font-size: 12px; color: #64748b; margin-top: 3px; }}
  .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }}
  .box {{ background: #f7faf9; border: 1px solid #dce6e3; border-radius: 10px; padding: 11px 13px; }}
  .lbl {{ font-size: 10.5px; color: #64748b; text-transform: uppercase; font-weight: 700; letter-spacing: 0.4px; }}
  .val {{ font-size: 19px; font-weight: 800; margin-top: 4px; color: #0f172a; }}
  h2 {{ font-size: 13px; color: #0f766e; margin: 18px 0 8px; text-transform: uppercase; letter-spacing: 0.6px; border-left: 3.5px solid #0d9488; padding-left: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 11.5px; margin-bottom: 12px; }}
  th, td {{ padding: 7px 8px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: top; }}
  th {{ background: #f1f7f6; color: #334155; font-size: 10.5px; text-transform: uppercase; font-weight: 700; }}
  .empty-td {{ text-align: center; color: #64748b; padding: 14px; }}
  .empty-note {{ padding: 14px; background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; font-size: 12px; color: #64748b; text-align: center; }}
  .badge {{ display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 10px; font-weight: 700; text-transform: uppercase; }}
  .badge-low {{ background: #dcfce7; color: #166534; }}
  .badge-mod {{ background: #fef3c7; color: #92400e; }}
  .badge-high {{ background: #ffe4e6; color: #9f1239; }}
  .badge-pending {{ background: #e2e8f0; color: #475569; }}
  .fp-track {{ display: flex; height: 14px; border-radius: 999px; overflow: hidden; border: 1px solid #cbd5e1; margin: 8px 0 6px; }}
  .fp-heel {{ background: #3b82f6; height: 100%; }}
  .fp-fore {{ background: #8b5cf6; height: 100%; }}
  ul {{ margin: 6px 0 0 18px; line-height: 1.65; font-size: 12.5px; }}
  .sign-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 22px; padding-top: 14px; border-top: 1px dashed #cbd5e1; font-size: 11.5px; color: #475569; }}
  .sign-line {{ border-bottom: 1px solid #94a3b8; height: 24px; margin-bottom: 4px; }}
  .actions {{ max-width: 960px; margin: 0 auto 12px; display: flex; justify-content: flex-end; gap: 10px; }}
  .btn {{ padding: 8px 16px; border-radius: 8px; border: none; background: #0d9488; color: #fff; font-weight: 600; cursor: pointer; font-size: 13px; }}
  .btn-soft {{ background: #fff; color: #334155; border: 1px solid #cbd5e1; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .sheet {{ border: none; box-shadow: none; padding: 0; max-width: 100%; }}
    .actions {{ display: none; }}
    tr, .box {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="actions">
  <button class="btn btn-soft" onclick="window.close()">← Close</button>
  <button class="btn" onclick="window.print()">🖨️ Print / Save PDF</button>
</div>
<div class="sheet">
  <div class="top">
    <div>
      <div class="proj-pill">Clinical Screening Record · 100 Hz 5-Sensor Wearable</div>
      <h1>{e(scope_title)}</h1>
      <div class="sub">{e(report['project'])} · Generated {e(settings.format_timestamp_tz(report.get('generated_at')))}</div>
    </div>
    <div style="text-align:right;font-size:12px;line-height:1.5">
      <div><strong>Patient ID:</strong> #{e(p['id'])}</div>
      <div><strong>District (NER):</strong> {e(p.get('region_district'))}</div>
      <div><strong>Calibration:</strong> {e(cal_label)}</div>
    </div>
  </div>

  <div class="grid-4">
    <div class="box">
      <div class="lbl">Patient Profile</div>
      <div class="val" style="font-size:16px">{e(p['name'])}</div>
      <div class="sub">Age {e(p.get('age'))} · {e(p.get('gender'))} · Leg: {e(p.get('monitored_leg'))}</div>
      <div class="sub">Ht {e(p.get('height_cm'))} cm · Wt {e(p.get('weight_kg'))} kg</div>
    </div>
    <div class="box">
      <div class="lbl">AI OA Risk &amp; Severity</div>
      <div class="val">{e(report['latest_oa_risk_score'])} <span style="font-size:12px;font-weight:600;color:#64748b">/ 100</span> {_tier_badge_html(report['latest_oa_risk_tier'])}</div>
      <div class="sub">Trend: <strong>{e(trend['direction'])}</strong> · Confidence: {e((latest_sess.get('oa_risk_tier') and 'RF+Bio') or 'Baseline')}</div>
    </div>
    <div class="box">
      <div class="lbl">WHO BMI Joint Load</div>
      <div class="val">{e(bmi['bmi'])}</div>
      <div class="sub">{e(bmi['category'])} ({e(bmi['oa_load_modifier'])} load)</div>
    </div>
    <div class="box">
      <div class="lbl">Screening Volume</div>
      <div class="val">{e(report.get('completed_sessions', 0))}/{e(report.get('total_sessions', 0))}</div>
      <div class="sub">{e(report.get('total_samples', 0))} samples · {e(report.get('total_alerts', 0))} alerts</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="box">
      <div class="lbl">OA Risk Clinical Scale &amp; Plantar Balance</div>
      <div style="margin-top:6px">{svg_scale}</div>
      <div class="fp-track">
        <div class="fp-heel" style="width:{heel_pct}%"></div>
        <div class="fp-fore" style="width:{fore_pct}%"></div>
      </div>
      <div class="sub" style="display:flex;justify-content:space-between">
        <span><strong>Heel Load:</strong> {e(bio.get('heel_pct'))}% (avg FSR {e(bio.get('avg_heel_fsr'))})</span>
        <span><strong>Forefoot:</strong> {e(bio.get('fore_pct'))}% (Imb {e(bio.get('mean_imbalance_pct'))}%)</span>
      </div>
    </div>
    <div class="box">
      <div class="lbl">Joint Kinematics Summary (vs AAOS Norm -10° to 135°)</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px">
        <div><div class="sub">Functional ROM</div><div style="font-size:16px;font-weight:800;color:#0f766e">{e(bio.get('rom_deg') if bio.get('rom_deg') is not None else latest_sess.get('rom_deg'))}°</div></div>
        <div><div class="sub">Ext / Peak Flex</div><div style="font-size:14px;font-weight:700">{e(bio.get('min_knee_angle'))}° / {e(bio.get('max_knee_angle'))}°</div></div>
        <div><div class="sub">Abnormal Rate</div><div style="font-size:14px;font-weight:700">{e(bio.get('abnormal_pct'))}%</div></div>
      </div>
      <div class="sub" style="margin-top:8px"><strong>Calibration Profile:</strong> {e(cal_detail)}</div>
      <div class="sub"><strong>Patient Notes:</strong> {e(p.get('notes'))}</div>
    </div>
  </div>

  <h2>Longitudinal OA Risk &amp; Knee ROM Progression</h2>
  {svg_trend}

  <h2>Bilateral Comparison (Left vs. Right Knee)</h2>
  <table>
    <tr><th>Side</th><th>Sessions</th><th>Avg ROM</th><th>Avg OA Risk</th><th>Latest Tier</th></tr>
    <tr><td>Left Knee</td><td>{e(bil['left']['session_count'])}</td><td>{e(bil['left']['avg_rom_deg'])}°</td><td>{e(bil['left']['avg_risk_score'])}</td><td>{_tier_badge_html(bil['left']['latest_tier'])}</td></tr>
    <tr><td>Right Knee</td><td>{e(bil['right']['session_count'])}</td><td>{e(bil['right']['avg_rom_deg'])}°</td><td>{e(bil['right']['avg_risk_score'])}</td><td>{_tier_badge_html(bil['right']['latest_tier'])}</td></tr>
  </table>
  <div class="sub" style="margin-bottom:10px"><strong>Asymmetry Finding:</strong> {e(bil['summary'])} (ROM Diff: {e(bil['rom_asymmetry_deg'])}° · Risk Diff: {e(bil['risk_score_diff'])} pts)</div>

  <h2>Recorded Session History</h2>
  <table>
    <tr><th>Session</th><th>Date ({e(settings.display_timezone_label)})</th><th>Duration</th><th>Side</th><th>Source</th><th>Samples</th><th>Angle / ROM</th><th>Alerts</th><th>OA Risk</th><th>Markers / Notes</th></tr>
    {sessions_rows}
  </table>

  <h2>Guided Exercise Trials</h2>
  <table>
    <tr><th>Exercise</th><th>Leg</th><th>Reps</th><th>ROM</th><th>Wobble</th><th>Flex Match</th><th>Quality</th><th>Finding</th></tr>
    {trials_rows}
  </table>

  <h2>Critical &amp; Abnormal Threshold Alerts</h2>
  <table>
    <tr><th>Timestamp ({e(settings.display_timezone_label)})</th><th>Severity</th><th>Marker</th><th>Knee Angle</th><th>AI Risk</th><th>Details</th></tr>
    {alerts_rows}
  </table>

  <h2>Clinical Recommendations &amp; Follow-up</h2>
  <ul>{recs_li}</ul>

  <div class="sign-grid">
    <div><div class="sign-line"></div><strong>Clinician / Physiotherapist Signature</strong></div>
    <div><div class="sign-line"></div><strong>Registration / Facility ID</strong></div>
    <div><div class="sign-line"></div><strong>Next Follow-Up Screening Date</strong></div>
  </div>
  <div class="sub" style="margin-top:12px;border-top:1px solid #e2e8f0;padding-top:8px">
    <strong>Methodology Note:</strong> 100 Hz dual-MPU6050 (±4g / ±500°/s / 20 Hz DLPF), Flex resistance ratio, and Heel/Forefoot FSR symmetry analyzed against AAOS Knee ROM (-10°–135°, gait ~60°), Costello et al. Varus Velocity (≥28.1°/s), WHO BMI (18.5/25/30), and ACR screening criteria (≥45 yrs). Assistive screening tool; correlate clinically with standing AP radiographs (Kellgren–Lawrence).
  </div>
</div>
</body>
</html>"""

