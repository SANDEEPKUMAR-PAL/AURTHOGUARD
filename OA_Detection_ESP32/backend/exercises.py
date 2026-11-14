"""Guided clinical OA screening exercises fusing all 5 wearable sensors."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.i18n import format_cue, get_localized_exercise, normalize_lang


EXERCISE_CATALOG: list[dict[str, Any]] = [
    {
        "key": "normal_walk",
        "name": "Normal Gait Walk Test",
        "category": "Dynamic Gait",
        "target_reps": 6,
        "target_min_deg": 0.0,
        "target_max_deg": 65.0,
        "rep_low_thresh": 18.0,
        "rep_high_thresh": 44.0,
        "sensors_used": ["Thigh IMU", "Shin IMU", "Knee Flex", "Heel FSR", "Forefoot FSR"],
        "instructions": "Walk steadily across the floor. System tracks stride ROM (0°–65°), heel-to-toe pressure transfer, and lateral joint stability.",
        "sim_profile": "normal",
    },
    {
        "key": "seated_flex_ext",
        "name": "Seated Flexion–Extension Sweep",
        "category": "Joint ROM & Stiffness",
        "target_reps": 5,
        "target_min_deg": 0.0,
        "target_max_deg": 110.0,
        "rep_low_thresh": 22.0,
        "rep_high_thresh": 80.0,
        "sensors_used": ["Thigh IMU", "Shin IMU", "Knee Flex"],
        "instructions": "Sit upright, bend knee slowly toward 110°, then extend completely straight to 0°. Detects flexion contracture and tendon stiffness.",
        "sim_profile": "ex_seated_flex",
    },
    {
        "key": "sit_to_stand",
        "name": "5× Sit-to-Stand Chair Transfer",
        "category": "Functional Strength & Load",
        "target_reps": 5,
        "target_min_deg": 0.0,
        "target_max_deg": 90.0,
        "rep_low_thresh": 20.0,
        "rep_high_thresh": 68.0,
        "sensors_used": ["Thigh IMU", "Shin IMU", "Knee Flex", "Heel FSR", "Forefoot FSR"],
        "instructions": "Stand up smoothly from seated 90° without hand support and sit back down. Tracks thigh tilt drive, extension speed, and plantar weight shift.",
        "sim_profile": "ex_sit_to_stand",
    },
    {
        "key": "mini_squat",
        "name": "Loaded Mini-Squat Stability",
        "category": "Varus/Valgus Stability",
        "target_reps": 5,
        "target_min_deg": 0.0,
        "target_max_deg": 60.0,
        "rep_low_thresh": 15.0,
        "rep_high_thresh": 42.0,
        "sensors_used": ["Thigh IMU", "Shin IMU", "Knee Flex", "Heel FSR", "Forefoot FSR"],
        "instructions": "Perform controlled shallow squats to ~55°–65° while keeping heel & forefoot grounded. Measures IMU roll wobble and load symmetry.",
        "sim_profile": "ex_mini_squat",
    },
    {
        "key": "weight_shift",
        "name": "Heel–Toe Plantar Weight Shift",
        "category": "Proprioception & FSR",
        "target_reps": 6,
        "target_min_deg": 0.0,
        "target_max_deg": 15.0,
        "rep_low_thresh": 0.0,
        "rep_high_thresh": 15.0,
        "sensors_used": ["Heel FSR", "Forefoot FSR", "Thigh IMU", "Shin IMU", "Knee Flex"],
        "instructions": "Stand with knee straight (0°–15°) and rock weight deliberately between heel and forefoot. Detects antalgic offloading and posture lock.",
        "sim_profile": "ex_weight_shift",
    },
]

EXERCISE_MAP: dict[str, dict[str, Any]] = {ex["key"]: ex for ex in EXERCISE_CATALOG}


def get_localized_catalog(lang: str | None = "en") -> list[dict[str, Any]]:
    """Return the exercise catalog with localized name, category, and instructions."""
    l = normalize_lang(lang)
    localized: list[dict[str, Any]] = []
    for ex in EXERCISE_CATALOG:
        text = get_localized_exercise(ex["key"], l)
        localized.append({**ex, **text, "lang": l})
    return localized


@dataclass
class ExerciseTracker:
    """Real-time multi-sensor evaluator for an active guided OA screening exercise."""

    trial_id: Optional[int] = None
    exercise_key: str = ""
    spec: dict[str, Any] = field(default_factory=dict)
    active: bool = False
    completed_reps: int = 0
    lang: str = "en"
    monitored_leg: str = "Left"
    _phase: str = "extended"  # extended | flexed  (or heel | fore for weight_shift)
    _angles: list[float] = field(default_factory=list)
    _flex_angles: list[float] = field(default_factory=list)
    _imbalances: list[float] = field(default_factory=list)
    _roll_wobbles: list[float] = field(default_factory=list)
    _heel_pcts: list[float] = field(default_factory=list)
    _live_cue: str = "Ready to begin exercise"

    def set_language(self, lang: str | None) -> None:
        self.lang = normalize_lang(lang)
        if not self._angles and self.exercise_key:
            self._live_cue = get_localized_exercise(self.exercise_key, self.lang)["instructions"]
        elif not self.active:
            self._live_cue = format_cue(self.lang, "ready")

    def start(
        self,
        trial_id: int,
        exercise_key: str,
        lang: str | None = "en",
        monitored_leg: str = "Left",
    ) -> dict[str, Any]:
        spec = EXERCISE_MAP[exercise_key]
        self.trial_id = trial_id
        self.exercise_key = exercise_key
        self.spec = spec
        self.active = True
        self.completed_reps = 0
        self.lang = normalize_lang(lang)
        self.monitored_leg = monitored_leg if monitored_leg in ("Left", "Right") else "Left"
        self._phase = "heel" if exercise_key == "weight_shift" else "extended"
        self._angles.clear()
        self._flex_angles.clear()
        self._imbalances.clear()
        self._roll_wobbles.clear()
        self._heel_pcts.clear()
        self._live_cue = get_localized_exercise(exercise_key, self.lang)["instructions"]
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        snap = self.snapshot()
        self.active = False
        return snap

    def update(
        self,
        knee_angle_c: float,
        flex_norm: float,
        thigh_roll_c: float,
        shin_roll_c: float,
        fsr_heel: int,
        fsr_fore: int,
        fsr_imbalance: float,
    ) -> dict[str, Any]:
        if not self.active:
            return self.snapshot()

        self._angles.append(float(knee_angle_c))
        self._flex_angles.append(float(flex_norm * 90.0))
        self._imbalances.append(float(fsr_imbalance))
        wobble = math.sqrt(0.5 * (thigh_roll_c ** 2 + shin_roll_c ** 2))
        self._roll_wobbles.append(wobble)

        total_fsr = fsr_heel + fsr_fore
        heel_pct = (fsr_heel / total_fsr * 100.0) if total_fsr > 50 else 50.0
        self._heel_pcts.append(heel_pct)

        if self.exercise_key == "weight_shift":
            if self._phase == "heel" and heel_pct < 38.0:
                self._phase = "fore"
                self._live_cue = format_cue(self.lang, "ws_fore_ok")
            elif self._phase == "fore" and heel_pct > 62.0:
                self._phase = "heel"
                self.completed_reps += 1
                self._live_cue = format_cue(self.lang, "ws_heel_rep", rep=self.completed_reps)
            elif knee_angle_c > 20.0:
                self._live_cue = format_cue(self.lang, "ws_straight")
            else:
                self._live_cue = format_cue(self.lang, "ws_rock")
        else:
            low_t = float(self.spec["rep_low_thresh"])
            high_t = float(self.spec["rep_high_thresh"])
            target_max = float(self.spec["target_max_deg"])

            if self._phase == "extended" and knee_angle_c >= high_t:
                self._phase = "flexed"
                self._live_cue = format_cue(self.lang, "flexed_hold", angle=f"{knee_angle_c:.0f}")
            elif self._phase == "flexed" and knee_angle_c <= low_t:
                self._phase = "extended"
                self.completed_reps += 1
                self._live_cue = format_cue(
                    self.lang,
                    "rep_done",
                    rep=self.completed_reps,
                    total=self.spec["target_reps"],
                )
            elif self._phase == "extended":
                self._live_cue = format_cue(
                    self.lang,
                    "bend",
                    target=f"{target_max:.0f}",
                    angle=f"{knee_angle_c:.0f}",
                )
            else:
                self._live_cue = format_cue(self.lang, "extend", angle=f"{knee_angle_c:.0f}")

            if wobble > 7.5:
                self._live_cue += f" — {format_cue(self.lang, 'wobble')}"

        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        if not self.spec:
            return {"active": False, "lang": self.lang, "monitored_leg": self.monitored_leg}

        loc = get_localized_exercise(self.exercise_key, self.lang)
        n = len(self._angles)
        target_reps = int(self.spec.get("target_reps", 5))
        if n == 0:
            return {
                "active": self.active,
                "lang": self.lang,
                "monitored_leg": self.monitored_leg,
                "trial_id": self.trial_id,
                "exercise_key": self.exercise_key,
                "exercise_name": loc["name"],
                "target_reps": target_reps,
                "completed_reps": 0,
                "progress_pct": 0,
                "target_min_deg": self.spec["target_min_deg"],
                "target_max_deg": self.spec["target_max_deg"],
                "live_cue": self._live_cue,
                "quality_score": 100.0,
                "finding": "Pending",
                "finding_label": format_cue(self.lang, "Pending"),
                "avg_rom_deg": 0.0,
                "max_flexion_deg": 0.0,
                "min_flexion_deg": 0.0,
                "avg_imbalance_pct": 0.0,
                "roll_wobble_deg": 0.0,
                "flex_agreement_pct": 100.0,
                "markers": [],
            }

        min_a = min(self._angles)
        max_a = max(self._angles)
        rom = max_a - min_a
        avg_imb = sum(self._imbalances) / n
        wobble_rms = math.sqrt(sum(w * w for w in self._roll_wobbles) / n)
        mean_flex_diff = sum(abs(a - fa) for a, fa in zip(self._angles, self._flex_angles)) / n
        flex_agree = max(0.0, min(100.0, 100.0 - mean_flex_diff * 1.4))

        markers: list[str] = []
        penalty = 0.0

        if self.exercise_key != "weight_shift":
            expected_rom = max(25.0, (self.spec["target_max_deg"] - self.spec["target_min_deg"]) * 0.65)
            if rom < expected_rom:
                markers.append(f"Reduced ROM ({rom:.1f}° vs {expected_rom:.0f}° expected)")
                penalty += min(35.0, (expected_rom - rom) * 0.9)
            if min_a > 5.0:
                markers.append(f"AAOS Flexion Contracture / Ext Deficit (+{min_a:.1f}° > 5°)")
                penalty += min(25.0, (min_a - 5.0) * 1.4)
        else:
            heel_span = max(self._heel_pcts) - min(self._heel_pcts)
            if heel_span < 30.0:
                markers.append(f"Limited Heel–Toe Load Transfer ({heel_span:.0f}% span)")
                penalty += 25.0
            if max_a > 20.0:
                markers.append(f"Knee Instability During Stance ({max_a:.1f}° > 20°)")
                penalty += 15.0

        if wobble_rms > 6.5:
            markers.append(f"Elevated Frontal Varus/Valgus Wobble ({wobble_rms:.1f}° > 6.5°)")
            penalty += min(22.0, (wobble_rms - 5.5) * 2.2)

        if self.exercise_key in ("normal_walk", "mini_squat", "sit_to_stand") and avg_imb > 52.0:
            markers.append(f"Asymmetric Plantar Loading ({avg_imb:.1f}%)")
            penalty += min(25.0, (avg_imb - 45.0) * 0.9)

        if flex_agree < 55.0:
            markers.append(f"Flex-IMU Guarding Discrepancy ({flex_agree:.0f}% match)")
            penalty += 12.0

        quality = round(max(0.0, min(100.0, 100.0 - penalty)), 1)
        if quality >= 72.0:
            finding = "Normal"
        elif quality >= 45.0:
            finding = "Mild Deficit"
        else:
            finding = "High Risk Marker"

        if not markers:
            markers.append("Full target motion achieved across all 5 sensors")

        return {
            "active": self.active,
            "lang": self.lang,
            "monitored_leg": self.monitored_leg,
            "trial_id": self.trial_id,
            "exercise_key": self.exercise_key,
            "exercise_name": loc["name"],
            "target_reps": target_reps,
            "completed_reps": self.completed_reps,
            "progress_pct": min(100, int(self.completed_reps * 100 / max(1, target_reps))),
            "target_min_deg": self.spec["target_min_deg"],
            "target_max_deg": self.spec["target_max_deg"],
            "live_cue": self._live_cue,
            "quality_score": quality,
            "finding": finding,
            "finding_label": format_cue(self.lang, finding),
            "avg_rom_deg": round(rom, 1),
            "max_flexion_deg": round(max_a, 1),
            "min_flexion_deg": round(min_a, 1),
            "avg_imbalance_pct": round(avg_imb, 1),
            "roll_wobble_deg": round(wobble_rms, 1),
            "flex_agreement_pct": round(flex_agree, 1),
            "markers": markers,
        }

