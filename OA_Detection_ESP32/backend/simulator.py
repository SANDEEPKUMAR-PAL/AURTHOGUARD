"""Virtual ESP32 wearable simulator for testing and demos without physical hardware."""

from __future__ import annotations

import asyncio
import math
import random
from typing import Awaitable, Callable, Literal, Optional


SimProfile = Literal[
    "normal",
    "early_oa",
    "severe_oa",
    "hyperflexion",
    "ex_seated_flex",
    "ex_sit_to_stand",
    "ex_mini_squat",
    "ex_weight_shift",
]


class VirtualESP32Simulator:
    """Emulates the ESP32 WROOM-32 sensor node at configurable Hz and gait profiles."""

    def __init__(
        self,
        on_packet: Callable[[dict], Awaitable[None]],
        on_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._on_packet = on_packet
        self._on_event = on_event
        self._task: Optional[asyncio.Task] = None
        self.running: bool = False
        self.profile: SimProfile = "normal"
        self.rate_hz: float = 10.0

        # Simulated sensor calibration state (2-point)
        self.calibrated: bool = False
        self.cal_step: int = 0
        self.thigh_pitch_offset: float = 0.0
        self.thigh_roll_offset: float = 0.0
        self.shin_pitch_offset: float = 0.0
        self.shin_roll_offset: float = 0.0
        self.thigh_polarity: int = 1
        self.shin_polarity: int = 1
        self.flex_straight: int = 2100
        self.flex_bent: int = 1150

        # Thresholds
        self.knee_min: float = -10.0
        self.knee_max: float = 135.0
        self.fsr_imb_thresh: float = 60.0
        self.flex_stiff_thresh: float = 0.25

        # Temporary calibration posture override
        self._calibrating_step: int = 0
        self._t_ms: int = 0

    def start(self, profile: SimProfile = "normal", rate_hz: float = 10.0) -> None:
        self.profile = profile
        self.rate_hz = rate_hz
        if self.running and self._task and not self._task.done():
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def set_thresholds(
        self, knee_min: float, knee_max: float, fsr_imb: float, flex_stiff: float
    ) -> None:
        self.knee_min = knee_min
        self.knee_max = knee_max
        self.fsr_imb_thresh = fsr_imb
        self.flex_stiff_thresh = flex_stiff

    def apply_calibration(self, cal: dict) -> None:
        self.thigh_pitch_offset = float(cal.get("thigh_pitch_offset", cal.get("thighPitchOffset", 0.0)))
        self.thigh_roll_offset = float(cal.get("thigh_roll_offset", cal.get("thighRollOffset", 0.0)))
        self.shin_pitch_offset = float(cal.get("shin_pitch_offset", cal.get("shinPitchOffset", 0.0)))
        self.shin_roll_offset = float(cal.get("shin_roll_offset", cal.get("shinRollOffset", 0.0)))
        self.thigh_polarity = int(cal.get("thigh_polarity", cal.get("thighPolarity", 1))) or 1
        self.shin_polarity = int(cal.get("shin_polarity", cal.get("shinPolarity", 1))) or 1
        self.flex_straight = int(cal.get("flex_straight", cal.get("flexStraight", 2100)))
        self.flex_bent = int(cal.get("flex_bent", cal.get("flexBent", 1150)))
        self.calibrated = True
        two_pt = cal.get("is_two_point", cal.get("isTwoPoint", False))
        self.cal_step = 2 if two_pt else 1

    def reset_calibration(self) -> None:
        self.calibrated = False
        self.cal_step = 0
        self.thigh_pitch_offset = 0.0
        self.thigh_roll_offset = 0.0
        self.shin_pitch_offset = 0.0
        self.shin_roll_offset = 0.0
        self.thigh_polarity = 1
        self.shin_polarity = 1
        self.flex_straight = 2100
        self.flex_bent = 1150

    async def trigger_calibration(self, step: int) -> None:
        """Simulate 2-second hardware calibration capture."""
        self._calibrating_step = step
        await self._on_event({"type": "calStatus", "step": step, "status": "collecting", "progress": 0})
        for pct in (25, 50, 75, 100):
            await asyncio.sleep(0.35)
            await self._on_event({"type": "calStatus", "step": step, "status": "collecting", "progress": pct})

        if step == 1:
            self.thigh_pitch_offset = round(-7.4 + random.uniform(-1.5, 1.5), 2)
            self.thigh_roll_offset = round(1.9 + random.uniform(-0.8, 0.8), 2)
            self.shin_pitch_offset = round(-5.8 + random.uniform(-1.5, 1.5), 2)
            self.shin_roll_offset = round(1.5 + random.uniform(-0.8, 0.8), 2)
            self.flex_straight = 2120 + random.randint(-60, 60)
            self.calibrated = True
            self.cal_step = 1
        else:
            self.flex_bent = self.flex_straight - 920 + random.randint(-40, 40)
            self.thigh_polarity = 1
            self.shin_polarity = 1
            self.calibrated = True
            self.cal_step = 2

        self._calibrating_step = 0
        await self._on_event({
            "type": "calResult",
            "status": "complete",
            "step": self.cal_step,
            "thighPitchOffset": self.thigh_pitch_offset,
            "thighRollOffset": self.thigh_roll_offset,
            "shinPitchOffset": self.shin_pitch_offset,
            "shinRollOffset": self.shin_roll_offset,
            "thighPolarity": self.thigh_polarity,
            "shinPolarity": self.shin_polarity,
            "flexStraight": self.flex_straight,
            "flexBent": self.flex_bent,
            "isTwoPoint": self.cal_step == 2,
        })

    async def _loop(self) -> None:
        dt = 1.0 / self.rate_hz
        t = 0.0
        while self.running:
            packet = self._generate_packet(t)
            await self._on_packet(packet)
            await asyncio.sleep(dt)
            t += dt
            self._t_ms += int(dt * 1000)

    def _generate_packet(self, t: float) -> dict:
        noise = lambda s: random.uniform(-s, s)

        if self._calibrating_step == 1:
            knee_c = noise(0.8)
            flex_norm = 0.02 + abs(noise(0.02))
            heel, fore = 1650 + int(noise(60)), 1580 + int(noise(60))
        elif self._calibrating_step == 2:
            knee_c = 90.0 + noise(1.2)
            flex_norm = 1.0 + noise(0.02)
            heel, fore = 950 + int(noise(60)), 900 + int(noise(60))
        elif self.profile == "normal":
            # Healthy gait kinematics (Zeni & Higginson; AAOS): ~2° to ~62° (~60° ROM), balanced plantar transfer (<50%)
            phase = 2.6 * t
            knee_c = 32.0 + 30.0 * math.sin(phase) + noise(1.2)
            flex_norm = max(0.0, min(1.2, knee_c / 90.0 + noise(0.02)))
            heel = int(max(150, 1750 + 880 * math.sin(phase) + noise(60)))
            fore = int(max(150, 1750 - 880 * math.sin(phase) + noise(60)))
        elif self.profile == "early_oa":
            # Early OA (KL 1–2): reduced ROM (~30°), +14° AAOS flexion contracture (>5°), tibial jerk & mild stiffness
            phase = 2.0 * t
            jerk = 4.5 * math.sin(13.0 * t)
            knee_c = 31.0 + 15.0 * math.sin(phase) + jerk + noise(1.8)
            flex_norm = max(0.0, min(1.0, (knee_c * 0.45) / 90.0 + noise(0.02)))
            heel = int(max(150, 2150 + 620 * math.sin(phase) + noise(70)))
            fore = int(max(150, 880 - 380 * math.sin(phase) + noise(70)))
        elif self.profile == "severe_oa":
            # Severe OA (KL 3–4): severely restricted stiff ROM (~18°), +20° contracture, >70% FSR asymmetry
            phase = 1.6 * t
            jerk = 7.0 * math.sin(15.0 * t)
            knee_c = 32.0 + 9.0 * math.sin(phase) + jerk + noise(2.2)
            flex_norm = max(0.0, min(0.6, (knee_c * 0.28) / 90.0 + noise(0.02)))
            heel = int(max(200, 3100 + 320 * math.sin(phase) + noise(90)))
            fore = int(max(80, 420 + 140 * math.cos(phase) + noise(50)))
        elif self.profile == "ex_seated_flex":
            phase = 1.6 * t
            knee_c = 56.0 + 52.0 * math.sin(phase) + noise(1.2)
            flex_norm = max(0.0, min(1.3, knee_c / 90.0 + noise(0.02)))
            heel = 480 + int(noise(45))
            fore = 460 + int(noise(45))
        elif self.profile == "ex_sit_to_stand":
            phase = 1.5 * t
            knee_c = 49.0 + 45.0 * math.sin(phase) + noise(1.4)
            flex_norm = max(0.0, min(1.15, knee_c / 90.0 + noise(0.02)))
            stand_load = max(0.0, -math.sin(phase))
            heel = int(600 + 2100 * stand_load + noise(60))
            fore = int(500 + 1850 * stand_load + noise(60))
        elif self.profile == "ex_mini_squat":
            phase = 1.8 * t
            knee_c = 33.0 + 29.0 * math.sin(phase) + noise(1.3)
            flex_norm = max(0.0, min(0.9, knee_c / 90.0 + noise(0.02)))
            heel = int(1750 + 550 * math.sin(phase) + noise(60))
            fore = int(1680 + 520 * math.sin(phase) + noise(60))
        elif self.profile == "ex_weight_shift":
            phase = 2.0 * t
            knee_c = 5.0 + 3.0 * math.sin(phase) + noise(0.8)
            flex_norm = max(0.0, min(0.25, knee_c / 90.0 + noise(0.01)))
            heel = int(max(180, 1750 + 1380 * math.sin(phase) + noise(60)))
            fore = int(max(180, 1750 - 1380 * math.sin(phase) + noise(60)))
        else:  # hyperflexion
            phase = 2.2 * t
            knee_c = 78.0 + 68.0 * math.sin(phase) + noise(2.0)
            flex_norm = max(0.0, min(1.5, knee_c / 90.0 + noise(0.03)))
            heel = int(max(100, 2300 + 1200 * math.sin(phase) + noise(80)))
            fore = int(max(100, 700 + 300 * math.cos(phase) + noise(60)))

        # Compute raw Flex ADC from flex_norm
        flex_raw = int(self.flex_straight + flex_norm * (self.flex_bent - self.flex_straight))
        flex_raw = max(0, min(4095, flex_raw))

        # Segment pitch kinematics
        if self.profile == "ex_seated_flex":
            thigh_pitch_c = 72.0 + noise(1.0)
        elif self.profile == "ex_sit_to_stand":
            thigh_pitch_c = 38.0 + 36.0 * math.sin(1.5 * t)
        elif self.profile == "ex_mini_squat":
            thigh_pitch_c = 18.0 + 16.0 * math.sin(1.8 * t)
        elif self.profile == "ex_weight_shift":
            thigh_pitch_c = 3.0 * math.sin(2.0 * t)
        else:
            thigh_pitch_c = -8.0 + 18.0 * math.sin(2.0 * t)
        shin_pitch_c = thigh_pitch_c + knee_c
        wobble_amp = 7.2 if self.profile in ("early_oa", "severe_oa") else 2.6
        thigh_roll_c = wobble_amp * math.cos(2.0 * t)
        shin_roll_c = (wobble_amp + 1.0) * math.cos(2.0 * t + 0.5)

        thigh_pitch_raw = thigh_pitch_c + self.thigh_pitch_offset
        shin_pitch_raw = shin_pitch_c + self.shin_pitch_offset
        raw_knee = shin_pitch_raw - thigh_pitch_raw

        total_fsr = heel + fore
        fsr_imb = (abs(heel - fore) / total_fsr * 100.0) if total_fsr > 100 else 0.0

        reasons: list[str] = []
        if knee_c < self.knee_min:
            reasons.append(f"Knee hyper-extension (<{self.knee_min:.0f}°)")
        elif knee_c > self.knee_max:
            reasons.append(f"Knee hyper-flexion (>{self.knee_max:.0f}°)")
        if fsr_imb > self.fsr_imb_thresh:
            reasons.append(f"Gait imbalance (>{self.fsr_imb_thresh:.0f}%)")
        if self.profile == "severe_oa" and flex_norm < self.flex_stiff_thresh:
            reasons.append("Knee stiffness detected")

        return {
            "type": "data",
            "source": "simulator",
            "timestamp": self._t_ms,
            "calibrated": self.calibrated,
            "calibrationStep": self.cal_step,
            "thigh": {
                "online": True,
                "ax": round(0.08 * math.sin(t * 2), 3),
                "ay": round(0.97 + noise(0.02), 3),
                "az": round(0.05 * math.cos(t * 2), 3),
                "gx": round(18.0 * math.cos(t * 2), 2),
                "gy": round(6.0 * math.sin(t * 2), 2),
                "gz": round(3.0 * noise(1), 2),
                "pitch": round(thigh_pitch_raw, 1),
                "roll": round(thigh_roll_c + self.thigh_roll_offset, 1),
                "pitchC": round(thigh_pitch_c, 1),
                "rollC": round(thigh_roll_c, 1),
            },
            "shin": {
                "online": True,
                "ax": round(0.14 * math.sin(t * 2 + 0.4), 3),
                "ay": round(0.94 + noise(0.03), 3),
                "az": round(0.09 * math.cos(t * 2), 3),
                "gx": round(32.0 * math.cos(t * 2 + 0.3), 2),
                "gy": round(9.0 * math.sin(t * 2), 2),
                "gz": round(4.0 * noise(1), 2),
                "pitch": round(shin_pitch_raw, 1),
                "roll": round(shin_roll_c + self.shin_roll_offset, 1),
                "pitchC": round(shin_pitch_c, 1),
                "rollC": round(shin_roll_c, 1),
            },
            "flex": flex_raw,
            "flexNorm": round(flex_norm, 3),
            "flexBaseline": self.flex_straight,
            "fsrHeel": heel,
            "fsrFore": fore,
            "kneeAngle": round(raw_knee, 1),
            "kneeAngleC": round(knee_c, 1),
            "fsrImbalance": round(fsr_imb, 1),
            "abnormal": len(reasons) > 0,
            "reason": " | ".join(reasons),
        }


async def _run_cli(host: str, port: int, profile: SimProfile, rate_hz: float) -> None:
    """Run virtual ESP32 as an independent WebSocket client pushing to /ws/esp32."""
    import argparse  # noqa: F401
    import json
    import websockets

    uri = f"ws://{host}:{port}/ws/esp32"
    print(f"[SIM-CLI] Connecting to {uri} (profile={profile}, {rate_hz} Hz)...")
    async with websockets.connect(uri) as ws:
        sim = VirtualESP32Simulator(
            on_packet=lambda pkt: ws.send(json.dumps(pkt)),
            on_event=lambda evt: ws.send(json.dumps(evt)),
        )
        sim.start(profile=profile, rate_hz=rate_hz)
        try:
            async for raw in ws:
                cmd = json.loads(raw)
                name = cmd.get("cmd")
                if name in ("calibrate_step1", "calibrate"):
                    asyncio.create_task(sim.trigger_calibration(1))
                elif name == "calibrate_step2":
                    asyncio.create_task(sim.trigger_calibration(2))
                elif name == "resetCalibration":
                    sim.reset_calibration()
                elif name == "setThresholds":
                    sim.set_thresholds(
                        float(cmd.get("kneeMin", sim.knee_min)),
                        float(cmd.get("kneeMax", sim.knee_max)),
                        float(cmd.get("fsrImb", sim.fsr_imb_thresh)),
                        float(cmd.get("flexSt", sim.flex_stiff_thresh)),
                    )
        finally:
            await sim.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Virtual ESP32 WebSocket Client Simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--profile",
        default="normal",
        choices=[
            "normal",
            "early_oa",
            "severe_oa",
            "hyperflexion",
            "ex_seated_flex",
            "ex_sit_to_stand",
            "ex_mini_squat",
            "ex_weight_shift",
        ],
    )
    parser.add_argument("--rate", type=float, default=10.0)
    args = parser.parse_args()
    asyncio.run(_run_cli(args.host, args.port, args.profile, args.rate))

