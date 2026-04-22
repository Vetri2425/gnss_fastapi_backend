"""
Pydantic Models for API Schemas.

Defines request/response models for GNSS API endpoints using Pydantic v2.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Response Models
# =============================================================================


class GNSSStatus(BaseModel):
    """GNSS position and fix status."""

    latitude: float = Field(0.0, description="Latitude in degrees")
    longitude: float = Field(0.0, description="Longitude in degrees")
    altitude: float = Field(0.0, description="Altitude in meters (MSL)")
    accuracy: float = Field(0.0, description="Horizontal accuracy in meters")
    vertical_accuracy: float = Field(0.0, description="Vertical accuracy in meters")
    fix_type: int = Field(0, description="Fix type (0=no fix, 1=DR, 2=2D, 3=3D, 4=GNSS+DR, 5=time only/base station)")
    fix_type_str: str = Field("no_fix", description="Fix type as string")
    num_satellites: int = Field(0, description="Number of satellites used")
    carrier_solution: int = Field(0, description="Carrier solution type")
    ground_speed: float = Field(0.0, description="Ground speed in m/s")
    heading: float = Field(0.0, description="Heading in degrees")
    pdop: float = Field(0.0, description="Position DOP")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "latitude": 37.7749,
                "longitude": -122.4194,
                "altitude": 10.5,
                "accuracy": 0.02,
                "vertical_accuracy": 0.04,
                "fix_type": 6,
                "fix_type_str": "rtk_fixed",
                "num_satellites": 12,
                "carrier_solution": 2,
                "ground_speed": 0.0,
                "heading": 180.5,
                "pdop": 1.2,
                "timestamp": "2026-03-26T12:00:00.000000",
            }
        }


class SurveyStatus(BaseModel):
    """Survey-in status for base station setup."""

    active: bool = Field(False, description="Whether survey-in is active")
    valid: bool = Field(False, description="Whether survey-in is valid/complete")
    in_progress: bool = Field(False, description="Whether survey is currently in progress")
    progress: int = Field(0, ge=0, le=100, description="Progress percentage")
    accuracy: float = Field(0.0, description="Current accuracy in meters")
    observation_time: int = Field(0, ge=0, description="Observation time in seconds")
    mean_accuracy: float = Field(0.0, description="Mean accuracy in meters")
    ecef_x: float = Field(0.0, description="ECEF X coordinate in meters")
    ecef_y: float = Field(0.0, description="ECEF Y coordinate in meters")
    ecef_z: float = Field(0.0, description="ECEF Z coordinate in meters")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "active": True,
                "valid": False,
                "in_progress": True,
                "progress": 45,
                "accuracy": 0.15,
                "observation_time": 135,
                "mean_accuracy": 0.15,
                "ecef_x": 4500000.0,
                "ecef_y": 1200000.0,
                "ecef_z": 4300000.0,
                "timestamp": "2026-03-26T12:00:00.000000",
            }
        }


class RTCMStatus(BaseModel):
    """RTCM message output status."""

    enabled: bool = Field(False, description="Whether RTCM output is enabled")
    msm_type: str = Field("", description="MSM type (MSM4 or MSM7)")
    message_counts: dict[str, int] = Field(
        default_factory=dict, description="Count per message type"
    )
    data_rate: float = Field(0.0, description="Data rate in bytes/second")
    total_messages_sent: int = Field(0, description="Total messages sent")
    last_message_time: Optional[datetime] = Field(None)

    class Config:
        json_schema_extra = {
            "example": {
                "enabled": True,
                "msm_type": "MSM4",
                "message_counts": {
                    "1074": 100,
                    "1084": 100,
                    "1094": 100,
                    "1124": 100,
                },
                "data_rate": 1200.5,
                "total_messages_sent": 400,
                "last_message_time": "2026-03-26T12:00:00.000000",
            }
        }


class NTRIPStatus(BaseModel):
    """NTRIP client connection status."""

    enabled: bool = Field(False, description="Whether NTRIP is enabled")
    connected: bool = Field(False, description="Whether connected to NTRIP caster")
    host: str = Field("", description="NTRIP caster host")
    port: int = Field(0, description="NTRIP caster port")
    mountpoint: str = Field("", description="NTRIP mountpoint")
    bytes_sent: int = Field(0, description="Bytes sent to caster")
    bytes_received: int = Field(0, description="Bytes received from caster")
    uptime: float = Field(0.0, description="Connection uptime in seconds")
    data_rate_bps: float = Field(0.0, description="Current data rate in bits per second")
    error_message: Optional[str] = Field(None, description="Last error message")

    class Config:
        json_schema_extra = {
            "example": {
                "enabled": True,
                "connected": True,
                "host": "ntrip.example.com",
                "port": 2101,
                "mountpoint": "RTCM32",
                "bytes_sent": 15000,
                "bytes_received": 250000,
                "uptime": 3600.5,
                "data_rate_bps": 1024.5,
                "error_message": None,
            }
        }


class ReceiverStatus(BaseModel):
    """GNSS receiver connection status."""

    connected: bool = Field(False, description="Whether receiver is connected")
    serial_port: str = Field("", description="Serial port path")
    baudrate: int = Field(0, description="Serial baud rate")
    firmware_version: Optional[str] = Field(None, description="Firmware version")
    hardware_version: Optional[str] = Field(None, description="Hardware version")
    error_count: int = Field(0, description="Total error count")
    nak_count: int = Field(0, description="NAK response count")
    ack_count: int = Field(0, description="ACK response count")

    class Config:
        json_schema_extra = {
            "example": {
                "connected": True,
                "serial_port": "/dev/ttyUSB0",
                "baudrate": 9600,
                "firmware_version": "1.00",
                "hardware_version": "000A0000",
                "error_count": 0,
                "nak_count": 0,
                "ack_count": 15,
            }
        }


class ReaderStatus(BaseModel):
    """GNSS reader thread status."""

    is_running: bool = Field(False, description="Whether reader thread is running")
    is_connected: bool = Field(False, description="Whether serial is connected")
    messages_read: int = Field(0, description="Total messages read")
    parse_errors: int = Field(0, description="Total parse errors")
    reconnect_attempts: int = Field(0, description="Current reconnect attempts")
    inbound_queue_size: int = Field(0, description="Inbound queue size")
    outbound_queue_size: int = Field(0, description="Outbound queue size")


class FullStatus(BaseModel):
    """Complete GNSS system status."""

    position: GNSSStatus
    survey: SurveyStatus
    rtcm: RTCMStatus
    ntrip: NTRIPStatus
    receiver: ReceiverStatus
    reader: ReaderStatus
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Request Models
# =============================================================================


class CommandRequest(BaseModel):
    """Command request model."""

    type: str = Field(..., description="Command type")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Command parameters"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "type": "survey_start",
                    "params": {"min_duration": 300, "accuracy_limit": 0.10},
                },
                {"type": "survey_stop", "params": {}},
                {"type": "rtcm_enable", "params": {"msm_type": "MSM4"}},
                {"type": "rtcm_disable", "params": {}},
                {"type": "poll_svin", "params": {}},
                {"type": "poll_pvt", "params": {}},
                {
                    "type": "base_mode",
                    "params": {"msm_type": "MSM4", "survey_mode": True},
                },
            ]
        }


class CommandResponse(BaseModel):
    """Command response model."""

    success: bool = Field(..., description="Whether command succeeded")
    message: str = Field(..., description="Response message")
    type: Optional[str] = Field(None, description="Command type")
    data: Optional[dict[str, Any]] = Field(None, description="Response data")
    warning: Optional[bool] = Field(None, description="Whether response is a warning, not execution")
    current_state: Optional[str] = Field(None, description="Current AutoFlow state (for warnings)")


class SurveyStartRequest(BaseModel):
    """Survey start configuration request."""

    min_duration: int = Field(10, ge=10, le=86400, description="Minimum duration in seconds")
    accuracy_limit: float = Field(0.10, ge=0.001, le=10.0, description="Accuracy limit in meters")
    force: bool = Field(False, description="Force survey start even if AutoFlow is active (operator override)")


class RTCMConfigRequest(BaseModel):
    """RTCM configuration request."""

    msm_type: str = Field("MSM4", pattern="^(MSM4|MSM7)$", description="MSM type")
    enable: bool = Field(True, description="Enable or disable RTCM")
    enable_beidou: Optional[bool] = Field(None, description="Doc-compat: True selects MSM7 (includes BeiDou)")


class BaseModeRequest(BaseModel):
    """Base station mode configuration request."""

    msm_type: str = Field("MSM4", pattern="^(MSM4|MSM7)$", description="MSM type")
    survey_mode: bool = Field(True, description="Use survey mode vs fixed mode")
    min_duration: int = Field(10, ge=10, le=86400, description="Minimum survey duration")
    accuracy_limit: float = Field(0.10, ge=0.001, le=10.0, description="Accuracy limit")
    # Fixed mode coordinates (if survey_mode=False)
    ecef_x: Optional[float] = Field(None, description="ECEF X in meters")
    ecef_y: Optional[float] = Field(None, description="ECEF Y in meters")
    ecef_z: Optional[float] = Field(None, description="ECEF Z in meters")


class FixedBaseRequest(BaseModel):
    """Fixed base station LLH configuration request."""

    latitude: float = Field(..., ge=-90, le=90, description="Latitude in decimal degrees")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude in decimal degrees")
    height: float = Field(
        ...,
        description=(
            "Height in meters. By default this must be the WGS84 ellipsoid height (HAE). "
            "If you only have an MSL/orthometric height, supply geoid_separation and the "
            "API will compute ellipsoid_height = height + geoid_separation automatically."
        ),
    )
    geoid_separation: Optional[float] = Field(
        None,
        description=(
            "EGM96/EGM2008 geoid undulation N in meters for your location. "
            "When provided, height is treated as MSL and converted: "
            "ellipsoid_h = height + geoid_separation. "
            "Typical values: −6.5 m for Chennai, +50 m for parts of India."
        ),
    )
    fixed_pos_acc: float = Field(0.10, ge=0.001, le=10.0, description="Position accuracy in meters")
    msm_type: str = Field("MSM4", pattern="^(MSM4|MSM7)$", description="RTCM MSM type to enable after config")
    enable_rtcm: bool = Field(True, description="Re-enable RTCM output after applying fixed config")
    use_high_precision: bool = Field(False, description="Use high-precision HP fields for sub-cm accuracy")
    lat_hp: Optional[float] = Field(None, ge=-1e-9, le=1e-9, description="Optional high-precision lat offset (deg)")
    lon_hp: Optional[float] = Field(None, ge=-1e-9, le=1e-9, description="Optional high-precision lon offset (deg)")
    height_hp: Optional[float] = Field(None, description="Optional high-precision height offset (m)")
    save_to_flash: bool = Field(False, description="Persist to Flash (default: RAM+BBR only)")


class FixedBaseResponse(BaseModel):
    """Fixed base station configuration response."""

    success: bool = Field(..., description="Whether command succeeded")
    message: str = Field(..., description="Response message")
    effective_mode: str = Field("FIXED", description="Applied TMODE mode")
    applied_llh: dict[str, float] = Field(
        ...,
        description=(
            "Applied LLH coordinates. height_ellipsoid is the WGS84 value sent to the "
            "receiver; height_input is what was passed in the request; "
            "geoid_separation is included when MSL→ellipsoid conversion was performed."
        ),
    )
    applied_accuracy: float = Field(..., description="Applied accuracy in meters")
    layers_applied: str = Field(..., description="Memory layers applied (RAM, RAM+BBR, or RAM+BBR+FLASH)")
    rtcm_enabled: bool = Field(False, description="Whether RTCM was re-enabled after config")


class BaseReferenceStatusResponse(BaseModel):
    """Current survey reference and last applied fixed-base reference."""

    fixed_reference: Optional[dict[str, Any]] = Field(
        None,
        description="Last fixed-base reference applied by the API in this process",
    )
    survey_reference: Optional[dict[str, Any]] = Field(
        None,
        description="Survey-in reference from NAV-SVIN, with LLH derived from surveyed ECEF",
    )


class NTRIPStartRequest(BaseModel):
    """NTRIP streaming start request."""

    host: str = Field(..., description="NTRIP caster hostname or IP")
    port: int = Field(2101, ge=1, le=65535, description="NTRIP caster port")
    mountpoint: str = Field(..., description="Mountpoint name (without leading /)")
    password: str = Field(..., description="NTRIP caster password")
    username: Optional[str] = Field(None, description="NTRIP username (None for Emlid-style)")
    ntrip_version: int = Field(1, ge=1, le=2, description="NTRIP protocol version")


# =============================================================================
# AutoFlow Schemas
# =============================================================================


class AutoflowConfigRequest(BaseModel):
    """AutoFlow configuration — save and optionally start/stop the flow."""

    enabled: bool = Field(False, description="Enable autoflow on startup")
    min_duration_sec: int = Field(10, ge=10, le=86400, description="Survey min duration (s)")
    accuracy_limit_m: float = Field(0.10, ge=0.001, le=10.0, description="Survey accuracy limit (m)")
    msm_type: str = Field("MSM4", pattern="^(MSM4|MSM7)$", description="RTCM MSM type")
    ntrip_host: str = Field("", description="NTRIP caster hostname or IP")
    ntrip_port: int = Field(2101, ge=1, le=65535, description="NTRIP caster port")
    ntrip_mountpoint: str = Field("", description="NTRIP mountpoint (without leading /)")
    ntrip_username: str = Field("", description="NTRIP username")
    ntrip_password: str = Field("", description="NTRIP password")
    ntrip_version: int = Field(1, ge=1, le=2, description="NTRIP protocol version (1 or 2)")


class AutoflowNTRIPStatus(BaseModel):
    """NTRIP push client status."""

    connected: bool = Field(False)
    bytes_sent: int = Field(0)
    connect_attempts: int = Field(0)
    last_error: Optional[str] = Field(None)
    host: str = Field("")
    port: int = Field(0)
    mountpoint: str = Field("")


class AutoflowStatusResponse(BaseModel):
    """AutoFlow orchestrator status."""

    state: str = Field("IDLE", description="Current state")
    enabled: bool = Field(False)
    last_error: Optional[str] = Field(None)
    config: Optional[dict[str, Any]] = Field(None)
    ntrip: Optional[dict[str, Any]] = Field(None)
    survey_elapsed: Optional[int] = Field(None, description="Survey elapsed seconds")
    stuck_retries: int = Field(0, description="Survey stuck retry count")
    location_change_pending: Optional[dict[str, Any]] = Field(
        None, description="Location change confirmation pending state"
    )


class LoRaStatus(BaseModel):
    enabled: bool
    connected: bool
    port: str
    baudrate: int
    packet_size: Optional[int] = None
    bytes_sent: int
    frames_sent: int
    write_errors: Optional[int] = None
    data_rate_bps: float
    uptime: float
    queue_size: Optional[int] = None


class LoRaConfigRequest(BaseModel):
    port: Optional[str] = None
    baudrate: Optional[int] = None
    packet_size: Optional[int] = None


class AutoflowConfigResponse(BaseModel):
    """AutoFlow configuration response — password masked."""

    enabled: bool = Field(False, description="Enable autoflow on startup")
    min_duration_sec: int = Field(10, ge=10, le=86400, description="Survey min duration (s)")
    accuracy_limit_m: float = Field(0.10, ge=0.001, le=10.0, description="Survey accuracy limit (m)")
    msm_type: str = Field("MSM4", pattern="^(MSM4|MSM7)$", description="RTCM MSM type")
    ntrip_host: str = Field("", description="NTRIP caster hostname or IP")
    ntrip_port: int = Field(2101, ge=1, le=65535, description="NTRIP caster port")
    ntrip_mountpoint: str = Field("", description="NTRIP mountpoint (without leading /)")
    ntrip_username: str = Field("", description="NTRIP username")
    ntrip_password: str = Field("", description="NTRIP password (masked)")
    ntrip_version: int = Field(1, ge=1, le=2, description="NTRIP protocol version (1 or 2)")
