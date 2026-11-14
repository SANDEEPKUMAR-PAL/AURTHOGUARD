"""AI-Assisted Biomechanical Gait Feature Extraction & OA Risk Scoring Engine."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backend.config import settings
from backend.schemas import AIRiskSnapshot, GaitFeatures, RiskDrivers


FEATURE_NAMES = [
    "rom_deg",
    "peak_flexion_deg",
    "extension_deficit_deg",
    "mean_angular_vel_dps",
    "jerk_index",
    "fsr_mean_imbalance_pct",
    "fsr_peak_loading_ratio",
    "flex_stiffness_index",
]


def _generate_synthetic_biomechanics_dataset(
    samples_per_class: int = 350, seed: int = 42
) -> tuple[np.ndarray, np.ndarray]:
    """Generate a clinically grounded biomechanical dataset for early OA screening.

    Research References:
      - AAOS Goniometry & AUC: Normal ROM 0°–135° (phys. hyperext -10°), flexion contracture >5°, flexion deficit <110°
      - Zeni & Higginson (Clin. Biomech., 2009) & PubMed KOA Gait: Healthy gait ROM ~58°–65° vs Early/Severe OA <48°
      - Wearable IMU Varus Thrust / Jerk Studies (Costello et al., 2020): Higher midstance tibial jerk & lower ang vel in OA

    Classes:
      0 = Low Risk (Healthy gait: ~60°±5.5° ROM, extension deficit ≤4.8°, balanced loading)
      1 = Moderate Risk (Early OA KL 1–2: ROM ~38°±5.5°, contracture >5°, mild stiffness & asymmetry)
      2 = High Risk (Severe OA KL 3–4 / Hyperflexion: ROM <30° or >135° spike, severe asymmetry >65%, high jerk)
    """
    rng = np.random.default_rng(seed)

    # Class 0: Healthy Adult Gait
    c0 = np.column_stack([
        np.clip(rng.normal(60.0, 5.5, samples_per_class), 49.0, 82.0),
        np.clip(rng.normal(63.0, 6.0, samples_per_class), 50.0, 90.0),
        np.clip(rng.uniform(0.0, 4.8, samples_per_class), 0.0, 5.0),
        np.clip(rng.normal(118.0, 18.0, samples_per_class), 78.0, 210.0),
        np.clip(rng.normal(4.2, 1.4, samples_per_class), 1.0, 8.5),
        np.clip(rng.normal(24.0, 8.0, samples_per_class), 4.0, 44.0),
        np.clip(rng.normal(0.55, 0.12, samples_per_class), 0.25, 1.0),
        np.clip(rng.normal(0.20, 0.08, samples_per_class), 0.0, 0.42),
    ])

    # Class 1: Early / Moderate OA Risk Markers (KL Grades 1–2)
    c1 = np.column_stack([
        np.clip(rng.normal(38.0, 5.5, samples_per_class), 25.0, 49.0),
        np.clip(rng.normal(49.0, 6.5, samples_per_class), 34.0, 72.0),
        np.clip(rng.normal(9.5, 3.2, samples_per_class), 5.2, 18.0),
        np.clip(rng.normal(64.0, 14.0, samples_per_class), 30.0, 102.0),
        np.clip(rng.normal(10.8, 2.6, samples_per_class), 6.0, 18.0),
        np.clip(rng.normal(48.0, 8.5, samples_per_class), 32.0, 65.0),
        np.clip(rng.normal(0.28, 0.08, samples_per_class), 0.10, 0.48),
        np.clip(rng.normal(0.52, 0.10, samples_per_class), 0.32, 0.76),
    ])

    # Class 2: High Risk / Severe OA Markers (KL Grades 3–4 & acute hyperflexion spikes)
    c2_stiff = np.column_stack([
        np.clip(rng.normal(19.0, 5.0, samples_per_class // 2), 4.0, 30.0),
        np.clip(rng.normal(36.0, 9.0, samples_per_class // 2), 12.0, 65.0),
        np.clip(rng.normal(19.5, 5.5, samples_per_class // 2), 10.5, 42.0),
        np.clip(rng.normal(32.0, 11.0, samples_per_class // 2), 5.0, 65.0),
        np.clip(rng.normal(18.5, 4.0, samples_per_class // 2), 11.0, 35.0),
        np.clip(rng.normal(72.0, 9.5, samples_per_class // 2), 55.0, 98.0),
        np.clip(rng.normal(0.14, 0.06, samples_per_class // 2), 0.01, 0.30),
        np.clip(rng.normal(0.80, 0.09, samples_per_class // 2), 0.58, 1.0),
    ])
    c2_hyper = np.column_stack([
        np.clip(rng.normal(85.0, 18.0, samples_per_class - samples_per_class // 2), 30.0, 150.0),
        np.clip(rng.normal(142.0, 8.0, samples_per_class - samples_per_class // 2), 125.0, 165.0),
        np.clip(rng.normal(14.0, 6.0, samples_per_class - samples_per_class // 2), 0.0, 35.0),
        np.clip(rng.normal(165.0, 30.0, samples_per_class - samples_per_class // 2), 90.0, 260.0),
        np.clip(rng.normal(22.0, 5.0, samples_per_class - samples_per_class // 2), 12.0, 40.0),
        np.clip(rng.normal(68.0, 12.0, samples_per_class - samples_per_class // 2), 45.0, 96.0),
        np.clip(rng.normal(0.22, 0.10, samples_per_class - samples_per_class // 2), 0.02, 0.55),
        np.clip(rng.normal(0.65, 0.14, samples_per_class - samples_per_class // 2), 0.35, 1.0),
    ])
    c2 = np.vstack([c2_stiff, c2_hyper])

    X = np.vstack([c0, c1, c2])
    y = np.concatenate([
        np.zeros(samples_per_class, dtype=int),
        np.ones(samples_per_class, dtype=int),
        np.full(samples_per_class, 2, dtype=int),
    ])
    return X, y


class OARiskModel:
    """Loads or trains the scikit-learn RandomForest OA Risk Classifier."""

    def __init__(self, model_path: Path | str) -> None:
        self.model_path = Path(model_path)
        self.pipeline: Optional[Pipeline] = None

    def ensure_ready(self) -> None:
        """Load persisted model from disk or train & save a fresh one."""
        if self.model_path.exists():
            try:
                loaded = joblib.load(self.model_path)
                if isinstance(loaded, Pipeline):
                    self.pipeline = loaded
                    return
            except Exception:
                pass

        X, y = _generate_synthetic_biomechanics_dataset()
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", RandomForestClassifier(n_estimators=120, max_depth=10, random_state=42)),
        ])
        pipeline.fit(X, y)
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(pipeline, self.model_path)
        self.pipeline = pipeline

    def score_features(self, features: GaitFeatures) -> tuple[float, str, float, list[str]]:
        """Score extracted gait features -> (risk_score, tier, confidence, risk_markers)."""
        if self.pipeline is None:
            self.ensure_ready()

        vec = np.array([[
            features.rom_deg,
            features.peak_flexion_deg,
            features.extension_deficit_deg,
            features.mean_angular_vel_dps,
            features.jerk_index,
            features.fsr_mean_imbalance_pct,
            features.fsr_peak_loading_ratio,
            features.flex_stiffness_index,
        ]], dtype=float)

        probs = self.pipeline.predict_proba(vec)[0]  # [p_low, p_mod, p_high]
        ml_score = float(probs[1] * 50.0 + probs[2] * 100.0)
        confidence = float(np.max(probs))

        markers: list[str] = []
        bonus = 0.0

        # Static / Seated / Standing rest posture protection:
        # When wearer is sitting or standing calmly (minimal cyclic ROM & low angular velocity),
        # do not misclassify low dynamic ROM or unchanged flex strip as stiff-knee OA walking.
        is_static_posture = (
            features.rom_deg < 14.0
            and features.mean_angular_vel_dps < 10.0
            and features.peak_flexion_deg <= (settings.knee_angle_max + settings.angle_tolerance_deg)
            and features.fsr_mean_imbalance_pct <= 58.0
            and features.varus_vel_dps < settings.varus_velocity_thresh
        )
        if is_static_posture:
            rest_score = round(min(22.0, 6.0 + features.fsr_mean_imbalance_pct * 0.25), 1)
            return (
                rest_score,
                "Low",
                round(confidence, 3),
                ["Calm static posture (Standing / Seated rest) — walk to score dynamic gait"],
            )

        # Calm stationary standing straight (<=10 deg ext deficit) or seated bent (>=42 deg)
        # with plantar/heel-rail FSR bias: cap to Moderate rather than false Critical Gait OA.
        is_static_postural_hold = (
            features.rom_deg < 12.0
            and features.mean_angular_vel_dps < 8.0
            and (features.extension_deficit_deg <= 10.0 or features.peak_flexion_deg >= 42.0)
            and features.peak_flexion_deg <= (settings.knee_angle_max + settings.angle_tolerance_deg)
            and features.varus_vel_dps < settings.varus_velocity_thresh
        )
        if is_static_postural_hold:
            bias_score = round(min(52.0, 28.0 + features.fsr_mean_imbalance_pct * 0.24), 1)
            return (
                bias_score,
                "Moderate",
                round(confidence, 3),
                [
                    f"Static posture FSR load bias ({features.fsr_mean_imbalance_pct:.0f}%) — check Heel/Fore FSR tare or walk for dynamic gait score"
                ],
            )

        if features.rom_deg < 48.0:
            markers.append(f"Restricted Gait Knee ROM ({features.rom_deg:.1f}° < 48° norm)")
        if features.peak_flexion_deg > settings.knee_angle_max:
            markers.append(f"AAOS Hyperflexion Excursion ({features.peak_flexion_deg:.1f}° > {settings.knee_angle_max:.0f}°)")
            bonus += 15.0
        if features.extension_deficit_deg > 5.0:
            markers.append(f"AAOS Flexion Contracture / Ext Deficit (+{features.extension_deficit_deg:.1f}° > 5°)")
        if features.fsr_mean_imbalance_pct > 55.0:
            markers.append(f"Asymmetric Plantar Loading ({features.fsr_mean_imbalance_pct:.1f}%)")
            if features.fsr_mean_imbalance_pct > 65.0:
                bonus += 10.0
        if features.jerk_index > 10.0:
            markers.append(f"Elevated Tibial Jerk Instability ({features.jerk_index:.1f})")
        if features.flex_stiffness_index > 0.55:
            markers.append(f"High Flex Stiffness Index ({features.flex_stiffness_index:.2f})")
        if features.varus_vel_dps >= settings.varus_velocity_thresh:
            markers.append(f"Dynamic Varus Thrust Velocity ({features.varus_vel_dps:.1f} ≥ {settings.varus_velocity_thresh:.1f} °/s)")
            bonus += 8.0
        if features.stance_pct > 64.0 and features.rom_deg < 48.0:
            markers.append(f"Prolonged Antalgic Stance ({features.stance_pct:.0f}% > 64%)")

        score = round(float(np.clip(ml_score + bonus, 0.0, 100.0)), 1)
        if score < 35.0:
            tier = "Low"
        elif score <= 65.0:
            tier = "Moderate"
        else:
            tier = "High"

        if not markers:
            markers.append("Healthy gait biomechanics within baseline range")

        return score, tier, round(confidence, 3), markers


class GaitWindowAnalyzer:
    """Sliding-window biomechanical feature extractor for real-time 10 Hz streams."""

    def __init__(
        self,
        model: OARiskModel,
        window_size: int = 40,
        step_interval: int = 5,
        sample_rate_hz: float = 10.0,
    ) -> None:
        self.model = model
        self.window_size = window_size
        self.step_interval = step_interval
        self.sample_rate_hz = sample_rate_hz
        self._angles: deque[float] = deque(maxlen=window_size)
        self._flex_norms: deque[float] = deque(maxlen=window_size)
        self._heel: deque[float] = deque(maxlen=window_size)
        self._fore: deque[float] = deque(maxlen=window_size)
        self._imbalance: deque[float] = deque(maxlen=window_size)
        self._net_rolls: deque[float] = deque(maxlen=window_size)
        self._impact_g: deque[float] = deque(maxlen=window_size)
        self._samples_since_eval = 0
        self.latest_snapshot = AIRiskSnapshot(window_samples=0, ready=False, oa_risk_tier="Pending")

    def reset(self) -> None:
        self._angles.clear()
        self._flex_norms.clear()
        self._heel.clear()
        self._fore.clear()
        self._imbalance.clear()
        self._net_rolls.clear()
        self._impact_g.clear()
        self._samples_since_eval = 0
        self.latest_snapshot = AIRiskSnapshot(window_samples=0, ready=False, oa_risk_tier="Pending")

    def add_sample(
        self,
        knee_angle_c: float,
        flex_norm: float,
        fsr_heel: int,
        fsr_fore: int,
        fsr_imbalance: float,
        net_roll_deg: float = 0.0,
        shin_accel_g: float = 1.0,
    ) -> AIRiskSnapshot:
        """Append one sample and re-evaluate AI risk when buffer warms up / every step_interval."""
        self._angles.append(float(knee_angle_c))
        self._flex_norms.append(float(flex_norm))
        self._heel.append(float(fsr_heel))
        self._fore.append(float(fsr_fore))
        self._imbalance.append(float(fsr_imbalance))
        self._net_rolls.append(float(net_roll_deg))
        self._impact_g.append(float(shin_accel_g))
        self._samples_since_eval += 1

        min_warmup = min(15, self.window_size)
        if len(self._angles) < min_warmup:
            self.latest_snapshot = AIRiskSnapshot(
                window_samples=len(self._angles),
                ready=False,
                oa_risk_tier="Pending",
                markers=[f"Collecting gait baseline ({len(self._angles)}/{min_warmup} samples)..."],
            )
            return self.latest_snapshot

        if self._samples_since_eval >= self.step_interval or not self.latest_snapshot.ready:
            self._samples_since_eval = 0
            self.latest_snapshot = self.evaluate_current_window()

        return self.latest_snapshot

    def evaluate_current_window(self) -> AIRiskSnapshot:
        """Extract features from the current deque and score with OARiskModel."""
        angles = np.asarray(self._angles, dtype=float)
        flex_norms = np.asarray(self._flex_norms, dtype=float)
        heel = np.asarray(self._heel, dtype=float)
        fore = np.asarray(self._fore, dtype=float)
        imbalance = np.asarray(self._imbalance, dtype=float)

        rom = float(np.max(angles) - np.min(angles))
        peak_flex = float(np.max(angles))
        ext_deficit = float(max(0.0, np.min(angles)))

        vel = np.abs(np.diff(angles)) * self.sample_rate_hz if len(angles) > 1 else np.array([0.0])
        mean_vel = float(np.mean(vel))

        acc = np.diff(angles, n=2) * (self.sample_rate_hz ** 2) if len(angles) > 2 else np.array([0.0])
        jerk = float(np.sqrt(np.mean(acc ** 2)) / 100.0)

        loaded_mask = (heel + fore) > 100.0
        mean_imb = float(np.mean(imbalance[loaded_mask])) if np.any(loaded_mask) else float(np.mean(imbalance))

        total_mean = float(np.mean(heel + fore))
        peak_loading_ratio = float(np.std(heel - fore) / (total_mean + 1.0)) if total_mean > 1.0 else 0.0

        flex_range = float(np.max(flex_norms) - np.min(flex_norms))
        flex_stiffness = float(np.clip(1.0 - flex_range, 0.0, 1.0))

        # Universal spatiotemporal & frontal stability biomarkers (independent of body size)
        duration_sec = max(0.2, (len(angles) - 1) / self.sample_rate_hz)
        cadence_spm = 0.0
        if rom >= 15.0 and len(angles) >= 6:
            centered = angles - np.mean(angles)
            up_crossings = int(np.sum((centered[:-1] < 0.0) & (centered[1:] >= 0.0)))
            cadence_spm = round(min(200.0, (up_crossings / duration_sec) * 60.0 * 2.0), 1)

        total_loads = heel + fore
        if float(np.min(total_loads)) < 250.0 and float(np.max(total_loads)) > 400.0:
            stance_pct = round(float(np.mean(total_loads >= 250.0) * 100.0), 1)
        elif rom >= 15.0:
            min_a = float(np.min(angles))
            stance_pct = round(float(np.mean(angles <= (min_a + 0.55 * rom)) * 100.0), 1)
        else:
            stance_pct = 0.0

        rolls = np.asarray(self._net_rolls, dtype=float)
        varus_vel_dps = (
            float(np.max(np.abs(np.diff(rolls))) * self.sample_rate_hz)
            if len(rolls) > 1
            else 0.0
        )
        impacts = np.asarray(self._impact_g, dtype=float)
        peak_impact_g = float(np.max(impacts)) if len(impacts) else 1.0

        features = GaitFeatures(
            rom_deg=round(rom, 2),
            peak_flexion_deg=round(peak_flex, 2),
            extension_deficit_deg=round(ext_deficit, 2),
            mean_angular_vel_dps=round(mean_vel, 2),
            jerk_index=round(jerk, 2),
            fsr_mean_imbalance_pct=round(mean_imb, 2),
            fsr_peak_loading_ratio=round(peak_loading_ratio, 3),
            flex_stiffness_index=round(flex_stiffness, 3),
            cadence_spm=cadence_spm,
            stance_pct=stance_pct,
            varus_vel_dps=round(varus_vel_dps, 1),
            peak_impact_g=round(peak_impact_g, 2),
        )

        score, tier, confidence, markers = self.model.score_features(features)
        # If the wearer transitioned earlier in the 4s window (e.g. standing -> sitting) and has now
        # held a calm straight (<=10 deg) or seated (>=42 deg) rest for the trailing >=15 samples (1.5s),
        # prevent stale transition ROM from triggering false High Risk / Critical alarms.
        if tier == "High" and len(angles) >= 20:
            tail = angles[-15:]
            tail_rom = float(np.max(tail) - np.min(tail))
            tail_vel = float(np.mean(np.abs(np.diff(tail)) * self.sample_rate_hz))
            if (
                tail_rom < 8.0
                and tail_vel < 6.0
                and float(np.max(tail)) <= (settings.knee_angle_max + settings.angle_tolerance_deg)
                and (float(np.max(tail)) <= 10.0 or float(np.min(tail)) >= 42.0)
                and varus_vel_dps < settings.varus_velocity_thresh
            ):
                score = round(min(52.0, 28.0 + mean_imb * 0.24), 1)
                tier = "Moderate" if mean_imb > 58.0 else "Low"
                if tier == "Low":
                    score = round(min(22.0, 6.0 + mean_imb * 0.25), 1)
                markers = [
                    f"Calm settled posture hold (last 1.5s ROM {tail_rom:.1f}°) — walk to score dynamic gait"
                ]
        is_static_calm = bool(markers and "Calm static posture" in markers[0])
        drivers = RiskDrivers(
            rom_deficit_pct=0 if is_static_calm else int(max(0, min(100, round(((58.0 - rom) / 42.0) * 100.0)))),
            stiffness_pct=0 if is_static_calm else int(max(0, min(100, round(flex_stiffness * 100.0)))),
            asymmetry_pct=int(max(0, min(100, round(mean_imb)))),
            jerk_wobble_pct=int(max(0, min(100, round((jerk / 22.0) * 100.0)))),
        )
        return AIRiskSnapshot(
            window_samples=len(angles),
            ready=True,
            oa_risk_score=score,
            oa_risk_tier=tier,
            confidence=confidence,
            markers=markers,
            features=features,
            risk_drivers=drivers,
        )

