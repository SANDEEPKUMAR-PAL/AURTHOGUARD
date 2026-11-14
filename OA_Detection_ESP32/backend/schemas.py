"""Pydantic request, response, and WebSocket payload schemas."""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from backend.config import settings


# ==================== Sensor & Telemetry Models ====================


class IMUSensorData(BaseModel):
    ax: float = 0.0
    ay: float = 0.0
    az: float = 0.0
    gx: float = 0.0
    gy: float = 0.0
    gz: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    pitchC: Optional[float] = None
    rollC: Optional[float] = None
    online: bool = True


class TelemetryIn(BaseModel):
    """Telemetry frame sent by ESP32 (WebSocket or HTTP POST) or simulator."""

    type: str = "data"
    timestamp: int = 0
    calibrated: bool = False
    calibrationStep: int = 0
    thigh: IMUSensorData = Field(default_factory=IMUSensorData)
    shin: IMUSensorData = Field(default_factory=IMUSensorData)
    flex: int = 0
    flexNorm: Optional[float] = None
    flexBaseline: int = Field(default_factory=lambda: settings.cal_flex_straight_default)
    fsrHeel: int = 0
    fsrFore: int = 0
    kneeAngle: float = 0.0
    kneeAngleC: Optional[float] = None
    fsrImbalance: float = 0.0
    abnormal: bool = False
    reason: str = ""
    source: str = "esp32"


# ==================== Patient & Calibration Models ====================


class CalibrationPayload(BaseModel):
    thigh_pitch_offset: float = 0.0
    thigh_roll_offset: float = 0.0
    shin_pitch_offset: float = 0.0
    shin_roll_offset: float = 0.0
    thigh_polarity: int = Field(default=1, ge=-1, le=1)
    shin_polarity: int = Field(default=1, ge=-1, le=1)
    flex_straight: int = Field(default_factory=lambda: settings.cal_flex_straight_default)
    flex_bent: int = Field(default_factory=lambda: settings.cal_flex_bent_default)
    fsr_heel_tare: int = 0
    fsr_fore_tare: int = 0
    is_two_point: bool = False


class CalibrationOut(CalibrationPayload):
    id: int
    patient_id: int
    is_active: bool
    calibrated_at: str


class PatientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    age: Optional[int] = Field(default=None, ge=1, le=120)
    gender: str = "Male"
    monitored_leg: str = "Left"
    height_cm: Optional[float] = Field(default=None, ge=40, le=250)
    weight_kg: Optional[float] = Field(default=None, ge=10, le=300)
    region_district: str = ""
    notes: str = ""


class PatientUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    age: Optional[int] = Field(default=None, ge=1, le=120)
    gender: Optional[str] = None
    monitored_leg: Optional[str] = None
    height_cm: Optional[float] = Field(default=None, ge=40, le=250)
    weight_kg: Optional[float] = Field(default=None, ge=10, le=300)
    region_district: Optional[str] = None
    notes: Optional[str] = None


class PatientOut(BaseModel):
    id: int
    name: str
    age: Optional[int] = None
    gender: str
    monitored_leg: str
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    region_district: str
    notes: str
    created_at: str
    updated_at: str
    session_count: int = 0
    active_calibration: Optional[CalibrationOut] = None


# ==================== Session & Alert Models ====================


class SessionStartRequest(BaseModel):
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    clinician_name: str = ""
    monitored_leg: Optional[Literal["Left", "Right"]] = None
    source: str = "esp32"
    notes: str = ""


class SessionStopRequest(BaseModel):
    notes: Optional[str] = None


class SessionUpdateRequest(BaseModel):
    notes: str = Field(default="", max_length=1000)


class SessionOut(BaseModel):
    id: int
    patient_id: Optional[int] = None
    patient_name: str
    clinician_name: str
    monitored_leg: str = "Left"
    calibration_id: Optional[int] = None
    status: str
    source: str
    start_time: str
    end_time: Optional[str] = None
    sample_count: int = 0
    alert_count: int = 0
    avg_knee_angle: Optional[float] = None
    min_knee_angle: Optional[float] = None
    max_knee_angle: Optional[float] = None
    rom_deg: Optional[float] = None
    oa_risk_score: Optional[float] = None
    oa_risk_tier: Optional[str] = None
    risk_markers: list[str] = Field(default_factory=list)
    notes: str = ""


class AlertOut(BaseModel):
    id: int
    session_id: Optional[int] = None
    patient_id: Optional[int] = None
    severity: str
    marker_type: str
    message: str
    knee_angle: Optional[float] = None
    oa_risk_score: Optional[float] = None
    created_at: str


# ==================== Device, Thresholds, AI & Simulator ====================


class ThresholdsConfig(BaseModel):
    knee_angle_min: float = Field(default_factory=lambda: settings.knee_angle_min, ge=-30.0, le=60.0)
    knee_angle_max: float = Field(default_factory=lambda: settings.knee_angle_max, ge=60.0, le=180.0)
    fsr_imbalance_thresh: float = Field(default_factory=lambda: settings.fsr_imbalance_thresh, ge=10.0, le=100.0)
    flex_stiff_thresh: float = Field(default_factory=lambda: settings.flex_stiff_thresh, ge=0.05, le=0.95)
    auto_record_on_stream: bool = True


