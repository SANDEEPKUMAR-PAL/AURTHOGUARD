"""FastAPI application entrypoint, WebSocket endpoints, and dashboard serving."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.ai_engine import OARiskModel
from backend.config import settings
from backend.database import Database
from backend.routers import analytics, biomechanics, device, exercises, patients, sessions, telemetry
from backend.schemas import ThresholdsConfig
from backend.ws_manager import ConnectionManager, detect_lan_ips

logger = logging.getLogger("backend.main")


def create_app(
    db_path: Optional[Path | str] = None,
    model_path: Optional[Path | str] = None,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    resolved_db = Path(db_path) if db_path else settings.resolved_db_path
    resolved_model = Path(model_path) if model_path else settings.resolved_model_path

    db = Database(resolved_db)
    ai_model = OARiskModel(resolved_model)
    manager = ConnectionManager(db=db, ai_model=ai_model)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.init()
        await manager.startup()
        lan_ips = detect_lan_ips()
        print("==========================================================")
        print(f" SIH26004 OA Risk Detection Backend v{__version__}")
        print(f" Dashboard     : http://localhost:{settings.port}")
        print(f" LAN ESP32 WS  : ws://{lan_ips[0]}:{settings.port}/ws/esp32")
        print(f" SQLite DB     : {resolved_db}")
        print("==========================================================")
        try:
            yield
        finally:
            await manager.shutdown()
            await db.close()

    app = FastAPI(
        title="SIH26004 - OA Risk Detection Backend",
        version=__version__,
        description="Backend REST API, Dual-Mode ESP32 WiFi Bridge, SQLite DB & AI OA Risk Engine",
        lifespan=lifespan,
    )
    app.state.manager = manager

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(telemetry.router)
    app.include_router(patients.router)
    app.include_router(sessions.router)
    app.include_router(device.router)
    app.include_router(exercises.router)
    app.include_router(analytics.router)
    app.include_router(biomechanics.router)

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_dashboard():
        dash = settings.dashboard_html_path
        if not dash.exists():
            return JSONResponse(status_code=404, content={"detail": "dashboard.html not found"})
        return FileResponse(dash, headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/esp32")
    async def ws_esp32_endpoint(ws: WebSocket):
        """Inbound WebSocket endpoint where the ESP32 pushes sensor data over WiFi."""
        await manager.connect_esp32(ws)
        remote_ip = ws.client.host if ws.client else None
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    try:
                        await manager.handle_device_message(
                            data, source="esp32_push", remote_ip=remote_ip
                        )
                    except Exception as exc:
                        logger.warning("Error processing ESP32 packet (retaining connection): %s", exc)
        except WebSocketDisconnect:
            await manager.disconnect_esp32(ws)
        except Exception:
            await manager.disconnect_esp32(ws)

    @app.websocket("/ws/dashboard")
    async def ws_dashboard_endpoint(ws: WebSocket):
        """Outbound real-time stream + command relay for browser dashboards."""
        await manager.connect_dashboard(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                cmd = msg.get("cmd")
                if cmd == "calibrate_step1":
                    await manager.trigger_calibration("step1", msg.get("patient_id"))
                elif cmd == "calibrate_step2":
                    await manager.trigger_calibration("step2", msg.get("patient_id"))
                elif cmd == "resetCalibration":
                    await manager.trigger_calibration("reset")
                elif cmd == "setThresholds":
                    cfg = ThresholdsConfig(
                        knee_angle_min=float(msg.get("kneeMin", manager.thresholds.knee_angle_min)),
                        knee_angle_max=float(msg.get("kneeMax", manager.thresholds.knee_angle_max)),
                        fsr_imbalance_thresh=float(msg.get("fsrImb", manager.thresholds.fsr_imbalance_thresh)),
                        flex_stiff_thresh=float(msg.get("flexSt", manager.thresholds.flex_stiff_thresh)),
                        auto_record_on_stream=bool(msg.get("autoRecord", manager.thresholds.auto_record_on_stream)),
                    )
                    manager.thresholds = cfg
                    await manager.db.set_setting("thresholds", cfg.model_dump())
                    await manager.send_thresholds_to_device()
                    await manager.broadcast_status()
                elif cmd == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            manager.disconnect_dashboard(ws)
        except Exception:
            manager.disconnect_dashboard(ws)

    return app


app = create_app()

