"""
GNSS State Management.

Thread-safe state management for GNSS receiver data including
position, survey status, RTCM status, and NTRIP connection state.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

_FIX_TYPE_STR = {
    0: "no_fix",
    1: "dr_only",
    2: "fix_2d",
    3: "fix_3d",
    4: "gnss_dr",
    5: "time_only",
}


@dataclass
class PositionFix:
    """GNSS position fix data."""

    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    accuracy: float = 0.0
    vertical_accuracy: float = 0.0
    fix_type: int = 0  # 0=no fix, 1=DR, 2=2D, 3=3D, 4=GNSS+DR, 5=time only (base station)
    num_satellites: int = 0
    hdop: float = 0.0
    vdop: float = 0.0
    pdop: float = 0.0
    velocity_north: float = 0.0
    velocity_east: float = 0.0
    velocity_down: float = 0.0
    ground_speed: float = 0.0
    heading: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SurveyStatus:
    """Survey-in status data."""

    active: bool = False
    valid: bool = False
    in_progress: bool = False
    progress: int = 0  # Percentage 0-100
    accuracy: float = 0.0  # meters
    observation_time: int = 0  # seconds
    ecef_x: float = 0.0
    ecef_y: float = 0.0
    ecef_z: float = 0.0
    ecef_x_hp: float = 0.0
    ecef_y_hp: float = 0.0
    ecef_z_hp: float = 0.0
    mean_accuracy: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class BaseReference:
    """Last applied or requested base reference configuration."""

    mode: str = ""
    source: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    height_ellipsoid: Optional[float] = None
    ecef_x: Optional[float] = None
    ecef_y: Optional[float] = None
    ecef_z: Optional[float] = None
    fixed_pos_acc: Optional[float] = None
    rtcm_enabled: Optional[bool] = None
    save_to_flash: Optional[bool] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RTCMStatus:
    """RTCM message status."""

    enabled: bool = False
    msm_type: str = ""  # MSM4 or MSM7
    message_counts: dict[str, int] = field(default_factory=dict)
    data_rate: float = 0.0  # bytes per second
    last_message_time: Optional[datetime] = None
    total_messages_sent: int = 0


@dataclass
class NTRIPStatus:
    """NTRIP connection status."""

    enabled: bool = False
    connected: bool = False
    host: str = ""
    port: int = 0
    mountpoint: str = ""
    bytes_sent: int = 0
    bytes_received: int = 0
    uptime: float = 0.0  # seconds
    connection_time: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class ReceiverStatus:
    """Overall receiver status."""

    connected: bool = False
    serial_port: str = ""
    baudrate: int = 0
    firmware_version: Optional[str] = None
    hardware_version: Optional[str] = None
    last_message_time: Optional[datetime] = None
    error_count: int = 0
    nak_count: int = 0
    ack_count: int = 0


class GNSSState:
    """
    Thread-safe GNSS state manager.

    Provides atomic access to all GNSS-related state data with
    read/write locking for concurrent access from multiple threads.
    """

    def __init__(self):
        """Initialize GNSS state with default values."""
        self._lock = threading.RLock()
        self._position = PositionFix()
        self._survey = SurveyStatus()
        self._base_reference = BaseReference()
        self._rtcm = RTCMStatus()
        self._ntrip = NTRIPStatus()
        self._receiver = ReceiverStatus()
        self._raw_messages: list[dict[str, Any]] = []
        self._max_raw_messages = 100

    @property
    def position(self) -> PositionFix:
        """Get current position fix (thread-safe read)."""
        with self._lock:
            return self._position

    @position.setter
    def position(self, value: PositionFix) -> None:
        """Set position fix (thread-safe write)."""
        with self._lock:
            self._position = value

    @property
    def survey(self) -> SurveyStatus:
        """Get survey status (thread-safe read)."""
        with self._lock:
            return self._survey

    @survey.setter
    def survey(self, value: SurveyStatus) -> None:
        """Set survey status (thread-safe write)."""
        with self._lock:
            self._survey = value

    @property
    def rtcm(self) -> RTCMStatus:
        """Get RTCM status (thread-safe read)."""
        with self._lock:
            return self._rtcm

    @rtcm.setter
    def rtcm(self, value: RTCMStatus) -> None:
        """Set RTCM status (thread-safe write)."""
        with self._lock:
            self._rtcm = value

    @property
    def base_reference(self) -> BaseReference:
        """Get last applied base reference (thread-safe read)."""
        with self._lock:
            return self._base_reference

    @base_reference.setter
    def base_reference(self, value: BaseReference) -> None:
        """Set last applied base reference (thread-safe write)."""
        with self._lock:
            self._base_reference = value

    @property
    def ntrip(self) -> NTRIPStatus:
        """Get NTRIP status (thread-safe read)."""
        with self._lock:
            return self._ntrip

    @ntrip.setter
    def ntrip(self, value: NTRIPStatus) -> None:
        """Set NTRIP status (thread-safe write)."""
        with self._lock:
            self._ntrip = value

    @property
    def receiver(self) -> ReceiverStatus:
        """Get receiver status (thread-safe read)."""
        with self._lock:
            return self._receiver

    @receiver.setter
    def receiver(self, value: ReceiverStatus) -> None:
        """Set receiver status (thread-safe write)."""
        with self._lock:
            self._receiver = value

    def update_position(self, **kwargs: Any) -> None:
        """
        Update position fix with new values.

        Args:
            **kwargs: Position fields to update (latitude, longitude, etc.)
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._position, key):
                    setattr(self._position, key, value)
            self._position.timestamp = datetime.utcnow()

    def update_survey(self, **kwargs: Any) -> None:
        """
        Update survey status with new values.

        Args:
            **kwargs: Survey fields to update
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._survey, key):
                    setattr(self._survey, key, value)
            self._survey.timestamp = datetime.utcnow()

    def update_base_reference(self, **kwargs: Any) -> None:
        """
        Update last applied base reference details.

        Args:
            **kwargs: Base reference fields to update
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._base_reference, key):
                    setattr(self._base_reference, key, value)
            self._base_reference.timestamp = datetime.utcnow()

    def add_raw_message(self, message: dict[str, Any]) -> None:
        """
        Add a raw UBX message to the buffer.

        Args:
            message: Parsed UBX message as dictionary
        """
        with self._lock:
            self._raw_messages.append(message)
            # Trim buffer if too large
            if len(self._raw_messages) > self._max_raw_messages:
                self._raw_messages = self._raw_messages[-self._max_raw_messages:]

    def get_raw_messages(self, count: int = 10) -> list[dict[str, Any]]:
        """
        Get the most recent raw messages.

        Args:
            count: Number of messages to return

        Returns:
            List of raw message dictionaries
        """
        with self._lock:
            return self._raw_messages[-count:]

    def clear_raw_messages(self) -> None:
        """Clear the raw message buffer."""
        with self._lock:
            self._raw_messages.clear()

    def increment_error_count(self) -> None:
        """Increment the receiver error count."""
        with self._lock:
            self._receiver.error_count += 1

    def increment_ack_count(self) -> None:
        """Increment the ACK count."""
        with self._lock:
            self._receiver.ack_count += 1

    def increment_nak_count(self) -> None:
        """Increment the NAK count."""
        with self._lock:
            self._receiver.nak_count += 1

    def update_rtcm_status(self, enabled: bool, msm_type: str = "") -> None:
        """Update RTCM enabled flag and MSM type (thread-safe)."""
        with self._lock:
            self._rtcm.enabled = enabled
            if msm_type:
                self._rtcm.msm_type = msm_type

    def increment_rtcm_message_type(self, msg_type: int) -> None:
        """Increment per-message-type counter and update last_message_time (thread-safe)."""
        key = str(msg_type)
        with self._lock:
            self._rtcm.message_counts[key] = self._rtcm.message_counts.get(key, 0) + 1
            self._rtcm.last_message_time = datetime.utcnow()

    def update_ntrip_status(self, **kwargs):
        """Update NTRIP status fields (thread-safe)."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._ntrip, key):
                    setattr(self._ntrip, key, value)

    def to_dict(self) -> dict[str, Any]:
        """
        Export all state to a dictionary.

        Returns:
            Dictionary containing all state data
        """
        with self._lock:
            return {
                "position": {
                    "latitude": self._position.latitude,
                    "longitude": self._position.longitude,
                    "altitude": self._position.altitude,
                    "accuracy": self._position.accuracy,
                    "vertical_accuracy": self._position.vertical_accuracy,
                    "fix_type": self._position.fix_type,
                    "fix_type_str": _FIX_TYPE_STR.get(self._position.fix_type, "unknown"),
                    "num_satellites": self._position.num_satellites,
                    "hdop": self._position.hdop,
                    "vdop": self._position.vdop,
                    "pdop": self._position.pdop,
                    "velocity_north": self._position.velocity_north,
                    "velocity_east": self._position.velocity_east,
                    "velocity_down": self._position.velocity_down,
                    "ground_speed": self._position.ground_speed,
                    "heading": self._position.heading,
                    "timestamp": self._position.timestamp.isoformat(),
                },
                "survey": {
                    "active": self._survey.active,
                    "valid": self._survey.valid,
                    "in_progress": self._survey.in_progress,
                    "progress": self._survey.progress,
                    "accuracy": self._survey.accuracy,
                    "observation_time": self._survey.observation_time,
                    "mean_accuracy": self._survey.mean_accuracy,
                    "ecef_x": self._survey.ecef_x,
                    "ecef_y": self._survey.ecef_y,
                    "ecef_z": self._survey.ecef_z,
                    "timestamp": self._survey.timestamp.isoformat(),
                },
                "base_reference": {
                    "mode": self._base_reference.mode,
                    "source": self._base_reference.source,
                    "latitude": self._base_reference.latitude,
                    "longitude": self._base_reference.longitude,
                    "height_ellipsoid": self._base_reference.height_ellipsoid,
                    "ecef_x": self._base_reference.ecef_x,
                    "ecef_y": self._base_reference.ecef_y,
                    "ecef_z": self._base_reference.ecef_z,
                    "fixed_pos_acc": self._base_reference.fixed_pos_acc,
                    "rtcm_enabled": self._base_reference.rtcm_enabled,
                    "save_to_flash": self._base_reference.save_to_flash,
                    "timestamp": self._base_reference.timestamp.isoformat(),
                },
                "rtcm": {
                    "enabled": self._rtcm.enabled,
                    "msm_type": self._rtcm.msm_type,
                    "message_counts": dict(self._rtcm.message_counts),
                    "data_rate": self._rtcm.data_rate,
                    "total_messages_sent": self._rtcm.total_messages_sent,
                    "last_message_time": (
                        self._rtcm.last_message_time.isoformat()
                        if self._rtcm.last_message_time else None
                    ),
                },
                "ntrip": {
                    "enabled": self._ntrip.enabled,
                    "connected": self._ntrip.connected,
                    "host": self._ntrip.host,
                    "port": self._ntrip.port,
                    "mountpoint": self._ntrip.mountpoint,
                    "bytes_sent": self._ntrip.bytes_sent,
                    "uptime": self._ntrip.uptime,
                    "error_message": self._ntrip.error_message,
                },
                "receiver": {
                    "connected": self._receiver.connected,
                    "serial_port": self._receiver.serial_port,
                    "baudrate": self._receiver.baudrate,
                    "firmware_version": self._receiver.firmware_version,
                    "error_count": self._receiver.error_count,
                    "nak_count": self._receiver.nak_count,
                    "ack_count": self._receiver.ack_count,
                },
            }