class ESP32ConnectRequest(BaseModel):
    ip: str = Field(..., min_length=3, max_length=120)
    port: int = Field(default_factory=lambda: settings.esp32_pull_bridge_port, ge=1, le=65535)


class CalibrationCommandRequest(BaseModel):
    step: Literal["step1", "step2", "reset"]
    patient_id: Optional[int] = None


class SimulatorStartRequest(BaseModel):
    profile: Literal[
        "normal",
        "early_oa",
        "severe_oa",
        "hyperflexion",
        "ex_seated_flex",
        "ex_sit_to_stand",
        "ex_mini_squat",
        "ex_weight_shift",
    ] = "normal"
    rate_hz: float = Field(default=10.0, ge=1.0, le=20.0)


class ExerciseStartRequest(BaseModel):
    exercise_key: Literal[
        "normal_walk",
        "seated_flex_ext",
        "sit_to_stand",
        "mini_squat",
        "weight_shift",
    ]
    patient_id: Optional[int] = None
    monitored_leg: Optional[Literal["Left", "Right"]] = None
    lang: Literal["en", "hi"] = "en"
    auto_simulate: bool = False


class ExerciseTrialOut(BaseModel):
    id: int
    patient_id: Optional[int] = None
    session_id: Optional[int] = None
    monitored_leg: str = "Left"
    exercise_key: str
    exercise_name: str
    status: str
    target_reps: int
    completed_reps: int
    quality_score: Optional[float] = None
    finding: Optional[str] = None
    avg_rom_deg: Optional[float] = None
    max_flexion_deg: Optional[float] = None
    min_flexion_deg: Optional[float] = None
    avg_imbalance_pct: Optional[float] = None
    roll_wobble_deg: Optional[float] = None
    flex_agreement_pct: Optional[float] = None
    markers: list[str] = Field(default_factory=list)
    started_at: str
    ended_at: Optional[str] = None


class GaitFeatures(BaseModel):
    rom_deg: float = 0.0
    peak_flexion_deg: float = 0.0
    extension_deficit_deg: float = 0.0
    mean_angular_vel_dps: float = 0.0
    jerk_index: float = 0.0
    fsr_mean_imbalance_pct: float = 0.0
    fsr_peak_loading_ratio: float = 0.0
    flex_stiffness_index: float = 0.0
    cadence_spm: float = 0.0
    stance_pct: float = 0.0
    varus_vel_dps: float = 0.0
    peak_impact_g: float = 1.0


class RiskDrivers(BaseModel):
    rom_deficit_pct: int = 0
    stiffness_pct: int = 0
    asymmetry_pct: int = 0
    jerk_wobble_pct: int = 0


class AIRiskSnapshot(BaseModel):
    window_samples: int = 0
    ready: bool = False
    oa_risk_score: float = 0.0
    oa_risk_tier: Literal["Pending", "Low", "Moderate", "High"] = "Pending"
    confidence: float = 0.0
    markers: list[str] = Field(default_factory=list)
    features: GaitFeatures = Field(default_factory=GaitFeatures)
    risk_drivers: RiskDrivers = Field(default_factory=RiskDrivers)
    model_type: str = "RandomForest+Biomechanical"


class DeviceStatusOut(BaseModel):
    esp32_connected: bool
    esp32_source: Optional[str] = None
    esp32_remote_ip: Optional[str] = None
    pull_bridge_target: Optional[str] = None
    simulator_running: bool
    simulator_profile: Optional[str] = None
    dashboard_clients: int
    active_session_id: Optional[int] = None
    active_patient_id: Optional[int] = None
    active_leg: str = "Left"
    language: str = "en"
    calibrated: bool
    calibration_step: int = 0
    active_calibration: Optional[dict[str, Any]] = None
    thresholds: ThresholdsConfig
    lan_ips: list[str] = Field(default_factory=list)
    ai_latest: AIRiskSnapshot = Field(default_factory=AIRiskSnapshot)
    active_exercise: Optional[dict[str, Any]] = None
    sensor_health: Optional[dict[str, Any]] = None
    display_timezone: str = "Asia/Kolkata"
    display_timezone_label: str = "IST"


class CalibrationApplyRequest(CalibrationPayload):
    patient_id: Optional[int] = None


class AutoCalibrationRequest(BaseModel):
    patient_id: Optional[int] = None
    samples: int = Field(default=8, ge=1, le=30)


class JointFootStimulationRequest(BaseModel):
    knee_angle_deg: float = Field(default=30.0, ge=-25.0, le=160.0)
    thigh_pitch_deg: float = Field(default=10.0, ge=-40.0, le=90.0)
    net_roll_deg: float = Field(default=1.5, ge=-30.0, le=30.0)
    heel_fsr: int = Field(default=1650, ge=0, le=4095)
    fore_fsr: int = Field(default=1600, ge=0, le=4095)
    flex_stiffness: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    repeat: int = Field(default=1, ge=1, le=40)


class LegSelectRequest(BaseModel):
    leg: Literal["Left", "Right"]


class LanguageSelectRequest(BaseModel):
    lang: Literal["en", "hi"]

