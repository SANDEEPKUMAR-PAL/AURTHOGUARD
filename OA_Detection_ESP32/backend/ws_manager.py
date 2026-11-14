"""Dual-mode ESP32 WiFi bridge, WebSocket fan-out hub, session recorder, and calibration relay."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import socket
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

import websockets
from fastapi import WebSocket
from pydantic import ValidationError

from backend.ai_engine import GaitWindowAnalyzer, OARiskModel
from backend.biomechanics import compute_foot_pressure_metrics
from backend.calibration import CalibrationCollector, normalize_telemetry_with_calibration
from backend.config import settings
from backend.database import Database, utc_now_iso
from backend.exercises import EXERCISE_MAP, ExerciseTracker
from backend.i18n import normalize_lang
from backend.schemas import (
    DeviceStatusOut,
    TelemetryIn,
    ThresholdsConfig,
)
from backend.sensor_health import evaluate_sensor_health
from backend.simulator import SimProfile, VirtualESP32Simulator

logger = logging.getLogger("backend.ws_manager")


def safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def safe_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "t", "y")
    return bool(val)


def detect_lan_ips() -> list[str]:
    """Return local non-loopback IPv4 addresses for ESP32 configuration display."""
    ips: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            ips.append(ip)
    except OSError:
        pass
    try:
        hostname_ips = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in hostname_ips:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips or [settings.default_lan_ip]


class ConnectionManager:
    """Coordinates ESP32 WiFi streams, AI scoring, SQLite session recording, and browser dashboards."""

    def __init__(self, db: Database, ai_model: OARiskModel) -> None:
        self.db = db
        self.ai_model = ai_model
        self.analyzer = GaitWindowAnalyzer(
            model=ai_model,
            window_size=settings.ai_window_size,
            step_interval=settings.ai_step_interval,
        )
        self.cal_collector = CalibrationCollector()
        self.exercise_tracker = ExerciseTracker()
        self.thresholds = ThresholdsConfig(
            knee_angle_min=settings.knee_angle_min,
            knee_angle_max=settings.knee_angle_max,
            fsr_imbalance_thresh=settings.fsr_imbalance_thresh,
            flex_stiff_thresh=settings.flex_stiff_thresh,
        )

        # Connected clients
        self.esp32_ws: Optional[WebSocket] = None
        self.esp32_remote_ip: Optional[str] = None
        self.esp32_source: Optional[str] = None  # "esp32_push" | "esp32_pull" | "http" | "simulator"
        self.dashboard_clients: set[WebSocket] = set()

        # Outbound pull bridge (optional fallback to legacy ws://ESP_IP:81)
        self._pull_task: Optional[asyncio.Task] = None
        self._pull_ws: Optional[Any] = None
        self._pull_target: Optional[str] = None

        # Virtual simulator
        self.simulator = VirtualESP32Simulator(
            on_packet=self._on_sim_packet,
            on_event=self._on_device_event,
        )

        # Active patient / calibration / recording session
        self.active_patient_id: Optional[int] = None
        self.active_patient_name: str = "Walk-in / Unassigned"
        self.active_leg: str = "Left"
        self.language: str = "en"
        self.latest_sensor_health: Optional[dict[str, Any]] = None
        self.latest_foot_pressure: Optional[dict[str, Any]] = None
        self.active_calibration: Optional[dict[str, Any]] = None
        self.calibration_step: int = 0
        self.active_session_id: Optional[int] = None
        self._session_auto_started: bool = False
        self._telemetry_buffer: list[tuple[Any, ...]] = []
        self.recent_frames: deque[dict[str, Any]] = deque(maxlen=1000)
        self._was_abnormal: bool = False
        self._was_high_risk: bool = False
        self._was_hw_alert: bool = False
        self._was_hw_moderate: bool = False
        self._last_http_seen_at: float = 0.0

    async def startup(self) -> None:
        """Finalize any interrupted sessions, load thresholds, and ensure AI model is ready."""
        await self.db.reconcile_dangling_sessions()
        saved_thresh = await self.db.get_setting("thresholds")
        if isinstance(saved_thresh, dict):
            self.thresholds = ThresholdsConfig(**saved_thresh)
            self.simulator.set_thresholds(
                self.thresholds.knee_angle_min,
                self.thresholds.knee_angle_max,
                self.thresholds.fsr_imbalance_thresh,
                self.thresholds.flex_stiff_thresh,
            )
        self.ai_model.ensure_ready()

    async def shutdown(self) -> None:
        """Flush buffered telemetry, stop simulator, and close pull bridge."""
        await self.simulator.stop()
        await self.stop_pull_bridge()
        await self.flush_telemetry_buffer()
        if self.active_session_id is not None:
            await self.stop_session(notes="Auto-stopped on server shutdown")

    # -------------------- Dashboard Connections --------------------

    async def connect_dashboard(self, ws: WebSocket) -> None:
        await ws.accept()
        self.dashboard_clients.add(ws)
        await ws.send_text(json.dumps({
            "type": "status",
            "data": self.get_status().model_dump(),
        }))
        if self.recent_frames:
            await ws.send_text(json.dumps({
                "type": "history",
                "frames": list(self.recent_frames)[-150:],
            }))

    def clear_live_buffer(self) -> int:
        count = len(self.recent_frames)
        self.recent_frames.clear()
        self.analyzer.reset()
        return count

    def disconnect_dashboard(self, ws: WebSocket) -> None:
        self.dashboard_clients.discard(ws)

    async def broadcast_dashboard(self, payload: dict[str, Any]) -> None:
        if not self.dashboard_clients:
            return
        msg = json.dumps(payload)
        dead: list[WebSocket] = []
        for client in list(self.dashboard_clients):
            try:
                await client.send_text(msg)
            except Exception:
                dead.append(client)
        for d in dead:
            self.dashboard_clients.discard(d)

    # -------------------- ESP32 Push Connection --------------------

    async def connect_esp32(self, ws: WebSocket) -> None:
        await ws.accept()
        self.esp32_ws = ws
        self.esp32_remote_ip = ws.client.host if ws.client else "unknown"
        self.esp32_source = "esp32_push"
        # Push current thresholds & patient calibration immediately on connect
        await self.send_thresholds_to_device()
        if self.active_calibration:
            await self.send_calibration_to_device(self.active_calibration)
        await self.broadcast_status()

    async def disconnect_esp32(self, ws: WebSocket) -> None:
        if self.esp32_ws is ws:
            self.esp32_ws = None
            self.esp32_remote_ip = None
            if not self.simulator.running and not self._pull_ws:
                self.esp32_source = None
            if self.active_session_id is not None and self._session_auto_started:
                await self.stop_session(notes="Completed on ESP32 disconnect")
            else:
                await self.broadcast_status()

    # -------------------- ESP32 Pull Bridge (Fallback ws://IP:81) --------------------

    async def start_pull_bridge(self, ip: str, port: int = settings.esp32_pull_bridge_port) -> None:
        await self.stop_pull_bridge()
        url = f"ws://{ip}:{port}"
        self._pull_target = url
        self._pull_task = asyncio.create_task(self._pull_loop(url, ip))
        await self.broadcast_status()

    async def stop_pull_bridge(self) -> None:
        self._pull_target = None
        if self._pull_ws is not None:
            try:
                await self._pull_ws.close()
            except Exception:
                pass
            self._pull_ws = None
        if self._pull_task and not self._pull_task.done():
            self._pull_task.cancel()
            try:
                await self._pull_task
            except asyncio.CancelledError:
                pass
        self._pull_task = None
        if self.esp32_source == "esp32_pull":
            self.esp32_source = None
            self.esp32_remote_ip = None
        if self.active_session_id is not None and self._session_auto_started:
            await self.stop_session(notes="Completed on bridge disconnect")
        else:
            await self.broadcast_status()

    async def _pull_loop(self, url: str, ip: str) -> None:
        while self._pull_target == url:
            try:
                async with websockets.connect(url, open_timeout=5) as ws:
                    self._pull_ws = ws
                    self.esp32_source = "esp32_pull"
                    self.esp32_remote_ip = ip
                    await self.send_thresholds_to_device()
                    if self.active_calibration:
                        await self.send_calibration_to_device(self.active_calibration)
                    await self.broadcast_status()
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self.handle_device_message(data, source="esp32_pull", remote_ip=ip)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._pull_ws = None
                if self.esp32_source == "esp32_pull":
                    self.esp32_source = None
                    self.esp32_remote_ip = None
                    await self.broadcast_status()
                await self.broadcast_dashboard({
                    "type": "bridgeInfo",
                    "connected": False,
                    "target": url,
                    "message": f"Reconnecting to {url}: {exc}",
                })
                await asyncio.sleep(settings.bridge_reconnect_delay_sec)

    # -------------------- Simulator & HTTP Helpers --------------------

    async def start_simulator(self, profile: SimProfile = "normal", rate_hz: float = settings.simulator_default_hz) -> None:
        self.analyzer.reset()
        self.esp32_source = "simulator"
        self.esp32_remote_ip = "virtual"
        self.simulator.set_thresholds(
            self.thresholds.knee_angle_min,
            self.thresholds.knee_angle_max,
            self.thresholds.fsr_imbalance_thresh,
            self.thresholds.flex_stiff_thresh,
        )
        if self.active_calibration:
            self.simulator.apply_calibration(self.active_calibration)
        self.simulator.start(profile=profile, rate_hz=rate_hz)
        await self.broadcast_status()

    async def stop_simulator(self) -> None:
        await self.simulator.stop()
        if self.esp32_source == "simulator":
            self.esp32_source = "esp32_push" if self.esp32_ws else None
            self.esp32_remote_ip = self.esp32_ws.client.host if (self.esp32_ws and self.esp32_ws.client) else None
        if self.active_session_id is not None and self._session_auto_started:
            await self.stop_session(notes="Completed simulator session")
        else:
            await self.broadcast_status()

    async def _on_sim_packet(self, packet: dict[str, Any]) -> None:
        await self.handle_device_message(packet, source="simulator", remote_ip="virtual")

    async def _on_device_event(self, event: dict[str, Any]) -> None:
        await self.handle_device_message(event, source="simulator", remote_ip="virtual")

    # -------------------- Command Relay (Backend -> Device) --------------------

    async def send_command_to_device(self, cmd_payload: dict[str, Any]) -> bool:
        cmd = cmd_payload.get("cmd")
        if self.simulator.running:
            if cmd == "calibrate_step1":
                asyncio.create_task(self.simulator.trigger_calibration(1))
                return True
            if cmd == "calibrate_step2":
                asyncio.create_task(self.simulator.trigger_calibration(2))
                return True
            if cmd == "resetCalibration":
                self.simulator.reset_calibration()
                return True
            if cmd == "applyCalibration":
                self.simulator.apply_calibration(cmd_payload)
                return True
            if cmd == "setThresholds":
                self.simulator.set_thresholds(
                    float(cmd_payload.get("kneeMin", self.thresholds.knee_angle_min)),
                    float(cmd_payload.get("kneeMax", self.thresholds.knee_angle_max)),
                    float(cmd_payload.get("fsrImb", self.thresholds.fsr_imbalance_thresh)),
                    float(cmd_payload.get("flexSt", self.thresholds.flex_stiff_thresh)),
                )
                return True

        msg = json.dumps(cmd_payload)
        sent = False
        if self.esp32_ws is not None:
            try:
                await self.esp32_ws.send_text(msg)
                sent = True
            except Exception:
                pass
        if self._pull_ws is not None:
            try:
                await self._pull_ws.send(msg)
                sent = True
            except Exception:
                pass
        return sent

    async def send_thresholds_to_device(self) -> bool:
        return await self.send_command_to_device({
            "cmd": "setThresholds",
            "kneeMin": self.thresholds.knee_angle_min,
            "kneeMax": self.thresholds.knee_angle_max,
            "fsrImb": self.thresholds.fsr_imbalance_thresh,
            "flexSt": self.thresholds.flex_stiff_thresh,
        })

    async def send_calibration_to_device(self, cal: dict[str, Any]) -> bool:
        return await self.send_command_to_device({
            "cmd": "applyCalibration",
            "thighPitchOffset": cal.get("thigh_pitch_offset", 0.0),
            "thighRollOffset": cal.get("thigh_roll_offset", 0.0),
            "shinPitchOffset": cal.get("shin_pitch_offset", 0.0),
            "shinRollOffset": cal.get("shin_roll_offset", 0.0),
            "thighPolarity": cal.get("thigh_polarity", 1),
            "shinPolarity": cal.get("shin_polarity", 1),
            "flexStraight": cal.get("flex_straight", 2048),
            "flexBent": cal.get("flex_bent", 1200),
            "fsrHeelTare": cal.get("fsr_heel_tare", 0),
            "fsrForeTare": cal.get("fsr_fore_tare", 0),
            "isTwoPoint": cal.get("is_two_point", False),
        })

    async def trigger_calibration(self, step: str, patient_id: Optional[int] = None) -> dict[str, Any]:
        if patient_id is not None and patient_id != self.active_patient_id:
            await self.select_patient(patient_id)

        if step == "reset":
            self.active_calibration = None
            self.calibration_step = 0
            self.cal_collector.step = 0
            await self.send_command_to_device({"cmd": "resetCalibration"})
            await self.broadcast_dashboard({"type": "calReset"})
            await self.broadcast_status()
            return {"status": "reset"}

        cmd_name = "calibrate_step1" if step == "step1" else "calibrate_step2"
        sent = await self.send_command_to_device({"cmd": cmd_name})
        if not sent and self.esp32_source == "http":
            # Fallback server-side collection when ESP32 is HTTP-only
            self.cal_collector.start(1 if step == "step1" else 2)
            sent = True
        return {"status": "started" if sent else "no_device", "step": step}

    async def apply_custom_calibration(
        self, payload: dict[str, Any], patient_id: Optional[int] = None
    ) -> dict[str, Any]:
        """Apply manual or per-sensor calibration offsets immediately (saving to DB when a patient is active)."""
        target_pid = patient_id if patient_id is not None else self.active_patient_id
        cal_data = {
            "thigh_pitch_offset": float(payload.get("thigh_pitch_offset", 0.0)),
            "thigh_roll_offset": float(payload.get("thigh_roll_offset", 0.0)),
            "shin_pitch_offset": float(payload.get("shin_pitch_offset", 0.0)),
            "shin_roll_offset": float(payload.get("shin_roll_offset", 0.0)),
            "thigh_polarity": int(payload.get("thigh_polarity", 1)) or 1,
            "shin_polarity": int(payload.get("shin_polarity", 1)) or 1,
            "flex_straight": int(payload.get("flex_straight", 2048)),
            "flex_bent": int(payload.get("flex_bent", 1200)),
            "fsr_heel_tare": int(payload.get("fsr_heel_tare", 0)),
            "fsr_fore_tare": int(payload.get("fsr_fore_tare", 0)),
            "is_two_point": bool(payload.get("is_two_point", False)),
        }
        if target_pid is not None and await self.db.get_patient(target_pid):
            if target_pid != self.active_patient_id:
                await self.select_patient(target_pid)
            saved = await self.db.create_calibration(target_pid, cal_data, activate=True)
        else:
            saved = {**cal_data, "id": None, "patient_id": None, "is_active": True, "calibrated_at": utc_now_iso()}

        self.active_calibration = saved
        self.calibration_step = 2 if saved.get("is_two_point") else 1
        await self.send_calibration_to_device(saved)
        await self.broadcast_dashboard({
            "type": "calResult",
            "status": "complete",
            "step": self.calibration_step,
            "calibration": saved,
        })
        await self.broadcast_status()
        return saved

    async def auto_calibrate_standing(
        self, patient_id: Optional[int] = None, samples: int = settings.auto_calibration_samples
    ) -> dict[str, Any]:
        """Instant 1-click Auto Calibration from live standing-straight (0°) sensor readings."""
        valid = [f for f in list(self.recent_frames) if "thigh" in f and "shin" in f]
        if len(valid) < 2 and (self.esp32_connected or self.simulator.running):
            for _ in range(8):
                await asyncio.sleep(0.15)
                valid = [f for f in list(self.recent_frames) if "thigh" in f and "shin" in f]
                if len(valid) >= min(samples, 4):
                    break

        if not valid:
            return {
                "status": "no_device",
                "detail": "No live sensor data yet. Connect ESP32 or start Simulator while patient stands straight (0°).",
            }

        recent = valid[-max(1, min(samples, len(valid))):]
        n = len(recent)
        tp = round(sum(float(f["thigh"].get("pitch", 0.0)) for f in recent) / n, 3)
        tr = round(sum(float(f["thigh"].get("roll", 0.0)) for f in recent) / n, 3)
        sp = round(sum(float(f["shin"].get("pitch", 0.0)) for f in recent) / n, 3)
        sr = round(sum(float(f["shin"].get("roll", 0.0)) for f in recent) / n, 3)
        fs = int(round(sum(int(f.get("flex", 2048)) for f in recent) / n))

        angles = [float(f["shin"].get("pitch", 0.0)) - float(f["thigh"].get("pitch", 0.0)) for f in recent]
        spread = round(max(angles) - min(angles), 2) if angles else 0.0

        existing = self.active_calibration or {}
        existing_bent = existing.get("flex_bent")
        fb = int(existing_bent) if (existing_bent is not None and abs(int(existing_bent) - fs) >= 20) else max(200, fs - 850)

        saved = await self.apply_custom_calibration(
            {
                "thigh_pitch_offset": tp,
                "thigh_roll_offset": tr,
                "shin_pitch_offset": sp,
                "shin_roll_offset": sr,
                "thigh_polarity": int(existing.get("thigh_polarity", 1)) or 1,
                "shin_polarity": int(existing.get("shin_polarity", 1)) or 1,
                "flex_straight": fs,
                "flex_bent": fb,
                "fsr_heel_tare": int(existing.get("fsr_heel_tare", 0)),
                "fsr_fore_tare": int(existing.get("fsr_fore_tare", 0)),
                "is_two_point": bool(existing.get("is_two_point", False)),
            },
            patient_id=patient_id,
        )
        return {
            "status": "calibrated",
            "samples_used": n,
            "stability_spread_deg": spread,
            "calibration": saved,
        }

    async def test_hardware_buzzer(self, duration_sec: float = 1.0) -> dict[str, Any]:
        """Trigger a brief Red LED + Buzzer test pulse on the connected ESP32."""
        sent_test = await self.send_command_to_device({"cmd": "testBuzzer"})
        sent_alert = await self.send_command_to_device({
            "cmd": "setAlertState",
            "critical": True,
            "reason": "Hardware Buzzer Test",
        })
        if sent_alert:
            async def _clear_later() -> None:
                await asyncio.sleep(max(0.4, min(3.0, duration_sec)))
                await self.send_command_to_device({
                    "cmd": "setAlertState",
                    "critical": self._was_hw_alert,
                    "reason": "",
                })
            asyncio.create_task(_clear_later())
        sent = sent_test or sent_alert
        return {"status": "sent" if sent else "no_device", "sent": sent}

    async def set_hardware_buzzer_mode(self, passive: bool) -> dict[str, Any]:
        """Configure Active (DC) vs Passive (2 kHz tone) buzzer mode on the ESP32."""
        await self.db.set_setting("buzzer_passive", bool(passive))
        sent = await self.send_command_to_device({"cmd": "setBuzzerMode", "passive": bool(passive)})
        return {"status": "sent" if sent else "saved_offline", "passive": bool(passive), "sent": sent}

    # -------------------- Patient & Session Management --------------------

    async def select_patient(self, patient_id: Optional[int]) -> Optional[dict[str, Any]]:
        if self.active_session_id is not None and patient_id != self.active_patient_id:
            await self.stop_session(notes="Auto-saved on patient switch")

        if patient_id is None:
            self.active_patient_id = None
            self.active_patient_name = "Walk-in / Unassigned"
            self.active_calibration = None
            self.calibration_step = 0
            await self.broadcast_status()
            return None

        patient = await self.db.get_patient(patient_id)
        if not patient:
            return None
        self.active_patient_id = patient["id"]
        self.active_patient_name = patient["name"]
        leg = patient.get("monitored_leg")
        self.active_leg = leg if leg in ("Left", "Right") else "Left"
        cal = patient.get("active_calibration")
        self.active_calibration = cal
        self.calibration_step = (2 if cal["is_two_point"] else 1) if cal else 0
        if cal:
            await self.send_calibration_to_device(cal)
        else:
            await self.send_command_to_device({"cmd": "resetCalibration"})
        await self.broadcast_status()
        return patient

    async def set_active_leg(self, leg: str) -> str:
        self.active_leg = "Right" if leg == "Right" else "Left"
        if self.active_patient_id is not None:
            await self.db.update_patient(self.active_patient_id, {"monitored_leg": self.active_leg})
        await self.broadcast_status()
        return self.active_leg

    async def set_language(self, lang: str) -> str:
        self.language = normalize_lang(lang)
        self.exercise_tracker.set_language(self.language)
        await self.broadcast_status()
        return self.language

    async def start_session(
        self,
        patient_id: Optional[int] = None,
        patient_name: Optional[str] = None,
        clinician_name: str = "",
        source: Optional[str] = None,
        monitored_leg: Optional[str] = None,
        notes: str = "",
        auto_started: bool = False,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        if self.active_session_id is not None:
            await self.stop_session()

        if patient_id is not None and patient_id != self.active_patient_id:
            await self.select_patient(patient_id)

        resolved_pid = patient_id if patient_id is not None else self.active_patient_id
        resolved_name = patient_name or self.active_patient_name or "Walk-in / Unassigned"
        cal_id = self.active_calibration["id"] if self.active_calibration and "id" in self.active_calibration else None
        resolved_src = source or self.esp32_source or "esp32"

        self.analyzer.reset()
        self._telemetry_buffer.clear()
        self._was_abnormal = False
        self._was_high_risk = False
        self._session_auto_started = auto_started

        resolved_leg = monitored_leg if monitored_leg in ("Left", "Right") else self.active_leg
        self.active_leg = resolved_leg
        session = await self.db.create_session(
            patient_id=resolved_pid,
            patient_name=resolved_name,
            clinician_name=clinician_name,
            calibration_id=cal_id,
            source=resolved_src,
            monitored_leg=resolved_leg,
            notes=notes,
        )
        self.active_session_id = session["id"]
        if broadcast:
            await self.broadcast_dashboard({"type": "sessionUpdated", "action": "started", "session": session})
            await self.broadcast_status()
        return session

    async def stop_session(self, notes: Optional[str] = None) -> Optional[dict[str, Any]]:
        if self.active_session_id is None:
            return None
        sid = self.active_session_id
        self._session_auto_started = False
        await self.flush_telemetry_buffer()
        stats = await self.db.compute_session_stats_from_db(sid)
        ai_snap = self.analyzer.latest_snapshot
        summary = {
            **stats,
            "oa_risk_score": ai_snap.oa_risk_score if ai_snap.ready else stats.get("avg_risk_score"),
            "oa_risk_tier": ai_snap.oa_risk_tier if ai_snap.ready else "Pending",
            "risk_markers": ai_snap.markers if ai_snap.ready else [],
        }
        completed = await self.db.complete_session(sid, summary, notes=notes)
        self.active_session_id = None
        await self.broadcast_dashboard({"type": "sessionUpdated", "action": "completed", "session": completed})
        await self.broadcast_status()
        return completed

    async def flush_telemetry_buffer(self) -> None:
        if self._telemetry_buffer:
            batch = list(self._telemetry_buffer)
            self._telemetry_buffer.clear()
            await self.db.insert_telemetry_batch(batch)

    # -------------------- Incoming Message Pipeline --------------------

    async def handle_device_message(
        self,
        payload: dict[str, Any],
        source: str = "esp32",
        remote_ip: Optional[str] = None,
        allow_auto_record: bool = True,
    ) -> dict[str, Any]:
        msg_type = payload.get("type", "data")

        if msg_type == "calStatus":
            await self.broadcast_dashboard(payload)
            return payload

        if msg_type == "calResult":
            cal_dict = {
                "thigh_pitch_offset": safe_float(payload.get("thighPitchOffset"), 0.0),
                "thigh_roll_offset": safe_float(payload.get("thighRollOffset"), 0.0),
                "shin_pitch_offset": safe_float(payload.get("shinPitchOffset"), 0.0),
                "shin_roll_offset": safe_float(payload.get("shinRollOffset"), 0.0),
                "thigh_polarity": safe_int(payload.get("thighPolarity"), 1) or 1,
                "shin_polarity": safe_int(payload.get("shinPolarity"), 1) or 1,
                "flex_straight": safe_int(payload.get("flexStraight", payload.get("flexBaseline")), settings.cal_flex_straight_default),
                "flex_bent": safe_int(payload.get("flexBent"), settings.cal_flex_bent_default),
                "fsr_heel_tare": safe_int(payload.get("fsrHeelTare"), 0),
                "fsr_fore_tare": safe_int(payload.get("fsrForeTare"), 0),
                "is_two_point": safe_bool(payload.get("isTwoPoint", safe_int(payload.get("step"), 1) == 2)),
            }
            if self.active_patient_id is not None:
                saved = await self.db.create_calibration(self.active_patient_id, cal_dict, activate=True)
                self.active_calibration = saved
            else:
                self.active_calibration = cal_dict
            self.calibration_step = 2 if cal_dict["is_two_point"] else 1
            await self.broadcast_dashboard({
                "type": "calResult",
                "calibration": self.active_calibration,
                "step": self.calibration_step,
            })
            await self.broadcast_status()
            return payload

        if msg_type == "config":
            if payload.get("calibrated") and payload.get("calibration") and not self.active_calibration:
                c = payload["calibration"]
                self.active_calibration = {
                    "thigh_pitch_offset": safe_float(c.get("thighPitchOffset"), 0.0),
                    "thigh_roll_offset": safe_float(c.get("thighRollOffset"), 0.0),
                    "shin_pitch_offset": safe_float(c.get("shinPitchOffset"), 0.0),
                    "shin_roll_offset": safe_float(c.get("shinRollOffset"), 0.0),
                    "thigh_polarity": safe_int(c.get("thighPolarity"), 1) or 1,
                    "shin_polarity": safe_int(c.get("shinPolarity"), 1) or 1,
                    "flex_straight": safe_int(c.get("flexStraight", c.get("flexBaseline")), settings.cal_flex_straight_default),
                    "flex_bent": safe_int(c.get("flexBent"), settings.cal_flex_bent_default),
                    "is_two_point": safe_bool(c.get("isTwoPoint"), False),
                }
                self.calibration_step = 2 if self.active_calibration["is_two_point"] else 1
            await self.broadcast_dashboard(payload)
            return payload

        if msg_type != "data":
            await self.broadcast_dashboard(payload)
            return payload

        # Keep virtual simulator streams isolated so stationary ESP32 hardware does not overwrite simulated gait
        if self.simulator.running and source != "simulator":
            return {"status": "ignored_simulator_active"}

        # Telemetry Packet Processing
        if source == "http":
            self._last_http_seen_at = asyncio.get_running_loop().time()
            self.esp32_source = "http"
            self.esp32_remote_ip = remote_ip

        try:
            telemetry = TelemetryIn(**{**payload, "source": source})
        except (ValidationError, TypeError, ValueError, KeyError) as exc:
            logger.warning("Dropped malformed telemetry packet from %s: %s", source, exc)
            return {"status": "error", "message": f"Malformed packet ignored: {exc}"}

        # Optional auto-record session when continuous hardware/simulator streaming starts
        auto_started_session: Optional[dict[str, Any]] = None
        if allow_auto_record and self.thresholds.auto_record_on_stream and self.active_session_id is None:
            auto_started_session = await self.start_session(source=source, auto_started=True, broadcast=False)

        # Fallback server-side calibration collector if active
        if self.cal_collector.step in (1, 2):
            done, pct, res = self.cal_collector.add_sample(telemetry)
            await self.broadcast_dashboard({
                "type": "calStatus",
                "step": self.cal_collector.step or (2 if res and res.get("is_two_point") else 1),
                "status": "collecting",
                "progress": pct,
            })
            if done and res:
                if self.active_patient_id is not None:
                    saved = await self.db.create_calibration(self.active_patient_id, res, activate=True)
                    self.active_calibration = saved
                else:
                    self.active_calibration = res
                self.calibration_step = 2 if res.get("is_two_point") else 1
                await self.broadcast_dashboard({
                    "type": "calResult",
                    "calibration": self.active_calibration,
                    "step": self.calibration_step,
                })
                await self.broadcast_status()

        # Normalize using active calibration
        norm = normalize_telemetry_with_calibration(telemetry, self.active_calibration)
        knee_c = norm["knee_angle_c"]
        flex_norm = norm["flex_norm"]

        # Per-sensor health diagnostics (loose wire / I2C / ADC rail guard)
        sensor_health = evaluate_sensor_health(telemetry, source=source)
        self.latest_sensor_health = sensor_health
        adj_heel = norm.get("fsr_heel_adj", telemetry.fsrHeel)
        adj_fore = norm.get("fsr_fore_adj", telemetry.fsrFore)
        fsr_imb = norm.get("fsr_imbalance_c", telemetry.fsrImbalance)

        # AI Gait Analysis (paused if either IMU 0x68/0x69 is offline so loose wires never cause false risk alerts)
        if sensor_health["imu_ready"]:
            shin_g = math.sqrt(telemetry.shin.ax ** 2 + telemetry.shin.ay ** 2 + telemetry.shin.az ** 2)
            ai_snap = self.analyzer.add_sample(
                knee_angle_c=knee_c,
                flex_norm=flex_norm,
                fsr_heel=adj_heel,
                fsr_fore=adj_fore,
                fsr_imbalance=fsr_imb,
                net_roll_deg=norm["net_roll_deg"],
                shin_accel_g=shin_g if shin_g > 0 else 1.0,
            )
        else:
            prev = self.analyzer.latest_snapshot
            pause_msg = "Paused: IMU sensor offline — check Thigh (0x68) / Shin (0x69) wiring"
            markers = [pause_msg] + [m for m in prev.markers if "Paused:" not in m]
            ai_snap = prev.model_copy(
                update={
                    "ready": False,
                    "oa_risk_tier": "Pending" if prev.oa_risk_score is None else prev.oa_risk_tier,
                    "markers": markers[:5],
                }
            )

        # Active Exercise Evaluation (all 5 sensors; guard against disconnected IMUs)
        exercise_snap = None
        if self.exercise_tracker.active:
            if sensor_health["imu_ready"] or self.exercise_tracker.exercise_key == "weight_shift":
                exercise_snap = self.exercise_tracker.update(
                    knee_angle_c=knee_c,
                    flex_norm=flex_norm,
                    thigh_roll_c=norm["thigh_roll_c"],
                    shin_roll_c=norm["shin_roll_c"],
                    fsr_heel=adj_heel,
                    fsr_fore=adj_fore,
                    fsr_imbalance=fsr_imb,
                )
            else:
                exercise_snap = self.exercise_tracker.snapshot()

        # Evaluate backend threshold alerts with calibrated +/- tolerance deadband
        tol = settings.angle_tolerance_deg
        eff_min = self.thresholds.knee_angle_min - tol
        eff_max = self.thresholds.knee_angle_max + tol
        vel_now = ai_snap.features.mean_angular_vel_dps if ai_snap.ready else 0.0
        rom_now = ai_snap.features.rom_deg if ai_snap.ready else 0.0
        cal_flex_span = (
            abs(
                int(self.active_calibration.get("flex_bent", settings.cal_flex_bent_default))
                - int(self.active_calibration.get("flex_straight", settings.cal_flex_straight_default))
            )
            if self.active_calibration
            else 0
        )
        has_valid_2pt_flex = bool(
            self.active_calibration and self.active_calibration.get("is_two_point") and cal_flex_span >= 30
        )
        # During calm static hardware posture, Heel FSR pinned near 3.3V rail (>=3850) while Forefoot is unloaded
        # reflects resting hardware offset/pull-up bias rather than dynamic gait overload -> watch as Moderate, not CRITICAL.
        is_static_heel_rail = bool(
            source != "simulator"
            and ai_snap.ready
            and rom_now < 12.0
            and vel_now < 8.0
            and eff_min <= knee_c <= eff_max
            and telemetry.fsrHeel >= 3850
            and telemetry.fsrFore <= 350
        )

        reasons: list[str] = []
        if telemetry.reason and not self.active_calibration:
            for r in telemetry.reason.split(" | "):
                r = r.strip()
                if not r:
                    continue
                if "hyper-" in r.lower() and eff_min <= knee_c <= eff_max:
                    continue
                if "imbalance" in r.lower() and ((adj_heel + adj_fore) < settings.fsr_active_min_total or is_static_heel_rail):
                    continue
                if "stiffness" in r.lower() and not has_valid_2pt_flex:
                    continue
                reasons.append(r)

        if sensor_health["imu_ready"]:
            if knee_c < eff_min and not any("hyper-extension" in r.lower() for r in reasons):
                reasons.append(f"Knee hyper-extension (<{self.thresholds.knee_angle_min:.0f}°)")
            elif knee_c > eff_max and not any("hyper-flexion" in r.lower() for r in reasons):
                reasons.append(f"Knee hyper-flexion (>{self.thresholds.knee_angle_max:.0f}°)")
            if (
                telemetry.calibrated
                and has_valid_2pt_flex
                and knee_c > 45.0
                and flex_norm < self.thresholds.flex_stiff_thresh
                and not any("stiffness" in r.lower() for r in reasons)
            ):
                reasons.append("Knee stiffness detected")
        if (
            not is_static_heel_rail
            and (adj_heel + adj_fore) >= settings.fsr_active_min_total
            and fsr_imb > self.thresholds.fsr_imbalance_thresh
            and not any("imbalance" in r.lower() for r in reasons)
        ):
            reasons.append(f"Gait imbalance (>{self.thresholds.fsr_imbalance_thresh:.0f}%)")

        raw_reason_lower = (telemetry.reason or "").lower()
        suppressed_raw_only = bool(
            ("hyper-" in raw_reason_lower and eff_min <= knee_c <= eff_max)
            or ("imbalance" in raw_reason_lower and ((adj_heel + adj_fore) < settings.fsr_active_min_total or is_static_heel_rail))
            or ("stiffness" in raw_reason_lower and not has_valid_2pt_flex)
        )
        threshold_abnormal = len(reasons) > 0 or (
            telemetry.abnormal
            and not self.active_calibration
            and not suppressed_raw_only
        )

        is_high_risk = bool(sensor_health["imu_ready"] and ai_snap.ready and ai_snap.oa_risk_tier == "High")
        extreme_angle = bool(
            sensor_health["imu_ready"]
            and (
                knee_c < self.thresholds.knee_angle_min - settings.extreme_angle_margin
                or knee_c > self.thresholds.knee_angle_max + settings.extreme_angle_margin
            )
        )
        is_critical = is_high_risk or len(reasons) >= 2 or extreme_angle
        is_abnormal = threshold_abnormal or is_high_risk
        is_moderate = bool(
            sensor_health["imu_ready"]
            and not is_abnormal
            and (
                is_static_heel_rail
                or (ai_snap.ready and ai_snap.oa_risk_tier == "Moderate")
                or (
                    (adj_heel + adj_fore) >= settings.fsr_active_min_total
                    and fsr_imb > settings.fsr_imbalance_warning_thresh
                )
            )
        )
        if is_high_risk and not reasons:
            reasons.append(f"Critical High OA Risk ({ai_snap.oa_risk_score:.1f}/100)")
        reason_str = " | ".join(dict.fromkeys(reasons))
        condition_state = (
            "critical"
            if is_critical
            else ("abnormal" if is_abnormal else ("moderate" if is_moderate else "normal"))
        )

        # Classify real-time movement / posture category
        if not sensor_health["imu_ready"]:
            movement_category = "Sensor Fault / Check Wiring"
        elif condition_state == "critical":
            movement_category = "Critical Joint Risk"
        elif condition_state == "abnormal":
            movement_category = "Abnormal Gait / Strain"
        elif knee_c >= 90.0:
            movement_category = "Deep Flexion / Exercise"
        elif knee_c >= 42.0 and (vel_now < 12.0 or abs(norm["thigh_pitch_c"]) >= 28.0):
            movement_category = "Seated / Bent Rest"
        elif rom_now >= 14.0 or vel_now >= 12.0:
            movement_category = "Active Walking Gait"
        elif knee_c < 22.0:
            movement_category = "Standing Neutral"
        else:
            movement_category = "Light Flexion / Transition"

        # Sync backend abnormal/moderate/normal state to ESP32 hardware Red/Green LED & Buzzer
        hw_alert = bool(sensor_health["imu_ready"] and (is_abnormal or is_high_risk))
        hw_moderate = bool(sensor_health["imu_ready"] and not hw_alert and is_moderate)
        if hw_alert != self._was_hw_alert or hw_moderate != self._was_hw_moderate:
            await self.send_command_to_device({
                "cmd": "setAlertState",
                "critical": hw_alert,
                "moderate": hw_moderate,
                "reason": reason_str[:80] if hw_alert else "",
            })
            self._was_hw_alert = hw_alert
            self._was_hw_moderate = hw_moderate

        # Alert transitions (Critical / Abnormal / Recovery)
        if is_abnormal and not self._was_abnormal:
            alert = await self.db.insert_alert(
                session_id=self.active_session_id,
                patient_id=self.active_patient_id,
                severity="critical" if is_critical else "warning",
                marker_type="critical_threshold" if is_critical else "threshold",
                message=reason_str or "Abnormal movement pattern detected",
                knee_angle=knee_c,
                oa_risk_score=ai_snap.oa_risk_score if ai_snap.ready else None,
            )
            await self.broadcast_dashboard({"type": "alert", "alert": alert})
        elif not is_abnormal and self._was_abnormal:
            alert = await self.db.insert_alert(
                session_id=self.active_session_id,
                patient_id=self.active_patient_id,
                severity="recovery",
                marker_type="threshold",
                message="Returned to normal movement",
                knee_angle=knee_c,
                oa_risk_score=ai_snap.oa_risk_score if ai_snap.ready else None,
            )
            await self.broadcast_dashboard({"type": "alert", "alert": alert})
        self._was_abnormal = is_abnormal

        if is_high_risk and not self._was_high_risk:
            alert = await self.db.insert_alert(
                session_id=self.active_session_id,
                patient_id=self.active_patient_id,
                severity="critical",
                marker_type="ai_risk",
                message=f"CRITICAL High OA Risk ({ai_snap.oa_risk_score:.1f}/100): " + "; ".join(ai_snap.markers[:2]),
                knee_angle=knee_c,
                oa_risk_score=ai_snap.oa_risk_score,
            )
            await self.broadcast_dashboard({"type": "alert", "alert": alert})
        self._was_high_risk = is_high_risk

        # Record telemetry batch if session is active
        if self.active_session_id is not None:
            self._telemetry_buffer.append((
                self.active_session_id,
                telemetry.timestamp,
                utc_now_iso(),
                telemetry.thigh.ax, telemetry.thigh.ay, telemetry.thigh.az,
                telemetry.thigh.gx, telemetry.thigh.gy, telemetry.thigh.gz,
                telemetry.thigh.pitch, telemetry.thigh.roll,
                norm["thigh_pitch_c"], norm["thigh_roll_c"],
                telemetry.shin.ax, telemetry.shin.ay, telemetry.shin.az,
                telemetry.shin.gx, telemetry.shin.gy, telemetry.shin.gz,
                telemetry.shin.pitch, telemetry.shin.roll,
                norm["shin_pitch_c"], norm["shin_roll_c"],
                telemetry.flex, flex_norm,
                telemetry.fsrHeel, telemetry.fsrFore,
                telemetry.kneeAngle, knee_c,
                telemetry.fsrImbalance,
                1 if is_abnormal else 0,
                reason_str,
                ai_snap.oa_risk_score if ai_snap.ready else None,
            ))
            if len(self._telemetry_buffer) >= settings.db_flush_batch_size:
                await self.flush_telemetry_buffer()

        cal_tare_h = int(self.active_calibration.get("fsr_heel_tare", 0)) if self.active_calibration else 0
        cal_tare_f = int(self.active_calibration.get("fsr_fore_tare", 0)) if self.active_calibration else 0
        foot_pressure = compute_foot_pressure_metrics(
            telemetry.fsrHeel,
            telemetry.fsrFore,
            heel_tare=cal_tare_h,
            fore_tare=cal_tare_f,
            imbalance_thresh=self.thresholds.fsr_imbalance_thresh,
        )
        self.latest_foot_pressure = foot_pressure

        enriched = {
            **payload,
            "type": "data",
            "source": source,
            "calibrated": telemetry.calibrated or (self.active_calibration is not None),
            "calibrationStep": telemetry.calibrationStep or self.calibration_step,
            "kneeAngleC": knee_c,
            "flexNorm": flex_norm,
            "fsrImbalance": fsr_imb,
            "thigh": {
                **telemetry.thigh.model_dump(),
                "pitchC": norm["thigh_pitch_c"],
                "rollC": norm["thigh_roll_c"],
            },
            "shin": {
                **telemetry.shin.model_dump(),
                "pitchC": norm["shin_pitch_c"],
                "rollC": norm["shin_roll_c"],
            },
            "abnormal": is_abnormal,
            "critical": is_critical,
            "condition": condition_state,
            "movementCategory": movement_category,
            "hardwareState": {
                "condition": condition_state,
                "greenLed": "OFF" if (is_abnormal or not sensor_health["imu_ready"]) else ("DIM" if is_moderate else "ON"),
                "redLed": "ON" if (is_abnormal and sensor_health["imu_ready"]) else "OFF",
                "buzzer": "ON" if (is_abnormal and sensor_health["imu_ready"]) else "OFF",
            },
            "reason": reason_str,
            "sessionId": self.active_session_id,
            "biomechanics": {
                "flexPct": norm["flex_pct"],
                "heelPct": norm["heel_pct"],
                "forePct": norm["fore_pct"],
                "netRollDeg": norm["net_roll_deg"],
                "footPressure": foot_pressure,
            },
            "aiRisk": ai_snap.model_dump(),
            "exercise": exercise_snap,
            "sensorHealth": sensor_health,
        }
        self.recent_frames.append(enriched)
        await self.broadcast_dashboard(enriched)
        if auto_started_session is not None:
            await self.broadcast_dashboard({"type": "sessionUpdated", "action": "started", "session": auto_started_session})
            await self.broadcast_status()
        return enriched

    # -------------------- Guided Exercise Management --------------------

    async def start_exercise(
        self,
        exercise_key: str,
        patient_id: Optional[int] = None,
        monitored_leg: Optional[str] = None,
        lang: Optional[str] = None,
        auto_simulate: bool = False,
    ) -> dict[str, Any]:
        if self.exercise_tracker.active:
            await self.stop_exercise()
        if patient_id is not None and patient_id != self.active_patient_id:
            await self.select_patient(patient_id)

        spec = EXERCISE_MAP[exercise_key]
        resolved_leg = monitored_leg if monitored_leg in ("Left", "Right") else self.active_leg
        resolved_lang = normalize_lang(lang) if lang is not None else self.language
        self.active_leg = resolved_leg
        self.language = resolved_lang
        trial = await self.db.create_exercise_trial(
            patient_id=self.active_patient_id,
            session_id=self.active_session_id,
            exercise_key=exercise_key,
            exercise_name=spec["name"],
            target_reps=spec["target_reps"],
            monitored_leg=resolved_leg,
        )
        snap = self.exercise_tracker.start(
            trial["id"],
            exercise_key,
            lang=resolved_lang,
            monitored_leg=resolved_leg,
        )
        if auto_simulate and self.esp32_ws is None and self._pull_ws is None:
            await self.start_simulator(profile=spec["sim_profile"], rate_hz=10.0)
        await self.broadcast_status()
        return {"trial": trial, "snapshot": snap}

    async def stop_exercise(self) -> Optional[dict[str, Any]]:
        if not self.exercise_tracker.active or self.exercise_tracker.trial_id is None:
            return None
        tid = self.exercise_tracker.trial_id
        snap = self.exercise_tracker.stop()
        completed = await self.db.complete_exercise_trial(tid, snap)
        await self.broadcast_status()
        return completed

    # -------------------- Status Helper --------------------

    def get_status(self) -> DeviceStatusOut:
        http_alive = False
        if self.esp32_source == "http":
            try:
                http_alive = (asyncio.get_running_loop().time() - self._last_http_seen_at) < settings.http_heartbeat_timeout_sec
            except RuntimeError:
                http_alive = False

        esp_connected = bool(
            self.esp32_ws is not None
            or self._pull_ws is not None
            or self.simulator.running
            or http_alive
        )
        return DeviceStatusOut(
            esp32_connected=esp_connected,
            esp32_source=self.esp32_source,
            esp32_remote_ip=self.esp32_remote_ip,
            pull_bridge_target=self._pull_target,
            simulator_running=self.simulator.running,
            simulator_profile=self.simulator.profile if self.simulator.running else None,
            dashboard_clients=len(self.dashboard_clients),
            active_session_id=self.active_session_id,
            active_patient_id=self.active_patient_id,
            calibrated=self.active_calibration is not None,
            calibration_step=self.calibration_step,
            active_calibration=self.active_calibration,
            thresholds=self.thresholds,
            lan_ips=detect_lan_ips(),
            ai_latest=self.analyzer.latest_snapshot,
            active_exercise=self.exercise_tracker.snapshot() if self.exercise_tracker.active else None,
            active_leg=self.active_leg,
            language=self.language,
            sensor_health=self.latest_sensor_health,
            display_timezone=settings.display_timezone,
            display_timezone_label=settings.display_timezone_label,
        )

    async def broadcast_status(self) -> None:
        await self.broadcast_dashboard({
            "type": "status",
            "data": self.get_status().model_dump(),
        })

