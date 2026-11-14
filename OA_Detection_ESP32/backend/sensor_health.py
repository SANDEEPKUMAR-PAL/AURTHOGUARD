"""Per-sensor hardware health and loose-wire diagnostics for the 5-sensor wearable."""

from __future__ import annotations

import math
from typing import Any

from backend.config import settings
from backend.schemas import TelemetryIn


def evaluate_sensor_health(telemetry: TelemetryIn, source: str = "esp32") -> dict[str, Any]:
    """Inspect all 5 sensors for I2C dropouts or ADC rail faults.

    When either MPU6050 is offline (`imu_ready == False`), AI OA risk scoring is paused
    so a loose wire does not trigger false High OA Risk clinical reports.
    """
    issues: list[str] = []

    def _imu_status(imu, name: str, addr: str) -> dict[str, str]:
        acc_mag = math.sqrt(imu.ax ** 2 + imu.ay ** 2 + imu.az ** 2)
        if not imu.online:
            issues.append(f"{name} ({addr}) offline — check I2C SDA/SCL/VCC")
            return {"status": "fault", "label": name, "detail": f"{addr} offline"}
        # Heel-strike impact transients at ±4g range can reach ~3.5g; stall <min_g or >max_g indicates mount/rail fault
        if source != "simulator" and acc_mag > 0.0 and (acc_mag < settings.imu_accel_min_g or acc_mag > settings.imu_accel_max_g):
            issues.append(f"{name} ({addr}) accel out of range ({acc_mag:.2f}g)")
            return {"status": "warn", "label": name, "detail": f"Check mount ({acc_mag:.1f}g)"}
        return {"status": "ok", "label": name, "detail": f"{addr} OK"}

    thigh_addr = f"0x{settings.imu_thigh_i2c_addr:02x}"
    shin_addr = f"0x{settings.imu_shin_i2c_addr:02x}"
    thigh_st = _imu_status(telemetry.thigh, "Thigh IMU", thigh_addr)
    shin_st = _imu_status(telemetry.shin, "Shin IMU", shin_addr)

    # Flex sensor (GPIO pin with 10k pull-down): <=flex_open_circuit_adc open circuit, >=flex_shorted_adc shorted to 3V3
    flex_pin_str = f"GPIO {settings.pin_flex}"
    if telemetry.flex <= settings.flex_open_circuit_adc:
        flex_st = {"status": "fault", "label": "Knee Flex", "detail": f"Open/disconnected ({flex_pin_str})"}
        issues.append(f"Flex sensor disconnected or open circuit ({flex_pin_str})")
    elif telemetry.flex >= settings.flex_shorted_adc:
        flex_st = {"status": "warn", "label": "Knee Flex", "detail": f"Shorted to 3.3V ({flex_pin_str})"}
        issues.append(f"Flex sensor reading saturated at 3.3V rail ({flex_pin_str})")
    else:
        flex_st = {"status": "ok", "label": "Knee Flex", "detail": f"ADC {telemetry.flex}"}

    def _fsr_status(raw: int, name: str, pin: str) -> dict[str, str]:
        if raw >= settings.fsr_saturated_adc:
            issues.append(f"{name} saturated at 3.3V rail ({pin})")
            return {"status": "warn", "label": name, "detail": f"Rail saturated ({pin})"}
        return {"status": "ok", "label": name, "detail": "Loaded" if raw > settings.fsr_loaded_min_adc else "Unloaded"}

    heel_pin_str = f"GPIO {settings.pin_fsr_heel}"
    fore_pin_str = f"GPIO {settings.pin_fsr_fore}"
    heel_st = _fsr_status(telemetry.fsrHeel, "Heel FSR", heel_pin_str)
    fore_st = _fsr_status(telemetry.fsrFore, "Forefoot FSR", fore_pin_str)

    imu_ready = (thigh_st["status"] == "ok") and (shin_st["status"] == "ok")
    all_ok = len(issues) == 0

    return {
        "all_ok": all_ok,
        "imu_ready": imu_ready,
        "issues": issues,
        "sensors": {
            "imu_thigh": thigh_st,
            "imu_shin": shin_st,
            "flex": flex_st,
            "fsr_heel": heel_st,
            "fsr_fore": fore_st,
        },
    }
