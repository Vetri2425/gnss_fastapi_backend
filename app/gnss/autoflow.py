"""
AutoFlow Orchestrator.

Full state-machine that drives the GNSS base-station workflow:

  IDLE
    └─► WAITING_SERIAL  (waiting for /dev/ttyAMA0 connection)
          └─► SURVEY        (survey-in running, polls NAV-SVIN every 5 s)
                └─► ENABLING_RTCM   (sends CFG-VALSET RTCM enable)
                      └─► NTRIP_CONNECT  (if host configured)
                            └─► STREAMING  (pushing RTCM to caster)
                      └─► STREAMING  (if no NTRIP — RTCM active on serial)
  FAILED  (any unrecoverable error)

Threading model
───────────────
Runs in a daemon thread (not asyncio task) so blocking sleeps / polls
never touch the event loop. Socket.IO events are emitted back to
connected clients via asyncio.run_coroutine_threadsafe(sio.emit(), loop).

Thread-safety
─────────────
All public methods are safe to call from FastAPI async route handlers:
  - trigger_run()  — sets threading.Event (instant, lock-free)
  - abort()        — sets threading.Event + lock-protected state reset
  - save_config()  — saves JSON + conditionally triggers/aborts
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import socketio

from app.config import Config
from app.gnss.commands import GNSSCommands
from app.gnss.geodesy import ecef_distance

if TYPE_CHECKING:
    from app.gnss.lora_push import LoRaPushClient
    from app.gnss.ntrip_push import NTRIPPushClient
    from app.gnss.reader import GNSSReader
    from app.gnss.state import GNSSState

logger = logging.getLogger(__name__)

_CONFIG_FILE: Path = Config.DATA_DIR / "autoflow_config.json"
_BASE_POSITION_FILE: Path = Config.DATA_DIR / "base_position.json"
_LOCATION_CHANGE_THRESHOLD_M: float = 10000.0  # metres
_AWAITING_CONFIRM_TIMEOUT_S: float = 300.0   # 5 minutes


# ── States ────────────────────────────────────────────────────────────────────

class AutoflowState(str, Enum):
    IDLE              = "IDLE"
    WAITING_SERIAL    = "WAITING_SERIAL"
    CHECKING_POSITION = "CHECKING_POSITION"
    AWAITING_CONFIRM  = "AWAITING_CONFIRM"
    APPLY_FIXED_BASE  = "APPLY_FIXED_BASE"
    SURVEY            = "SURVEY"
    ENABLING_RTCM     = "ENABLING_RTCM"
    NTRIP_CONNECT     = "NTRIP_CONNECT"
    STREAMING         = "STREAMING"
    FAILED            = "FAILED"


# ── Config dataclass ──────────────────────────────────────────────────────────

@dataclass
class AutoflowConfig:
    enabled: bool         = True
    min_duration_sec: int = 10
    accuracy_limit_m: float = 2.0
    msm_type: str         = "MSM7"
    ntrip_host: str       = ""
    ntrip_port: int       = 2101
    ntrip_mountpoint: str = ""
    ntrip_username: str   = ""
    ntrip_password: str   = ""
    ntrip_version: int    = 1

    def to_dict(self) -> dict:
        """Safe dict — password masked."""
        d = asdict(self)
        d["ntrip_password"] = "***" if self.ntrip_password else ""
        return d

    def to_dict_full(self) -> dict:
        """Full dict including password — for JSON persistence only."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AutoflowConfig":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class AutoflowOrchestrator:
    """
    Background-thread autoflow orchestrator.
    Public API is safe to call from async FastAPI route handlers.
    """

    def __init__(
        self,
        gnss_state: "GNSSState",
        gnss_reader: "GNSSReader",
        sio: socketio.AsyncServer,
        loop: asyncio.AbstractEventLoop,
    ):
        self.gnss_state = gnss_state
        self.gnss_reader = gnss_reader
        self.sio = sio
        self.loop = loop

        self._config = AutoflowConfig()
        self._load_config()

        # Mutable state — all access under self._lock
        self._lock = threading.Lock()
        self._state: AutoflowState = AutoflowState.IDLE
        self._last_error: Optional[str] = None
        self._ntrip_client: Optional["NTRIPPushClient"] = None
        self._lora_client: Optional["LoRaPushClient"] = None
        self._survey_start_ts: Optional[float] = None

        # Survey polling state
        self._last_obs_time: int = 0
        self._stuck_count: int = 0
        self._poll_busy: bool = False
        self._stuck_retries: int = 0

        # Thread control (threading.Event — safe from any thread)
        self._stop_event    = threading.Event()
        self._trigger_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Resurvey confirmation events
        self._confirm_resurvey: threading.Event = threading.Event()
        self._skip_resurvey: threading.Event = threading.Event()
        
        # Track which stage failed for smart auto-recovery
        self._failed_from_state: Optional[AutoflowState] = None
        
        # Location change state for plain WebSocket updates
        self._location_change_distance: Optional[float] = None
        self._location_change_deadline: Optional[float] = None

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start orchestrator thread and boot-check config (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="AutoflowOrchestrator",
            daemon=True,
        )
        self._thread.start()
        logger.info("[BOOT] AutoFlow orchestrator started")

        if self._config.enabled:
            logger.info(f"[BOOT] AutoFlow enabled — auto-trigger on startup")
            logger.info(f"[BOOT]   survey: dur={self._config.min_duration_sec}s  acc={self._config.accuracy_limit_m}m  rtcm={self._config.msm_type}")
            if self._config.ntrip_host:
                logger.info(f"[BOOT]   ntrip:  {self._config.ntrip_host}:{self._config.ntrip_port}/{self._config.ntrip_mountpoint}  user={self._config.ntrip_username or '(none)'}")
            else:
                logger.info("[BOOT]   ntrip:  not configured — RTCM will stream on serial ports only")
            self.trigger_run()
        else:
            logger.info("[BOOT] AutoFlow disabled — waiting for manual trigger or config enable")

    def stop(self, timeout: float = 10.0) -> None:
        """Shutdown: signal stop, join thread, cleanup NTRIP and LoRa."""
        self._stop_event.set()
        self._trigger_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._cleanup_ntrip()
        self._cleanup_lora()

    def trigger_run(self) -> None:
        """
        Trigger an autoflow run. Thread-safe — just sets a threading.Event.
        Ignored if a run is already in progress.
        """
        with self._lock:
            if self._state in (
                AutoflowState.SURVEY,
                AutoflowState.ENABLING_RTCM,
                AutoflowState.NTRIP_CONNECT,
                AutoflowState.STREAMING,
                AutoflowState.AWAITING_CONFIRM,
            ):
                logger.warning(f"[AUTOFLOW] Trigger ignored — already in {self._state.value}")
                return
            self._state = AutoflowState.WAITING_SERIAL
            self._last_error = None
        self._trigger_event.set()
        logger.info("[AUTOFLOW] Run triggered")

    def abort(self) -> None:
        """Abort current run and return to IDLE. Thread-safe."""
        with self._lock:
            self._state = AutoflowState.IDLE
            self._last_error = "Aborted by user"
        self._cleanup_ntrip()
        # Stop RTCM output and TMODE on the receiver itself
        if self.gnss_reader.is_connected:
            self.gnss_reader.send_command(GNSSCommands.create_rtcm_disable_command())
            self.gnss_reader.send_command(GNSSCommands.create_survey_stop_command())
            logger.info("[AUTOFLOW] Sent RTCM disable + TMODE stop to receiver")
        self._trigger_event.set()
        self._emit("autoflow_state", self._status_dict())
        logger.info("[AUTOFLOW] Aborted — IDLE")

    def save_config(self, cfg: AutoflowConfig) -> None:
        """Persist config and start/stop flow based on enabled flag."""
        was_enabled = self._config.enabled
        self._config = cfg
        self._save_config()
        logger.info(
            f"[AUTOFLOW] Config saved: enabled={cfg.enabled}  msm={cfg.msm_type}  "
            f"dur={cfg.min_duration_sec}s  acc={cfg.accuracy_limit_m}m"
        )
        if cfg.ntrip_host:
            logger.info(
                f"[AUTOFLOW]   NTRIP: {cfg.ntrip_host}:{cfg.ntrip_port}/{cfg.ntrip_mountpoint}  "
                f"user={cfg.ntrip_username or '(none)'}"
            )
        if cfg.enabled and not was_enabled:
            self.trigger_run()
        elif not cfg.enabled and was_enabled:
            self.abort()

    @property
    def state(self) -> AutoflowState:
        with self._lock:
            return self._state

    def get_status(self) -> dict:
        return self._status_dict()

    def get_config_copy(self) -> AutoflowConfig:
        """Return a copy of the current config, including the real password."""
        with self._lock:
            return deepcopy(self._config)

    def confirm_resurvey(self) -> None:
        """Called from API when user confirms resurvey. Thread-safe."""
        self._confirm_resurvey.set()
        logger.info("[AUTOFLOW] User confirmed resurvey")

    def skip_resurvey(self) -> None:
        """Called from API when user says same site. Thread-safe."""
        self._skip_resurvey.set()
        logger.info("[AUTOFLOW] User skipped resurvey — using saved position")

    # ── Base position helpers ─────────────────────────────────────────────

    def _load_base_position(self) -> dict | None:
        """Load saved base position from data/base_position.json."""
        try:
            if _BASE_POSITION_FILE.exists():
                data = json.loads(_BASE_POSITION_FILE.read_text())
                if all(k in data for k in ("ecef_x", "ecef_y", "ecef_z")):
                    return data
        except Exception as e:
            logger.warning(f"[AUTOFLOW] Failed to load base_position.json: {e}")
        return None

    def _save_base_position(self, ecef_x: float, ecef_y: float,
                            ecef_z: float, accuracy: float) -> None:
        """Save surveyed ECEF to data/base_position.json."""
        try:
            from datetime import datetime, timezone
            data = {
                "ecef_x": ecef_x,
                "ecef_y": ecef_y,
                "ecef_z": ecef_z,
                "accuracy": accuracy,
                "surveyed_at": datetime.now(timezone.utc).isoformat(),
            }
            _BASE_POSITION_FILE.write_text(json.dumps(data, indent=2))
            logger.info(
                f"[AUTOFLOW] Base position saved: "
                f"X={ecef_x:.3f} Y={ecef_y:.3f} Z={ecef_z:.3f} "
                f"acc={accuracy:.3f}m"
            )
        except Exception as e:
            logger.error(f"[AUTOFLOW] Failed to save base_position.json: {e}")

    # ── Background thread ─────────────────────────────────────────────────

    def _run_loop(self) -> None:
        logger.info("[AUTOFLOW] Run loop started")
        while not self._stop_event.is_set():
            triggered = self._trigger_event.wait(timeout=1.0)
            if self._stop_event.is_set():
                break
            if not triggered:
                continue
            self._trigger_event.clear()

            with self._lock:
                current = self._state

            if current == AutoflowState.IDLE:
                continue

            try:
                self._execute()
            except Exception as e:
                logger.exception(f"[AUTOFLOW] Unhandled error: {e}")
                with self._lock:
                    self._failed_from_state = self._state
                    self._state = AutoflowState.FAILED
                    self._last_error = str(e)
                self._cleanup_ntrip()
                self._emit("autoflow_state", self._status_dict())
                self._emit("autoflow_error", {"error": str(e)})
                
                # Auto-recovery — wait then retry
                recovery_delay = 120.0  # 2 minutes
                logger.warning(
                    f"[AUTOFLOW] FAILED from {self._failed_from_state.value if self._failed_from_state else 'unknown'}. "
                    f"Auto-recovering in {int(recovery_delay)}s. "
                    f"Error: {e}"
                )
                
                # Wait in chunks so stop() exits cleanly
                waited = 0
                while waited < recovery_delay:
                    if self._stop_event.is_set():
                        return
                    self._stop_event.wait(timeout=5.0)
                    waited += 5
                
                if self._stop_event.is_set():
                    return
                
                # Check if we have a saved base position
                saved = self._load_base_position()
                has_saved_position = saved is not None
                
                logger.info(
                    f"[AUTOFLOW] Auto-recovery starting. "
                    f"saved_position={has_saved_position}  "
                    f"failed_from={self._failed_from_state.value if self._failed_from_state else 'unknown'}"
                )
                
                # Reset state and re-trigger
                with self._lock:
                    self._state = AutoflowState.IDLE
                    self._last_error = None
                self.trigger_run()
                # Loop continues — trigger_run() sets WAITING_SERIAL
                # _execute() will use saved position if available

        logger.info("[AUTOFLOW] Run loop exited")

    def start_ntrip_direct(
        self,
        host: str,
        port: int,
        mountpoint: str,
        password: str,
        username: str = "",
        ntrip_version: int = 1,
    ) -> None:
        """Start NTRIP push client directly (bypasses autoflow state machine)."""
        from app.gnss.ntrip_push import NTRIPPushClient
        self._cleanup_ntrip()

        client = NTRIPPushClient(
            host=host,
            port=port,
            mountpoint=mountpoint,
            password=password,
            username=username or "",
            ntrip_version=ntrip_version,
            max_retries=10,
        )

        def _on_connected():
            self.gnss_state.update_ntrip_status(
                enabled=True, connected=True,
                host=host, port=port, mountpoint=mountpoint,
            )
            self._emit("ntrip_status", client.get_status())

        def _on_disconnected():
            self.gnss_state.update_ntrip_status(connected=False)
            self._emit("ntrip_status", client.get_status())

        client.set_connected_callback(_on_connected)
        client.set_disconnected_callback(_on_disconnected)

        self.gnss_state.update_ntrip_status(
            enabled=True, connected=False,
            host=host, port=port, mountpoint=mountpoint,
        )

        with self._lock:
            self._ntrip_client = client
        self.gnss_reader.set_rtcm_callback("ntrip", client.put_rtcm)
        client.start()
        logger.info(f"[NTRIP] Direct start -> {host}:{port}/{mountpoint}")

    def stop_ntrip_direct(self) -> None:
        """Stop NTRIP push client directly (bypasses autoflow state machine)."""
        self._cleanup_ntrip()
        self.gnss_state.update_ntrip_status(enabled=False, connected=False)
        logger.info("[NTRIP] Direct stop")

    # ── LoRa Public API ───────────────────────────────────────────────────

    def start_lora(self) -> bool:
        """
        Start LoRa streaming. Thread-safe.
        Returns True if started, False if already running.
        """
        with self._lock:
            if self._lora_client and self._lora_client._connected:
                logger.warning("[AUTOFLOW] LoRa already streaming")
                return False
        self._start_lora()
        return True

    def stop_lora(self) -> None:
        """Stop LoRa streaming. Thread-safe."""
        self._cleanup_lora()
        logger.info("[AUTOFLOW] LoRa streaming stopped")

    def get_lora_status(self) -> dict:
        """Return LoRa client status or disabled status."""
        with self._lock:
            client = self._lora_client
        if client:
            return client.get_status()
        return {
            "enabled": False,
            "connected": False,
            "port": Config.LORA_PORT,
            "baudrate": Config.LORA_BAUDRATE,
            "bytes_sent": 0,
            "frames_sent": 0,
            "data_rate_bps": 0,
            "uptime": 0,
        }

    def _execute(self) -> None:
        """Full autoflow sequence — runs blocking in the orchestrator thread."""

        cfg = self._config   # snapshot for this run

        # ── Phase 1: Wait for serial connection ──────────────────────────
        self._set_state(AutoflowState.WAITING_SERIAL)
        logger.info("[AUTOFLOW] Phase 1: Waiting for serial connection...")
        while not self._halted():
            if self.gnss_reader.is_connected:
                break
            self._stop_event.wait(timeout=2.0)
        if self._halted():
            return
        logger.info("[AUTOFLOW] Serial connected")

        # ── Phase 1b: Stop any running RTCM / TMODE before fresh start ──────
        logger.info("[AUTOFLOW] Stopping any active RTCM and TMODE on receiver...")
        disable_ack = self.gnss_reader.send_command_and_wait_ack(
            GNSSCommands.create_rtcm_disable_command(),
            timeout=8.0,
        )
        stop_ack = self.gnss_reader.send_command_and_wait_ack(
            GNSSCommands.create_survey_stop_command(),
            timeout=8.0,
        )
        logger.info(f"[AUTOFLOW] Phase 1b ACKs: rtcm_disable={disable_ack}  survey_stop={stop_ack}")
        self._stop_event.wait(timeout=1.0)
        if self._halted():
            return
        # Clear stale survey state so the poll loop cannot exit on a previous valid=True
        self.gnss_state.update_survey(
            active=False, valid=False, in_progress=False,
            progress=0, accuracy=0.0, observation_time=0, mean_accuracy=0.0,
        )
        logger.info("[AUTOFLOW] Receiver reset — starting fresh survey")

        # ── Phase 1c: Check saved base position ──────────────────────────
        self._set_state(AutoflowState.CHECKING_POSITION)
        self._emit("autoflow_state", self._status_dict())

        saved = self._load_base_position()
        use_saved = False

        if saved:
            # Wait for a 3D fix to compare position (max 60s)
            logger.info("[AUTOFLOW] Saved base position found — waiting for 3D fix to verify location")
            fix_deadline = time.monotonic() + 60.0
            got_fix = False
            while not self._halted() and time.monotonic() < fix_deadline:
                pos = self.gnss_state.position
                if pos.fix_type == 3:  # 3D fix
                    got_fix = True
                    break
                self._stop_event.wait(timeout=2.0)

            if got_fix and not self._halted():
                pos = self.gnss_state.position
                # Convert current LLH position to ECEF for comparison
                import math
                lat_r = math.radians(pos.latitude)
                lon_r = math.radians(pos.longitude)
                a = 6378137.0
                f = 1 / 298.257223563
                e2 = 2 * f - f * f
                N = a / math.sqrt(1 - e2 * math.sin(lat_r) ** 2)
                cur_x = (N + pos.altitude) * math.cos(lat_r) * math.cos(lon_r)
                cur_y = (N + pos.altitude) * math.cos(lat_r) * math.sin(lon_r)
                cur_z = (N * (1 - e2) + pos.altitude) * math.sin(lat_r)

                dist = ecef_distance(
                    cur_x, cur_y, cur_z,
                    saved["ecef_x"], saved["ecef_y"], saved["ecef_z"]
                )
                logger.info(f"[AUTOFLOW] Distance from saved position: {dist:.1f}m (threshold: {_LOCATION_CHANGE_THRESHOLD_M}m)")

                if dist < _LOCATION_CHANGE_THRESHOLD_M:
                    logger.info("[AUTOFLOW] Same site detected — using saved base position")
                    use_saved = True
                else:
                    # Location changed — notify frontend, wait for confirm
                    logger.warning(f"[AUTOFLOW] Location changed by {dist:.1f}m — notifying frontend")
                    self._set_state(AutoflowState.AWAITING_CONFIRM)
                    with self._lock:
                        self._location_change_distance = dist
                        self._location_change_deadline = time.monotonic() + _AWAITING_CONFIRM_TIMEOUT_S
                    self._confirm_resurvey.clear()
                    self._skip_resurvey.clear()
                    self._emit("location_changed", {
                        "distance_metres": round(dist, 1),
                        "saved_position": saved,
                        "current_ecef": {
                            "x": round(cur_x, 3),
                            "y": round(cur_y, 3),
                            "z": round(cur_z, 3),
                        },
                        "auto_resurvey_in_seconds": int(_AWAITING_CONFIRM_TIMEOUT_S),
                    })

                    # Wait for user or timeout
                    deadline = time.monotonic() + _AWAITING_CONFIRM_TIMEOUT_S
                    while not self._halted() and time.monotonic() < deadline:
                        if self._confirm_resurvey.is_set():
                            logger.info("[AUTOFLOW] User confirmed resurvey")
                            break
                        if self._skip_resurvey.is_set():
                            logger.info("[AUTOFLOW] User skipped resurvey — using saved position")
                            use_saved = True
                            break
                        self._stop_event.wait(timeout=5.0)
                    else:
                        if not self._halted():
                            logger.warning("[AUTOFLOW] 15min timeout — auto-resurveying")
                    
                    # Clear location change data when exiting AWAITING_CONFIRM
                    with self._lock:
                        self._location_change_distance = None
                        self._location_change_deadline = None
            else:
                logger.warning("[AUTOFLOW] No 3D fix in 60s — proceeding to survey")

        # ── Apply saved position or proceed to survey ─────────────────────
        if use_saved and not self._halted():
            logger.info("[AUTOFLOW] Applying saved base position directly")
            self._set_state(AutoflowState.APPLY_FIXED_BASE)
            fixed_cmd = GNSSCommands.create_fixed_mode_command(
                ecef_x=saved["ecef_x"],
                ecef_y=saved["ecef_y"],
                ecef_z=saved["ecef_z"],
            )
            fixed_ack = self.gnss_reader.send_command_and_wait_ack(fixed_cmd, timeout=8.0)
            if fixed_ack is not True:
                logger.error("[AUTOFLOW] Fixed base from saved position failed — falling back to survey")
                use_saved = False
            else:
                logger.info(
                    f"[AUTOFLOW] Fixed base applied from saved position: "
                    f"X={saved['ecef_x']:.3f} Y={saved['ecef_y']:.3f} Z={saved['ecef_z']:.3f}"
                )
                self.gnss_state.update_base_reference(
                    mode="FIXED",
                    source="saved_position",
                    ecef_x=saved["ecef_x"],
                    ecef_y=saved["ecef_y"],
                    ecef_z=saved["ecef_z"],
                    fixed_pos_acc=saved.get("accuracy", 0.10),
                    rtcm_enabled=False,
                    save_to_flash=False,
                )

        if not use_saved and not self._halted():
            # ── Phase 2: Start survey-in ──────────────────────────────────────
            self._set_state(AutoflowState.SURVEY)
            cmd = GNSSCommands.create_survey_start_command(
                min_duration=cfg.min_duration_sec,
                accuracy_limit=cfg.accuracy_limit_m,
            )
            start_ack = self.gnss_reader.send_command_and_wait_ack(cmd, timeout=8.0)
            if start_ack is not True:
                with self._lock:
                    self._failed_from_state = AutoflowState.SURVEY
                    self._state = AutoflowState.FAILED
                    self._last_error = "Survey start was not ACKed by receiver"
                logger.error(f"[AUTOFLOW] Survey start failed: ack={start_ack}")
                self._emit("autoflow_state", self._status_dict())
                return
            self._survey_start_ts = time.time()
            self._last_obs_time = 0
            self._stuck_count = 0
            self._poll_busy = False
            self._stuck_retries = 0
            logger.info(
                f"[AUTOFLOW] Phase 2: Survey-in started  "
                f"min_dur={cfg.min_duration_sec}s  acc_limit={cfg.accuracy_limit_m}m"
            )

            while not self._halted():
                # Prevent overlapping polls
                if self._poll_busy:
                    self._stop_event.wait(timeout=1.0)
                    continue

                # Poll NAV-SVIN
                self._poll_busy = True
                poll_cmd = GNSSCommands.create_nav_svin_poll_command()
                self.gnss_reader.send_command(poll_cmd)

                # Wait for response with 4s timeout
                # Break on EITHER observation_time change OR survey valid=True
                poll_deadline = time.monotonic() + 4.0
                while not self._halted() and time.monotonic() < poll_deadline:
                    survey = self.gnss_state.survey
                    if survey.observation_time != self._last_obs_time:
                        break
                    if survey.valid:
                        logger.info("[POLL] survey.valid=True detected in wait loop — breaking early")
                        break
                    self._stop_event.wait(timeout=0.5)

                self._poll_busy = False

                if self._halted():
                    return

                survey = self.gnss_state.survey
                elapsed = int(time.time() - self._survey_start_ts)

                # Calculate progress based on configured min_duration
                if cfg.min_duration_sec > 0 and survey.observation_time > 0:
                    progress = min(100, int((survey.observation_time / cfg.min_duration_sec) * 100))
                else:
                    progress = 0

                # Give long-running surveys a full 24h window before failing.
                max_survey_duration = 86400
                if elapsed > max_survey_duration:
                    logger.warning(
                        f"[AUTOFLOW] Survey timeout after {elapsed}s (max: {max_survey_duration}s) — "
                        f"acc={survey.mean_accuracy:.3f}m (limit: {cfg.accuracy_limit_m}m)"
                    )
                    with self._lock:
                        self._failed_from_state = AutoflowState.SURVEY
                        self._state = AutoflowState.FAILED
                        self._last_error = f"Survey timeout after {elapsed}s (max: {max_survey_duration}s)"
                    self._emit("autoflow_state", self._status_dict())
                    return

                # Stuck detection
                if survey.observation_time != self._last_obs_time:
                    self._last_obs_time = survey.observation_time
                    self._stuck_count = 0
                else:
                    self._stuck_count += 1

                # Auto-retry after 10 stuck polls
                if self._stuck_count >= 10:
                    logger.warning(f"[AUTOFLOW] Survey stuck after 10 polls — restarting")
                    self._stuck_retries += 1

                    # Stop survey
                    stop_cmd = GNSSCommands.create_survey_stop_command()
                    stop_ack = self.gnss_reader.send_command_and_wait_ack(stop_cmd, timeout=8.0)
                    logger.info(f"[AUTOFLOW] Survey restart stop ACK: {stop_ack}")
                    self._stop_event.wait(timeout=1.0)

                    # Reset stuck counters
                    self._stuck_count = 0
                    self._last_obs_time = 0

                    # Restart survey
                    start_cmd = GNSSCommands.create_survey_start_command(
                        min_duration=cfg.min_duration_sec,
                        accuracy_limit=cfg.accuracy_limit_m,
                    )
                    start_ack = self.gnss_reader.send_command_and_wait_ack(start_cmd, timeout=8.0)
                    if start_ack is not True:
                        with self._lock:
                            self._failed_from_state = AutoflowState.SURVEY
                            self._state = AutoflowState.FAILED
                            self._last_error = "Survey restart was not ACKed by receiver"
                        logger.error(f"[AUTOFLOW] Survey restart failed: ack={start_ack}")
                        self._emit("autoflow_state", self._status_dict())
                        return
                    self._survey_start_ts = time.time()
                    logger.info(f"[AUTOFLOW] Survey restarted (retry #{self._stuck_retries})")

                if survey.valid:
                    logger.info(
                        f"[POLL] Survey VALID!  acc={survey.mean_accuracy:.3f}m  "
                        f"obs={survey.observation_time}s  elapsed={elapsed}s"
                    )
                    logger.info(
                        f"[POLL] ECEF: X={survey.ecef_x:.3f}  Y={survey.ecef_y:.3f}  Z={survey.ecef_z:.3f}"
                    )
                    break

                if survey.active:
                    logger.info(
                        f"[SURVEY] {survey.observation_time:4d}s / {cfg.min_duration_sec}s  "
                        f"acc={survey.mean_accuracy:.3f}m / {cfg.accuracy_limit_m}m  "
                        f"progress={progress:3d}%  elapsed={elapsed}s"
                    )

                self._emit("autoflow_progress", {
                    "observation_time": survey.observation_time,
                    "mean_accuracy": survey.mean_accuracy,
                    "active": survey.active,
                    "valid": survey.valid,
                    "elapsed": elapsed,
                    "progress": progress,
                })
                self._stop_event.wait(timeout=5.0)

            if self._halted():
                return

            survey = self.gnss_state.survey

            # Lock the surveyed coordinates in as an explicit fixed base position
            # before enabling RTCM. This makes the base reference unambiguous for
            # consumers that depend on ARP metadata such as RTCM 1005.
            fixed_cmd = GNSSCommands.create_fixed_mode_command(
                ecef_x=survey.ecef_x,
                ecef_y=survey.ecef_y,
                ecef_z=survey.ecef_z,
            )
            fixed_ack = self.gnss_reader.send_command_and_wait_ack(fixed_cmd, timeout=8.0)
            if fixed_ack is not True:
                with self._lock:
                    self._failed_from_state = AutoflowState.APPLY_FIXED_BASE
                    self._state = AutoflowState.FAILED
                    self._last_error = "Fixed base configuration was not ACKed by receiver"
                logger.error(f"[AUTOFLOW] Fixed base config failed: ack={fixed_ack}")
                self._emit("autoflow_state", self._status_dict())
                return
            logger.info(
                "[AUTOFLOW] Survey coordinates applied as fixed base "
                f"X={survey.ecef_x:.3f} Y={survey.ecef_y:.3f} Z={survey.ecef_z:.3f}"
            )
            self.gnss_state.update_base_reference(
                mode="FIXED",
                source="autoflow_survey",
                ecef_x=survey.ecef_x,
                ecef_y=survey.ecef_y,
                ecef_z=survey.ecef_z,
                fixed_pos_acc=survey.mean_accuracy,
                rtcm_enabled=False,
                save_to_flash=False,
            )

            # Save surveyed position for future boots
            self._save_base_position(
                ecef_x=survey.ecef_x,
                ecef_y=survey.ecef_y,
                ecef_z=survey.ecef_z,
                accuracy=survey.mean_accuracy,
            )

        # ── Phase 3: Enable RTCM ──────────────────────────────────────────
        self._set_state(AutoflowState.ENABLING_RTCM)
        rtcm_cmd = GNSSCommands.create_rtcm_enable_command(cfg.msm_type)
        rtcm_ack = self.gnss_reader.send_command_and_wait_ack(rtcm_cmd, timeout=10.0)
        if rtcm_ack is not True:
            with self._lock:
                self._failed_from_state = AutoflowState.ENABLING_RTCM
                self._state = AutoflowState.FAILED
                self._last_error = "RTCM enable was not ACKed by receiver"
            logger.error(f"[AUTOFLOW] RTCM enable failed: ack={rtcm_ack}")
            self._emit("autoflow_state", self._status_dict())
            return
        logger.info(f"[AUTOFLOW] Phase 3: RTCM {cfg.msm_type} enabled on UART1/UART2/USB")

        # Update RTCM state so frontend shows enabled
        self.gnss_state.update_rtcm_status(enabled=True, msm_type=cfg.msm_type)
        self.gnss_state.update_base_reference(rtcm_enabled=True)

        self._stop_event.wait(timeout=1.0)   # brief settle after RTCM enable
        if self._halted():
            return

        # ── Phase 4: NTRIP push (optional) ───────────────────────────────
        if cfg.ntrip_host and cfg.ntrip_mountpoint:
            self._set_state(AutoflowState.NTRIP_CONNECT)
            logger.info(
                f"[AUTOFLOW] Phase 4: Connecting NTRIP  "
                f"{cfg.ntrip_host}:{cfg.ntrip_port}/{cfg.ntrip_mountpoint}"
            )
            self._start_ntrip(cfg)

            # Allow the initial attempt, backoff, and a retry before timing out.
            deadline = time.time() + 90.0
            while not self._halted():
                with self._lock:
                    connected = self._ntrip_client.connected if self._ntrip_client else False
                if connected:
                    break
                if time.time() > deadline:
                    with self._lock:
                        self._failed_from_state = AutoflowState.NTRIP_CONNECT
                        self._state = AutoflowState.FAILED
                        self._last_error = "NTRIP connection timeout (90 s)"
                    logger.error("[AUTOFLOW] NTRIP connection timed out after 90 s")
                    self._emit("autoflow_state", self._status_dict())
                    return
                self._stop_event.wait(timeout=1.0)

            if self._halted():
                return

            self._set_state(AutoflowState.STREAMING)
            logger.info("[AUTOFLOW] ============================================")
            logger.info("[AUTOFLOW] === Base station FULLY OPERATIONAL ===")
            logger.info(
                f"[AUTOFLOW]   RTCM {cfg.msm_type} streaming → "
                f"{cfg.ntrip_host}:{cfg.ntrip_port}/{cfg.ntrip_mountpoint}"
            )
            logger.info("[AUTOFLOW] ============================================")

            # Monitor streaming — stay in this loop while active
            _last_monitor_log: float = 0.0
            while not self._halted():
                with self._lock:
                    client = self._ntrip_client

                if client:
                    status = client.get_status()
                    self.gnss_state.update_ntrip_status(
                        enabled=True,
                        connected=status.get("connected", False),
                        host=status.get("host", ""),
                        port=status.get("port", 0),
                        mountpoint=status.get("mountpoint", ""),
                        bytes_sent=status.get("bytes_sent", 0),
                        error_message=status.get("last_error"),
                    )

                    # Check if NTRIP is in cooldown (temporary gave_up, not fatal)
                    if status.get("gave_up"):
                        # NTRIP max retries reached — in 10-minute cooldown
                        # This is a network fault, not a base station failure
                        # Stay in NTRIP_CONNECT state and wait for recovery
                        if self._state != AutoflowState.NTRIP_CONNECT:
                            self._set_state(AutoflowState.NTRIP_CONNECT)
                            logger.warning(
                                "[AUTOFLOW] NTRIP in cooldown (10min retry). "
                                "RTCM streaming continues on serial. "
                                "Waiting for NTRIP client to recover."
                            )
                        # Do NOT return — continue monitoring loop
                        # Client will reset gave_up after cooldown and reconnect

                    # Check if NTRIP is stale (no data for >30s)
                    if status.get("stale") and self._state == AutoflowState.STREAMING:
                        logger.warning("[AUTOFLOW] NTRIP stale — no data for >30s, reconnecting")
                        client.stop()
                        client.start()
                        status["stale"] = True

                    if not status["connected"] and self._state == AutoflowState.STREAMING:
                        logger.warning("[AUTOFLOW] NTRIP disconnected — awaiting reconnect...")
                        self._set_state(AutoflowState.NTRIP_CONNECT)
                    elif status["connected"] and self._state == AutoflowState.NTRIP_CONNECT:
                        self._set_state(AutoflowState.STREAMING)
                        logger.info("[AUTOFLOW] NTRIP reconnected")

                    # Periodic streaming stats every 60 s
                    now = time.time()
                    if now - _last_monitor_log >= 60.0:
                        logger.info(
                            f"[STREAMING] connected={status.get('connected')}  "
                            f"frames={status.get('frames_sent', 0):,}  "
                            f"bytes={status.get('bytes_sent', 0):,}  "
                            f"rate={status.get('data_rate_bps', 0):.0f} B/s  "
                            f"uptime={status.get('uptime', 0):.0f}s  "
                            f"attempts={status.get('connect_attempts', 0)}"
                        )
                        _last_monitor_log = now

                    self._emit("autoflow_ntrip", status)

                self._stop_event.wait(timeout=10.0)

        else:
            # No NTRIP — RTCM active on serial ports, base station ready
            self._set_state(AutoflowState.STREAMING)
            logger.info("[AUTOFLOW] === RTCM active — no NTRIP configured — base station ready ===")
            self._emit("autoflow_state", self._status_dict())

    # ── Helpers ───────────────────────────────────────────────────────────

    def _start_ntrip(self, cfg: AutoflowConfig) -> None:
        """Create NTRIPPushClient, register RTCM callback on reader, start thread."""
        self._cleanup_ntrip()
        from app.gnss.ntrip_push import NTRIPPushClient
        client = NTRIPPushClient(
            host=cfg.ntrip_host,
            port=cfg.ntrip_port,
            mountpoint=cfg.ntrip_mountpoint,
            password=cfg.ntrip_password,
            username=cfg.ntrip_username,
            ntrip_version=cfg.ntrip_version,
            max_retries=10,
        )

        # Wire up NTRIP connection callbacks to emit status events
        client.set_connected_callback(lambda: self._emit_ntrip_status(client))
        client.set_disconnected_callback(lambda: self._emit_ntrip_status(client))

        with self._lock:
            self._ntrip_client = client
        self.gnss_reader.set_rtcm_callback("ntrip", client.put_rtcm)
        client.start()

    def _cleanup_ntrip(self) -> None:
        """Stop NTRIP client and remove the reader RTCM callback."""
        self.gnss_reader.remove_rtcm_callback("ntrip")
        with self._lock:
            client = self._ntrip_client
            self._ntrip_client = None
        if client:
            client.stop()

    def _start_lora(self) -> None:
        """Create LoRaPushClient and register RTCM callback."""
        self._cleanup_lora()
        from app.gnss.lora_push import LoRaPushClient
        client = LoRaPushClient()
        with self._lock:
            self._lora_client = client
        self.gnss_reader.set_rtcm_callback("lora", client.put_rtcm)
        client.start()
        logger.info("[AUTOFLOW] LoRa streaming started")
        self._emit("lora_status", client.get_status())

    def _cleanup_lora(self) -> None:
        """Stop LoRa client and remove RTCM callback."""
        self.gnss_reader.remove_rtcm_callback("lora")
        with self._lock:
            client = self._lora_client
            self._lora_client = None
        if client:
            client.stop()
        logger.info("[AUTOFLOW] LoRa client cleaned up")

    def _set_state(self, new_state: AutoflowState) -> None:
        with self._lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            logger.info(f"[AUTOFLOW] {old.value} → {new_state.value}")
            self._emit("autoflow_state", self._status_dict())

    def _halted(self) -> bool:
        """True if stop was requested or run was aborted."""
        with self._lock:
            return self._stop_event.is_set() or self._state == AutoflowState.IDLE

    def _status_dict(self) -> dict:
        with self._lock:
            ntrip = self._ntrip_client.get_status() if self._ntrip_client else None
            
            # Location change pending state for plain WebSocket
            lc_active = self._state == AutoflowState.AWAITING_CONFIRM
            lc_distance = self._location_change_distance
            lc_deadline = self._location_change_deadline
            lc_remaining = (
                max(0, int(lc_deadline - time.monotonic()))
                if lc_deadline else None
            )
            
            return {
                "state": self._state.value,
                "enabled": self._config.enabled,
                "last_error": self._last_error,
                "config": self._config.to_dict(),
                "ntrip": ntrip,
                "survey_elapsed": (
                    int(time.time() - self._survey_start_ts)
                    if self._survey_start_ts and self._state == AutoflowState.SURVEY
                    else None
                ),
                "stuck_retries": self._stuck_retries,
                "location_change_pending": {
                    "active": lc_active,
                    "distance_metres": round(lc_distance, 1) if lc_distance else None,
                    "auto_resurvey_in_seconds": lc_remaining,
                },
            }

    # ── Socket.IO bridge: thread → asyncio event loop ────────────────────

    def _emit(self, event: str, data: dict) -> None:
        """
        Emit a Socket.IO event from the background thread.
        asyncio.run_coroutine_threadsafe is the ONLY correct way to
        submit a coroutine from a non-async thread into the event loop.
        """
        if self.loop is None or self.loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.sio.emit(event, data), self.loop
            )
            # Fire-and-forget with error logging; never block the thread.
            future.add_done_callback(self._on_emit_done)
        except RuntimeError as e:
            logger.warning(f"[AUTOFLOW] run_coroutine_threadsafe: {e}")

    def _emit_ntrip_status(self, client: "NTRIPPushClient") -> None:
        """Mirror live NTRIP client status into shared state and emit it."""
        status = client.get_status()
        self.gnss_state.update_ntrip_status(
            enabled=True,
            connected=status.get("connected", False),
            host=status.get("host", ""),
            port=status.get("port", 0),
            mountpoint=status.get("mountpoint", ""),
            bytes_sent=status.get("bytes_sent", 0),
            error_message=status.get("last_error"),
        )
        self._emit("ntrip_status", status)

    @staticmethod
    def _on_emit_done(future: concurrent.futures.Future) -> None:
        exc = future.exception()
        if exc:
            logger.error(f"[AUTOFLOW] Socket.IO emit error: {exc}")

    # ── Config persistence ────────────────────────────────────────────────

    def _load_config(self) -> None:
        try:
            if _CONFIG_FILE.exists():
                data = json.loads(_CONFIG_FILE.read_text())
                self._config = AutoflowConfig.from_dict(data)
                # If password not in JSON, load from env
                if not self._config.ntrip_password:
                    self._config.ntrip_password = os.getenv("NTRIP_PASSWORD", "")
                logger.info(
                    f"[BOOT] AutoFlow config loaded: enabled={self._config.enabled}  "
                    f"msm={self._config.msm_type}  dur={self._config.min_duration_sec}s  "
                    f"ntrip_password={'SET' if self._config.ntrip_password else 'NOT SET'}"
                )
            else:
                logger.info("[BOOT] No autoflow_config.json found — using defaults")
                self._config.ntrip_password = os.getenv("NTRIP_PASSWORD", "")
        except Exception as e:
            logger.warning(f"[BOOT] Failed to load autoflow config: {e} — using defaults")

    def _save_config(self) -> None:
        try:
            data = self._config.to_dict_full()
            data.pop("ntrip_password", None)  # Never persist password to disk
            _CONFIG_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"[AUTOFLOW] Failed to save config: {e}")
