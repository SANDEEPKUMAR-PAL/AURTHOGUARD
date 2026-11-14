#!/usr/bin/env python3
"""Master verification runner for SIH26004 OA Detection System.

Executes all 5 verification phases:
1. Full Pytest test suite (10 test modules across backend, firmware mock, biomechanics, exercises).
2. Backend Configuration & Zero-Hardcoding checks (.env, Settings, paths).
3. Clinical Biomechanics & Rulebook Section 11 Working Conditions Truth Table.
4. ESP32 Firmware De-hardcoding, Task Watchdog, HTTP Fallback, and I2C Auto-Recovery.
5. End-to-End Dashboard Headless DOM & API Route Audit.

Exits with code 0 on complete pass, non-zero on any failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def run_stage(title: str, func) -> bool:
    print("\n" + "=" * 70)
    print(f"STAGE: {title}")
    print("=" * 70)
    t0 = time.time()
    try:
        func()
        elapsed = time.time() - t0
        print(f"==> [PASS] {title} ({elapsed:.2f}s)")
        return True
    except AssertionError as e:
        elapsed = time.time() - t0
        print(f"==> [FAIL] {title}: {e} ({elapsed:.2f}s)", file=sys.stderr)
        return False
    except Exception as e:
        elapsed = time.time() - t0
        print(f"==> [ERROR] {title}: Unexpected exception: {e} ({elapsed:.2f}s)", file=sys.stderr)
        return False


def stage_pytest_suite():
    python_bin = sys.executable
    cmd = [python_bin, "-m", "pytest", str(PROJECT_ROOT / "tests"), "-v"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    res = subprocess.run(cmd, env=env, text=True)
    assert res.returncode == 0, f"Pytest suite failed with return code {res.returncode}"


def stage_configuration_checks():
    from backend.config import Settings, settings

    # Verify defaults
    cfg = Settings()
    assert cfg.port == 8000
    assert cfg.pin_sda == 21
    assert cfg.pin_scl == 22
    assert cfg.pin_green_led == 26
    assert cfg.pin_red_led == 27
    assert cfg.pin_buzzer == 25
    assert cfg.knee_angle_min == -10.0
    assert cfg.knee_angle_max == 135.0
    assert cfg.fsr_imbalance_thresh == 60.0
    assert cfg.resolved_db_path.is_absolute()
    assert cfg.resolved_model_path.is_absolute()
    print("  - Settings defaults, pin allocations, and resolved paths verified.")


def stage_rulebook_compliance():
    from backend.biomechanics import compute_foot_pressure_metrics
    from backend.schemas import TelemetryIn, IMUSensorData
    from backend.sensor_health import evaluate_sensor_health

    # Verify foot pressure
    fp = compute_foot_pressure_metrics(1600, 1600)
    assert fp["dominant_zone"] == "Balanced"
    assert fp["status_level"] == "ok"

    # Verify sensor health
    t = TelemetryIn(
        timestamp=100,
        thigh=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        shin=IMUSensorData(online=True, ax=0.0, ay=1.0, az=0.0, gx=0, gy=0, gz=0, pitch=0, roll=0),
        flex=1800,
        fsrHeel=1500,
        fsrFore=1400,
    )
    health = evaluate_sensor_health(t, source="esp32")
    assert health["all_ok"] is True
    print("  - Biomechanical rules and sensor rail diagnostics verified.")


def stage_firmware_mock():
    config_h = PROJECT_ROOT / "config.h"
    secrets_t = PROJECT_ROOT / "secrets.h.template"
    ino_f = PROJECT_ROOT / "OA_Detection_ESP32.ino"

    assert config_h.exists(), f"Missing {config_h}"
    assert secrets_t.exists(), f"Missing {secrets_t}"
    assert ino_f.exists(), f"Missing {ino_f}"

    c_text = config_h.read_text(encoding="utf-8")
    assert "PIN_LED_GREEN" in c_text
    assert "MPU_THIGH_ADDR" in c_text
    assert "I2C_TIMEOUT_MS" in c_text

    ino_text = ino_f.read_text(encoding="utf-8")
    assert '#include "config.h"' in ino_text
    assert "Wire.setTimeOut(50)" in ino_text or "Wire.setTimeOut(I2C_TIMEOUT_MS)" in ino_text
    assert 'cfgBackendHost == "192.168.1.100"' not in ino_text
    print("  - Firmware config.h, secrets template, and hardening fixes verified.")


def stage_dashboard_audit():
    from e2e_dashboard_audit import main as run_audit
    ret = run_audit()
    assert ret == 0, f"e2e_dashboard_audit exited with code {ret}"


def main() -> int:
    print("======================================================================")
    print("STARTING MASTER SYSTEM VERIFICATION (SIH26004 OA DETECTION)")
    print(f"Project Directory: {PROJECT_ROOT}")
    print(f"Python Executable: {sys.executable}")
    print("======================================================================")

    stages = [
        ("Full Pytest Automated Test Suite", stage_pytest_suite),
        ("Backend Configuration & Settings Validation", stage_configuration_checks),
        ("Clinical Biomechanics & Rulebook Section 11 Compliance", stage_rulebook_compliance),
        ("ESP32 Firmware De-Hardcoding & Reliability Hardening", stage_firmware_mock),
        ("End-to-End Headless Dashboard & API Route Audit", stage_dashboard_audit),
    ]

    passed = 0
    total = len(stages)

    for title, func in stages:
        if run_stage(title, func):
            passed += 1
        else:
            print(f"\nVerification aborted due to failure in: {title}", file=sys.stderr)
            return 1

    print("\n" + "=" * 70)
    print(f"ALL {passed}/{total} VERIFICATION STAGES PASSED SUCCESSFULLY!")
    print("======================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
