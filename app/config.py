"""
Configuration module for GNSS FastAPI Backend.

Centralized configuration management using environment variables
with sensible defaults for all settings.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


class Config:
    """Application configuration with environment variable support."""

    # FastAPI Server Configuration
    HOST: str = os.getenv("FASTAPI_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("FASTAPI_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"

    # WebSocket Configuration
    WS_ASYNC_MODE: str = "asgi"
    _raw_cors: str = os.getenv("WS_CORS_ORIGINS", "*")
    WS_CORS_ORIGINS: list[str] | str = (
        "*" if _raw_cors.strip() == "*"
        else [o.strip() for o in _raw_cors.split(",")]
    )
    WS_PATH: str = "/ws"

    # Serial Port Configuration
    SERIAL_PORT: str = os.getenv("SERIAL_PORT", "/dev/ttyAMA0")
    SERIAL_BAUDRATE: int = int(os.getenv("SERIAL_BAUDRATE", "38400"))
    SERIAL_TIMEOUT: float = float(os.getenv("SERIAL_TIMEOUT", "1.0"))
    SERIAL_RECONNECT_DELAY: int = int(os.getenv("SERIAL_RECONNECT_DELAY", "5"))
    SERIAL_MAX_RECONNECT_ATTEMPTS: int = int(
        os.getenv("SERIAL_MAX_RECONNECT_ATTEMPTS", "10")
    )

    # UBX Reader Configuration
    UBX_POLL_INTERVAL: float = float(os.getenv("UBX_POLL_INTERVAL", "0.1"))
    UBX_QUEUE_MAXSIZE: int = int(os.getenv("UBX_QUEUE_MAXSIZE", "5000"))

    # GNSS Configuration
    # Survey-in accuracy threshold in meters (default: 0.1m = 10cm)
    SURVEY_ACCURACY_THRESHOLD: float = float(
        os.getenv("SURVEY_ACCURACY_THRESHOLD", "0.1")
    )
    # Survey-in minimum duration in seconds (default: 10s)
    SURVEY_MIN_DURATION: int = int(os.getenv("SURVEY_MIN_DURATION", "10"))

    # RTCM Configuration
    RTCM_MSM_TYPE: str = os.getenv("RTCM_MSM_TYPE", "MSM4")  # MSM4 or MSM7
    RTCM_MESSAGE_INTERVAL: int = int(os.getenv("RTCM_MESSAGE_INTERVAL", "1000"))  # ms

    # NTRIP Configuration
    NTRIP_ENABLED: bool = os.getenv("NTRIP_ENABLED", "False").lower() == "true"
    NTRIP_HOST: str = os.getenv("NTRIP_HOST", "")
    NTRIP_PORT: int = int(os.getenv("NTRIP_PORT", "2101"))
    NTRIP_MOUNTPOINT: str = os.getenv("NTRIP_MOUNTPOINT", "")
    NTRIP_USERNAME: str = os.getenv("NTRIP_USERNAME", "")
    NTRIP_PASSWORD: str = os.getenv("NTRIP_PASSWORD", "")

    # LoRa Configuration
    LORA_ENABLED: bool = os.getenv("LORA_ENABLED", "False").lower() == "true"
    LORA_PORT: str = os.getenv("LORA_PORT", "/dev/ttyUSB0")
    LORA_BAUDRATE: int = int(os.getenv("LORA_BAUDRATE", "115200"))
    LORA_PACKET_SIZE: int = int(os.getenv("LORA_PACKET_SIZE", "240"))
    LORA_WRITE_TIMEOUT: float = float(os.getenv("LORA_WRITE_TIMEOUT", "2.0"))

    # Logging Configuration
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = (
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    LOG_FILE: Optional[str] = os.getenv("LOG_FILE", None)

    # Application paths
    BASE_DIR: Path = Path(__file__).parent.parent
    DATA_DIR: Path = BASE_DIR / "data"

    @classmethod
    def create_data_dir(cls) -> None:
        """Create data directory if it doesn't exist."""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load_autoflow_config(cls) -> dict:
        """Load autoflow config from data/autoflow_config.json."""
        config_file = cls.DATA_DIR / "autoflow_config.json"
        try:
            if config_file.exists():
                import json
                with open(config_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load autoflow config: {e}")
        return {}

    @classmethod
    def apply_autoflow_config(cls) -> None:
        """Apply autoflow config values to Config with validation."""
        autoflow_config = cls.load_autoflow_config()
        if not autoflow_config:
            return

        errors = []

        # Survey min duration — must be positive integer
        if "min_duration_sec" in autoflow_config:
            val = autoflow_config["min_duration_sec"]
            try:
                val = int(val)
                if val <= 0:
                    raise ValueError("must be > 0")
                cls.SURVEY_MIN_DURATION = val
            except (ValueError, TypeError) as e:
                errors.append(f"min_duration_sec={val!r} invalid ({e}) — keeping default {cls.SURVEY_MIN_DURATION}s")

        # Survey accuracy limit — must be positive float
        if "accuracy_limit_m" in autoflow_config:
            val = autoflow_config["accuracy_limit_m"]
            try:
                val = float(val)
                if val <= 0:
                    raise ValueError("must be > 0")
                cls.SURVEY_ACCURACY_THRESHOLD = val
            except (ValueError, TypeError) as e:
                errors.append(f"accuracy_limit_m={val!r} invalid ({e}) — keeping default {cls.SURVEY_ACCURACY_THRESHOLD}m")

        # RTCM MSM type — must be MSM4 or MSM7
        if "msm_type" in autoflow_config:
            val = str(autoflow_config["msm_type"]).upper()
            if val in ("MSM4", "MSM7"):
                cls.RTCM_MSM_TYPE = val
            else:
                errors.append(f"msm_type={val!r} invalid — must be MSM4 or MSM7 — keeping default {cls.RTCM_MSM_TYPE}")

        # NTRIP host — string, no validation beyond type
        if "ntrip_host" in autoflow_config and autoflow_config["ntrip_host"]:
            cls.NTRIP_HOST = str(autoflow_config["ntrip_host"])
            cls.NTRIP_ENABLED = True

        # NTRIP port — must be 1-65535
        if "ntrip_port" in autoflow_config:
            val = autoflow_config["ntrip_port"]
            try:
                val = int(val)
                if not (1 <= val <= 65535):
                    raise ValueError("must be 1-65535")
                cls.NTRIP_PORT = val
            except (ValueError, TypeError) as e:
                errors.append(f"ntrip_port={val!r} invalid ({e}) — keeping default {cls.NTRIP_PORT}")

        # NTRIP mountpoint and username — plain strings
        if "ntrip_mountpoint" in autoflow_config and autoflow_config["ntrip_mountpoint"]:
            cls.NTRIP_MOUNTPOINT = str(autoflow_config["ntrip_mountpoint"])
        if "ntrip_username" in autoflow_config:
            cls.NTRIP_USERNAME = str(autoflow_config["ntrip_username"])

        # NTRIP password — never load from JSON (Task 2), skip silently
        # Password is loaded from NTRIP_PASSWORD env var only

        # Log summary
        if errors:
            for err in errors:
                logger.warning(f"[CONFIG] Validation warning: {err}")
        logger.info(
            f"[CONFIG] Applied autoflow config: "
            f"survey_dur={cls.SURVEY_MIN_DURATION}s  "
            f"survey_acc={cls.SURVEY_ACCURACY_THRESHOLD}m  "
            f"rtcm={cls.RTCM_MSM_TYPE}  "
            f"ntrip_enabled={cls.NTRIP_ENABLED}  "
            f"ntrip_host={cls.NTRIP_HOST or '(not set)'}"
        )


# Create data directory on module load
Config.create_data_dir()

# Apply autoflow config after data directory is created
Config.apply_autoflow_config()
