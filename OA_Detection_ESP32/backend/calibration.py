"""Per-patient 2-step biomechanical calibration and telemetry normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.config import settings
from backend.schemas import TelemetryIn


@dataclass
class CalibrationCollector:
    """Collects multi-sample standing (0°) and bent (90°) baselines on the backend."""

    step: int = 0  # 0=idle, 1=collecting step1, 2=collecting step2
    target_samples: int = settings.calibration_target_samples
    samples: list[TelemetryIn] = field(default_factory=list)
    step1_result: Optional[dict[str, Any]] = None

    def start(self, step: int, target_samples: int = settings.calibration_target_samples) -> None:
        self.step = step
        self.target_samples = target_samples
        self.samples.clear()

    def add_sample(self, telemetry: TelemetryIn) -> tuple[bool, int, Optional[dict[str, Any]]]:
        """Add a telemetry frame; returns (completed, progress_pct, result_dict)."""
        if self.step not in (1, 2):
            return False, 0, None
        self.samples.append(telemetry)
        progress = min(100, int((len(self.samples) * 100) / self.target_samples))
        if len(self.samples) < self.target_samples:
            return False, progress, None

        if self.step == 1:
            result = self._finalize_step1()
            self.step1_result = result
            self.step = 0
            return True, 100, result

        result = self._finalize_step2()
        self.step = 0
        return True, 100, result

    def _finalize_step1(self) -> dict[str, Any]:
        n = len(self.samples)
        return {
            "thigh_pitch_offset": round(sum(s.thigh.pitch for s in self.samples) / n, 3),
            "thigh_roll_offset": round(sum(s.thigh.roll for s in self.samples) / n, 3),
            "shin_pitch_offset": round(sum(s.shin.pitch for s in self.samples) / n, 3),
            "shin_roll_offset": round(sum(s.shin.roll for s in self.samples) / n, 3),
            "thigh_polarity": 1,
            "shin_polarity": 1,
            "flex_straight": int(round(sum(s.flex for s in self.samples) / n)),
            "flex_bent": settings.cal_flex_bent_default,
            "fsr_heel_tare": 0,
            "fsr_fore_tare": 0,
            "is_two_point": False,
        }

    def _finalize_step2(self) -> dict[str, Any]:
        n = len(self.samples)
        base = dict(self.step1_result or {
            "thigh_pitch_offset": 0.0,
            "thigh_roll_offset": 0.0,
            "shin_pitch_offset": 0.0,
            "shin_roll_offset": 0.0,
            "flex_straight": settings.cal_flex_straight_default,
            "fsr_heel_tare": 0,
            "fsr_fore_tare": 0,
        })
        avg_thigh = sum(s.thigh.pitch for s in self.samples) / n
        avg_shin = sum(s.shin.pitch for s in self.samples) / n
        avg_flex = int(round(sum(s.flex for s in self.samples) / n))

        delta_thigh = avg_thigh - base["thigh_pitch_offset"]
        delta_shin = avg_shin - base["shin_pitch_offset"]
        # Determine polarity so that a 90° knee bend produces positive flexion.
        # Flip whichever segment rotated more when inverted mounting yields negative delta.
        thigh_polarity = 1
        shin_polarity = 1
        if (delta_shin - delta_thigh) < 0:
            if abs(delta_thigh) > abs(delta_shin):
                thigh_polarity = -1
            else:
                shin_polarity = -1

        base["thigh_polarity"] = thigh_polarity
        base["shin_polarity"] = shin_polarity
        base["flex_bent"] = avg_flex if abs(avg_flex - base["flex_straight"]) >= 20 else base["flex_straight"] - 800
        base["is_two_point"] = True
        return base


def _wrap_deg(angle: float) -> float:
    """Wrap angle difference into [-180, 180] degrees to prevent 360-degree jumps near +-180 deg mounts."""
    return ((angle + 180.0) % 360.0) - 180.0


def normalize_telemetry_with_calibration(
    telemetry: TelemetryIn, calibration: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Compute calibrated clinical knee flexion (0° straight -> 90° bent), flex ratio, and 5-sensor summary."""
    heel_tare = int(calibration.get("fsr_heel_tare", 0)) if calibration else 0
    fore_tare = int(calibration.get("fsr_fore_tare", 0)) if calibration else 0
    adj_heel = max(0, int(telemetry.fsrHeel) - heel_tare)
    adj_fore = max(0, int(telemetry.fsrFore) - fore_tare)

    total_fsr = adj_heel + adj_fore
    heel_pct = int(round((adj_heel / total_fsr) * 100)) if total_fsr > 0 else 50
    fore_pct = 100 - heel_pct if total_fsr > 0 else 50
    fsr_imbalance_c = round(abs(adj_heel - adj_fore) / total_fsr * 100.0, 1) if total_fsr > 100 else 0.0

    if not calibration:
        # Even without calibration, treat uncalibrated ESP32 flexion cleanly
        flex_norm = telemetry.flexNorm
        if flex_norm is None:
            flex_norm = max(0.0, min(1.5, (settings.cal_flex_straight_default - telemetry.flex) / 1000.0))
        knee_c = telemetry.kneeAngleC if telemetry.kneeAngleC is not None else telemetry.kneeAngle
        t_pitch_c = telemetry.thigh.pitchC if telemetry.thigh.pitchC is not None else telemetry.thigh.pitch
        t_roll_c = telemetry.thigh.rollC if telemetry.thigh.rollC is not None else telemetry.thigh.roll
        s_pitch_c = telemetry.shin.pitchC if telemetry.shin.pitchC is not None else telemetry.shin.pitch
        s_roll_c = telemetry.shin.rollC if telemetry.shin.rollC is not None else telemetry.shin.roll
        return {
            "thigh_pitch_c": t_pitch_c,
            "thigh_roll_c": t_roll_c,
            "shin_pitch_c": s_pitch_c,
            "shin_roll_c": s_roll_c,
            "knee_angle_c": round(float(knee_c), 2),
            "flex_norm": round(float(flex_norm), 3),
            "flex_pct": int(round(float(flex_norm) * 100)),
            "heel_pct": heel_pct,
            "fore_pct": fore_pct,
            "fsr_heel_adj": adj_heel,
            "fsr_fore_adj": adj_fore,
            "fsr_imbalance_c": fsr_imbalance_c if (heel_tare or fore_tare) else round(float(telemetry.fsrImbalance), 1),
            "net_roll_deg": round(float(s_roll_c) - float(t_roll_c), 2),
        }

    t_pol = int(calibration.get("thigh_polarity", 1)) or 1
    s_pol = int(calibration.get("shin_polarity", 1)) or 1
    t_pitch_c = t_pol * _wrap_deg(telemetry.thigh.pitch - float(calibration.get("thigh_pitch_offset", 0.0)))
    t_roll_c = _wrap_deg(telemetry.thigh.roll - float(calibration.get("thigh_roll_offset", 0.0)))
    s_pitch_c = s_pol * _wrap_deg(telemetry.shin.pitch - float(calibration.get("shin_pitch_offset", 0.0)))
    s_roll_c = _wrap_deg(telemetry.shin.roll - float(calibration.get("shin_roll_offset", 0.0)))

    # If the ESP32 itself already computed calibrated clinical flexion and is marked calibrated,
    # prefer ESP32's complementary-filtered angle when close, otherwise compute from corrected pitches.
    if telemetry.calibrated and telemetry.kneeAngleC is not None:
        knee_c = float(telemetry.kneeAngleC)
    else:
        knee_c = max(-30.0, min(160.0, s_pitch_c - t_pitch_c))

    flex_straight = int(calibration.get("flex_straight", settings.cal_flex_straight_default))
    flex_bent = int(calibration.get("flex_bent", settings.cal_flex_bent_default))
    span = flex_bent - flex_straight
    if abs(span) >= 20:
        flex_norm = max(0.0, min(1.5, (telemetry.flex - flex_straight) / span))
    else:
        flex_norm = 0.0

    return {
        "thigh_pitch_c": round(t_pitch_c, 2),
        "thigh_roll_c": round(t_roll_c, 2),
        "shin_pitch_c": round(s_pitch_c, 2),
        "shin_roll_c": round(s_roll_c, 2),
        "knee_angle_c": round(knee_c, 2),
        "flex_norm": round(flex_norm, 3),
        "flex_pct": int(round(flex_norm * 100)),
        "heel_pct": heel_pct,
        "fore_pct": fore_pct,
        "fsr_heel_adj": adj_heel,
        "fsr_fore_adj": adj_fore,
        "fsr_imbalance_c": fsr_imbalance_c if (heel_tare or fore_tare) else round(float(telemetry.fsrImbalance), 1),
        "net_roll_deg": round(s_roll_c - t_roll_c, 2),
    }

