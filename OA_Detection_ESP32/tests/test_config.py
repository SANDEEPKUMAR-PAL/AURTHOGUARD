"""Unit tests for backend configuration and environment overrides."""
import os
import pytest
from pathlib import Path
from backend.config import Settings, settings


def test_default_settings():
    """Verify that default settings conform to the SIH26004 specification."""
    cfg = Settings()
    # Host & Ports
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8000
    assert cfg.esp32_pull_bridge_port == 81

    # Hardware Pins
    assert cfg.pin_sda == 21
    assert cfg.pin_scl == 22
    assert cfg.pin_flex == 32
    assert cfg.pin_fsr_heel == 34
    assert cfg.pin_fsr_fore == 35
    assert cfg.pin_green_led == 26
    assert cfg.pin_red_led == 27
    assert cfg.pin_buzzer == 25

    # I2C Addresses
    assert cfg.imu_thigh_i2c_addr == 0x68
    assert cfg.imu_shin_i2c_addr == 0x69

    # Clinical Biomechanical Thresholds (AAOS goniometry + gait norms)
    assert cfg.knee_angle_min == -10.0
    assert cfg.knee_angle_max == 135.0
    assert cfg.fsr_imbalance_thresh == 60.0
    assert cfg.fsr_imbalance_warning_thresh == 30.0
    assert cfg.flex_stiff_thresh == 0.25
    assert cfg.varus_velocity_thresh == 28.1
    assert cfg.varus_wobble_thresh == 6.5
    assert cfg.bilateral_rom_asymmetry_thresh == 8.0
    assert cfg.bilateral_risk_diff_thresh == 15.0

    # Sensor Rail Diagnostics (12-bit ADC & Accel limits)
    assert cfg.flex_open_circuit_adc == 15
    assert cfg.flex_shorted_adc == 4085
    assert cfg.fsr_saturated_adc == 4088
    assert cfg.imu_accel_min_g == 0.25
    assert cfg.imu_accel_max_g == 4.50


def test_settings_env_overrides(monkeypatch):
    """Verify that environment variables with OA_ prefix correctly override defaults."""
    monkeypatch.setenv("OA_PORT", "9050")
    monkeypatch.setenv("OA_KNEE_ANGLE_MIN", "-15.5")
    monkeypatch.setenv("OA_KNEE_ANGLE_MAX", "145.0")
    monkeypatch.setenv("OA_FSR_IMBALANCE_THRESH", "75.0")
    monkeypatch.setenv("OA_VARUS_VELOCITY_THRESH", "32.0")
    monkeypatch.setenv("OA_PIN_GREEN_LED", "33")

    overridden = Settings()
    assert overridden.port == 9050
    assert overridden.knee_angle_min == -15.5
    assert overridden.knee_angle_max == 145.0
    assert overridden.fsr_imbalance_thresh == 75.0
    assert overridden.varus_velocity_thresh == 32.0
    assert overridden.pin_green_led == 33


def test_resolved_paths_and_directories():
    """Verify database and model file paths resolve to valid project locations."""
    cfg = Settings()
    assert cfg.resolved_db_path.is_absolute()
    assert str(cfg.resolved_db_path).endswith("oa_system.db")

    assert cfg.resolved_model_path.is_absolute()
    assert str(cfg.resolved_model_path).endswith(".joblib") or str(cfg.resolved_model_path).endswith(".pkl")

    assert cfg.dashboard_html_path.is_absolute()
    assert cfg.dashboard_html_path.exists()


def test_singleton_settings_instance():
    """Verify that the module-level settings singleton is active and typed."""
    assert isinstance(settings, Settings)
    assert settings.port > 0
    assert settings.knee_angle_max > settings.knee_angle_min
