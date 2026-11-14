"""Unit tests for the biomechanical gait feature extractor and ML OA risk model."""

import math
from pathlib import Path

from backend.ai_engine import GaitWindowAnalyzer, OARiskModel


def test_model_trains_saves_and_reloads(tmp_path: Path):
    model_file = tmp_path / "oa_model.joblib"
    m1 = OARiskModel(model_file)
    m1.ensure_ready()
    assert model_file.exists()
    assert m1.pipeline is not None

    m2 = OARiskModel(model_file)
    m2.ensure_ready()
    assert m2.pipeline is not None


def test_gait_analyzer_distinguishes_normal_vs_oa_profiles(tmp_path: Path):
    model = OARiskModel(tmp_path / "oa_model.joblib")
    model.ensure_ready()

    # 1) Healthy gait waveform (~60 deg ROM, balanced heel/forefoot)
    normal = GaitWindowAnalyzer(model=model, window_size=40, step_interval=5)
    for i in range(40):
        t = i * 0.1
        angle = 32.0 + 30.0 * math.sin(2.6 * t)
        heel = int(1750 + 1350 * math.sin(2.6 * t))
        fore = int(1750 - 1350 * math.sin(2.6 * t))
        imb = abs(heel - fore) / (heel + fore) * 100.0
        snap = normal.add_sample(angle, max(0.0, angle / 90.0), heel, fore, imb)

    assert snap.ready is True
    assert snap.oa_risk_tier == "Low"
    assert snap.oa_risk_score < 35.0
    assert snap.features.rom_deg > 50.0

    # 2) Severe OA stiff + asymmetric waveform (~18 deg ROM, +22 ext deficit, >70% imbalance)
    severe = GaitWindowAnalyzer(model=model, window_size=40, step_interval=5)
    for i in range(40):
        t = i * 0.1
        angle = 32.0 + 9.0 * math.sin(1.6 * t) + 6.5 * math.sin(15.0 * t)
        heel = 3200
        fore = 400
        imb = abs(heel - fore) / (heel + fore) * 100.0
        snap_sev = severe.add_sample(angle, 0.22, heel, fore, imb)

    assert snap_sev.ready is True
    assert snap_sev.oa_risk_tier == "High"
    assert snap_sev.oa_risk_score > 65.0
    assert any("Asymmetric" in m or "Restricted" in m for m in snap_sev.markers)


def test_simulator_profiles_map_to_expected_ai_risk_tiers(tmp_path: Path):
    from backend.simulator import VirtualESP32Simulator

    async def _noop(_):
        return None

    model = OARiskModel(tmp_path / "sim_profiles_model.joblib")
    model.ensure_ready()

    expected = {
        "normal": ("Low",),
        "early_oa": ("Moderate", "High"),
        "severe_oa": ("High",),
        "hyperflexion": ("Moderate", "High"),
    }

    for profile, allowed_tiers in expected.items():
        sim = VirtualESP32Simulator(on_packet=_noop, on_event=_noop)
        sim.profile = profile
        analyzer = GaitWindowAnalyzer(model=model, window_size=40, step_interval=5)
        snap = None
        for i in range(40):
            pkt = sim._generate_packet(i * 0.1)
            snap = analyzer.add_sample(
                pkt["kneeAngleC"],
                pkt["flexNorm"],
                pkt["fsrHeel"],
                pkt["fsrFore"],
                pkt["fsrImbalance"],
            )
        assert snap is not None and snap.ready is True
        assert snap.oa_risk_tier in allowed_tiers, (
            f"Profile {profile} produced {snap.oa_risk_tier} (score={snap.oa_risk_score}, features={snap.features})"
        )


