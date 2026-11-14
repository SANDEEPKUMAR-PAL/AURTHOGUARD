"""Clinical foot-pressure meter calculations and interactive joint/foot stimulation synthesis."""

from __future__ import annotations

import math
import time
from typing import Any, Optional

from backend.config import settings
from backend.schemas import TelemetryIn


STIMULATION_PRESETS: list[dict[str, Any]] = [
    {
        "key": "standing_0",
        "label": "Stand 0°",
        "knee_angle_deg": 0.0,
        "thigh_pitch_deg": 0.0,
        "net_roll_deg": 0.5,
        "heel_fsr": 1750,
        "fore_fsr": 1680,
        "flex_stiffness": 0.15,
    },
    {
        "key": "walk_heel_strike",
        "label": "Heel Strike",
        "knee_angle_deg": 16.0,
        "thigh_pitch_deg": 22.0,
        "net_roll_deg": 2.0,
        "heel_fsr": 2950,
        "fore_fsr": 650,
        "flex_stiffness": 0.22,
    },
    {
        "key": "walk_mid_flex",
        "label": "Mid-Flex 45°",
        "knee_angle_deg": 45.0,
        "thigh_pitch_deg": 14.0,
        "net_roll_deg": 2.2,
        "heel_fsr": 1700,
        "fore_fsr": 1750,
        "flex_stiffness": 0.20,
    },
    {
        "key": "seated_90",
        "label": "Seated 90°",
        "knee_angle_deg": 90.0,
        "thigh_pitch_deg": 78.0,
        "net_roll_deg": 1.5,
        "heel_fsr": 420,
        "fore_fsr": 400,
        "flex_stiffness": 0.18,
    },
    {
        "key": "forefoot_push",
        "label": "Forefoot Push",
        "knee_angle_deg": 32.0,
        "thigh_pitch_deg": -12.0,
        "net_roll_deg": 2.5,
        "heel_fsr": 550,
        "fore_fsr": 3050,
        "flex_stiffness": 0.22,
    },
    {
        "key": "oa_stiff_offload",
        "label": "OA Stiff + Offload",
        "knee_angle_deg": 22.0,
        "thigh_pitch_deg": 8.0,
        "net_roll_deg": 9.5,
        "heel_fsr": 3250,
        "fore_fsr": 380,
        "flex_stiffness": 0.72,
    },
    {
        "key": "hyperflexion_alert",
        "label": "Hyperflexion 142°",
        "knee_angle_deg": 142.0,
        "thigh_pitch_deg": 65.0,
        "net_roll_deg": 8.0,
        "heel_fsr": 2200,
        "fore_fsr": 900,
        "flex_stiffness": 0.30,
    },
]


def compute_foot_pressure_metrics(
    heel_raw: int,
    fore_raw: int,
    heel_tare: int = 0,
    fore_tare: int = 0,
    imbalance_thresh: float = settings.fsr_imbalance_thresh,
) -> dict[str, Any]:
    """Compute plantar pressure percentages, Center-of-Pressure ratio, and Foot Pressure Meter needle."""
    heel_adj = max(0, int(heel_raw) - int(heel_tare))
    fore_adj = max(0, int(fore_raw) - int(fore_tare))
    total_adj = heel_adj + fore_adj

    if total_adj <= 100:
        return {
            "heel_raw": int(heel_raw),
            "fore_raw": int(fore_raw),
            "heel_adj": heel_adj,
            "fore_adj": fore_adj,
            "total_adj": total_adj,
            "heel_pct": 50,
            "fore_pct": 50,
            "heel_force_pct": round(min(100.0, heel_adj / settings.fsr_force_scale_divisor), 1),
            "fore_force_pct": round(min(100.0, fore_adj / settings.fsr_force_scale_divisor), 1),
            "total_load_pct": round(min(100.0, total_adj / settings.fsr_total_load_divisor), 1),
            "cop_ratio": 0.5,
            "imbalance_pct": 0.0,
            "meter_needle_deg": 0.0,
            "dominant_zone": "Unloaded",
            "status_level": "ok",
        }

    heel_pct = int(round((heel_adj / total_adj) * 100))
    fore_pct = 100 - heel_pct
    cop_ratio = round(fore_adj / total_adj, 3)  # 0.0 = pure heel, 0.5 = centered, 1.0 = pure forefoot
    imbalance_pct = round(abs(heel_adj - fore_adj) / total_adj * 100.0, 1)
    meter_needle_deg = round((cop_ratio - 0.5) * 180.0, 1)

    if imbalance_pct > imbalance_thresh:
        dominant_zone = "Heel Overload" if heel_adj > fore_adj else "Forefoot Overload"
        status_level = "fault"
    elif imbalance_pct > settings.fsr_imbalance_warning_thresh:
        dominant_zone = "Heel Dominant" if heel_adj > fore_adj else "Forefoot Dominant"
        status_level = "warn"
    else:
        dominant_zone = "Balanced"
        status_level = "ok"

    return {
        "heel_raw": int(heel_raw),
        "fore_raw": int(fore_raw),
        "heel_adj": heel_adj,
        "fore_adj": fore_adj,
        "total_adj": total_adj,
        "heel_pct": heel_pct,
        "fore_pct": fore_pct,
        "heel_force_pct": round(min(100.0, heel_adj / settings.fsr_force_scale_divisor), 1),
        "fore_force_pct": round(min(100.0, fore_adj / settings.fsr_force_scale_divisor), 1),
        "total_load_pct": round(min(100.0, total_adj / settings.fsr_total_load_divisor), 1),
        "cop_ratio": cop_ratio,
        "imbalance_pct": imbalance_pct,
        "meter_needle_deg": meter_needle_deg,
        "dominant_zone": dominant_zone,
        "status_level": status_level,
    }


