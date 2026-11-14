"""Application configuration using Pydantic Settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime settings for the OA Risk Detection backend."""

    model_config = SettingsConfigDict(
        env_prefix="OA_",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    db_path: str = "data/oa_system.db"
    model_path: str = "data/oa_risk_model.joblib"
    cors_origins: list[str] = ["*"]
    default_lan_ip: str = "127.0.0.1"

    # Outbound pull bridge & simulator streaming
    esp32_pull_bridge_port: int = 81
    bridge_reconnect_delay_sec: float = 3.0
    simulator_default_hz: float = 10.0
    http_heartbeat_timeout_sec: float = 5.0

    # Hardware Pin Allocations (ESP32)
    pin_sda: int = 21
    pin_scl: int = 22
    pin_flex: int = 32
    pin_fsr_heel: int = 34
    pin_fsr_fore: int = 35
    pin_green_led: int = 26
    pin_red_led: int = 27
    pin_buzzer: int = 25

    # I2C Addresses
    imu_thigh_i2c_addr: int = 0x68
    imu_shin_i2c_addr: int = 0x69

    # Sensor Rail Bounds & Hardware Sanity Limits
    imu_accel_min_g: float = 0.25
    imu_accel_max_g: float = 4.5
    flex_open_circuit_adc: int = 15
    flex_shorted_adc: int = 4085
    fsr_saturated_adc: int = 4088
    fsr_loaded_min_adc: int = 80

    # Clinical Knee Flexion & Plantar Limits (AAOS Goniometry + Gait Literature):
    # -10° (max physiological hyperextension), 135° (AAOS normal flexion limit),
    # 60% (sustained unilateral antalgic FSR offloading threshold), 0.25 (tendon stiffness ratio)
    knee_angle_min: float = -10.0
    knee_angle_max: float = 135.0
    fsr_imbalance_thresh: float = 60.0
    flex_stiff_thresh: float = 0.25
    varus_velocity_thresh: float = 28.1
    varus_wobble_thresh: float = 6.5
    bilateral_rom_asymmetry_thresh: float = 8.0
    bilateral_risk_diff_thresh: float = 15.0
    acr_age_threshold: int = 45
    extreme_angle_margin: float = 5.0
    angle_tolerance_deg: float = 3.5
    fsr_active_min_total: int = 220

    # Biomechanical Force Divisors & Boundaries
    fsr_imbalance_warning_thresh: float = 30.0
    fsr_force_scale_divisor: float = 40.95
    fsr_total_load_divisor: float = 64.0

    # Calibration & Sampling Counts
    calibration_target_samples: int = 20
    auto_calibration_samples: int = 8
    cal_flex_straight_default: int = 2048
    cal_flex_bent_default: int = 1200

    # AI Sliding Window Configuration (10 Hz stream -> 40 samples = 4s)
    ai_window_size: int = 40
    ai_step_interval: int = 5

    # Batching interval for SQLite telemetry writes
    db_flush_batch_size: int = 10

    # Display & Reporting Timezone (defaults to IST / Asia/Kolkata, UTC+05:30)
    display_timezone: str = "Asia/Kolkata"
    display_timezone_label: str = "IST"
    tz_offset_minutes: int = 330

    def get_display_tz(self):
        """Return configured display tzinfo, falling back to fixed offset."""
        from datetime import timedelta, timezone

        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                return ZoneInfo(self.display_timezone)
            except (ZoneInfoNotFoundError, KeyError, ValueError):
                pass
        except ImportError:
            pass
        return timezone(timedelta(minutes=self.tz_offset_minutes))

    def now_tz_iso(self) -> str:
        """Return current timestamp in the configured display timezone (ISO-8601)."""
        from datetime import datetime

        return datetime.now(self.get_display_tz()).isoformat()

    def format_timestamp_tz(
        self,
        iso_val: str | None,
        *,
        include_label: bool = True,
        include_seconds: bool = True,
    ) -> str:
        """Convert an ISO-8601 timestamp (UTC, offset-aware, or naive) to display timezone."""
        if not iso_val:
            return ""
        from datetime import datetime, timezone

        raw = str(iso_val).strip()
        if not raw:
            return ""
        try:
            normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(self.get_display_tz())
            fmt = "%Y-%m-%d %H:%M:%S" if include_seconds else "%Y-%m-%d %H:%M"
            formatted = local_dt.strftime(fmt)
            if include_label and self.display_timezone_label:
                return f"{formatted} {self.display_timezone_label}"
            return formatted
        except ValueError:
            return raw[:19].replace("T", " ")

    @property
    def resolved_db_path(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def resolved_model_path(self) -> Path:
        p = Path(self.model_path)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def dashboard_html_path(self) -> Path:
        return BASE_DIR / "dashboard.html"


settings = Settings()