def build_stimulation_packet(
    knee_angle_deg: float,
    heel_fsr: int,
    fore_fsr: int,
    thigh_pitch_deg: float = 10.0,
    net_roll_deg: float = 1.5,
    flex_stiffness: Optional[float] = None,
    calibration: Optional[dict[str, Any]] = None,
    thresholds: Optional[dict[str, float]] = None,
    step_idx: int = 0,
) -> dict[str, Any]:
    """Synthesize a calibrated 5-sensor telemetry frame for interactive joint/foot stimulation."""
    cal = calibration or {}
    t_off = float(cal.get("thigh_pitch_offset", 0.0))
    s_off = float(cal.get("shin_pitch_offset", 0.0))
    tr_off = float(cal.get("thigh_roll_offset", 0.0))
    sr_off = float(cal.get("shin_roll_offset", 0.0))
    t_pol = int(cal.get("thigh_polarity", 1)) or 1
    s_pol = int(cal.get("shin_polarity", 1)) or 1
    flex_straight = int(cal.get("flex_straight", 2048))
    flex_bent = int(cal.get("flex_bent", 1200))

    # Micro gait oscillation when multi-sample burst is injected so ML velocity/jerk stay physically valid
    wave = 1.2 * math.sin(step_idx * 0.65) if step_idx > 0 else 0.0
    knee_c = float(knee_angle_deg) + wave
    thigh_c = float(thigh_pitch_deg)
    shin_c = thigh_c + knee_c

    thigh_raw_p = t_off + (thigh_c / t_pol)
    shin_raw_p = s_off + (shin_c / s_pol)
    thigh_roll_c = 0.5 * float(net_roll_deg) * -1.0
    shin_roll_c = 0.5 * float(net_roll_deg)

    if flex_stiffness is not None and flex_stiffness > 0.45:
        flex_norm = max(0.0, min(1.5, (1.0 - float(flex_stiffness)) * (knee_c / 90.0)))
    else:
        flex_norm = max(0.0, min(1.5, knee_c / 90.0))
    flex_raw = int(round(flex_straight + flex_norm * (flex_bent - flex_straight)))
    flex_raw = max(20, min(4080, flex_raw))

    fp = compute_foot_pressure_metrics(
        heel_fsr,
        fore_fsr,
        heel_tare=int(cal.get("fsr_heel_tare", 0)),
        fore_tare=int(cal.get("fsr_fore_tare", 0)),
        imbalance_thresh=float((thresholds or {}).get("fsr_imbalance_thresh", 60.0)),
    )

    th = thresholds or {}
    tol = settings.angle_tolerance_deg
    k_min = float(th.get("knee_angle_min", -10.0))
    k_max = float(th.get("knee_angle_max", 135.0))
    imb_lim = float(th.get("fsr_imbalance_thresh", 60.0))
    stiff_lim = float(th.get("flex_stiff_thresh", 0.25))

    reasons: list[str] = []
    if knee_c < (k_min - tol):
        reasons.append(f"Knee hyper-extension (<{k_min:.0f}°)")
    elif knee_c > (k_max + tol):
        reasons.append(f"Knee hyper-flexion (>{k_max:.0f}°)")
    if (int(heel_fsr) + int(fore_fsr)) >= settings.fsr_active_min_total and fp["imbalance_pct"] > imb_lim:
        reasons.append(f"Gait imbalance (>{imb_lim:.0f}%)")
    if knee_c > 45.0 and flex_norm < stiff_lim:
        reasons.append("Knee stiffness detected")

    t_rad = math.radians(thigh_c)
    s_rad = math.radians(shin_c)
    pkt = {
        "type": "data",
        "source": "simulator",
        "timestamp": int(time.time() * 1000) % 10_000_000 + step_idx * 100,
        "calibrated": bool(calibration),
        "calibrationStep": 2 if cal.get("is_two_point") else (1 if calibration else 0),
        "thigh": {
            "online": True,
            "ax": round(math.sin(t_rad) * 0.25, 3),
            "ay": round(max(0.45, math.cos(t_rad)), 3),
            "az": 0.05,
            "gx": round(12.0 * math.cos(step_idx * 0.65), 2),
            "gy": 2.0,
            "gz": 1.0,
            "pitch": round(thigh_raw_p, 2),
            "roll": round(thigh_roll_c + tr_off, 2),
            "pitchC": round(thigh_c, 2),
            "rollC": round(thigh_roll_c, 2),
        },
        "shin": {
            "online": True,
            "ax": round(math.sin(s_rad) * 0.30, 3),
            "ay": round(max(0.45, math.cos(s_rad)), 3),
            "az": 0.08,
            "gx": round(18.0 * math.cos(step_idx * 0.65), 2),
            "gy": 3.0,
            "gz": 1.5,
            "pitch": round(shin_raw_p, 2),
            "roll": round(shin_roll_c + sr_off, 2),
            "pitchC": round(shin_c, 2),
            "rollC": round(shin_roll_c, 2),
        },
        "flex": flex_raw,
        "flexNorm": round(flex_norm, 3),
        "flexBaseline": flex_straight,
        "fsrHeel": int(heel_fsr),
        "fsrFore": int(fore_fsr),
        "kneeAngle": round(shin_raw_p - thigh_raw_p, 2),
        "kneeAngleC": round(knee_c, 2),
        "fsrImbalance": fp["imbalance_pct"],
        "abnormal": len(reasons) > 0,
        "reason": " | ".join(reasons),
    }

    # Ensure TelemetryIn schema compatibility cleanly
    TelemetryIn(**pkt)
    return pkt
