"""
HTTP REST API Routes.

FastAPI router with endpoints for GNSS control, status monitoring,
and configuration management.
"""

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import (
    AutoflowConfigRequest,
    AutoflowConfigResponse,
    AutoflowStatusResponse,
    BaseReferenceStatusResponse,
    BaseModeRequest,
    CommandRequest,
    CommandResponse,
    FixedBaseRequest,
    FixedBaseResponse,
    FullStatus,
    GNSSStatus,
    LoRaStatus,
    NTRIPStartRequest,
    NTRIPStatus,
    RTCMConfigRequest,
    RTCMStatus,
    ReceiverStatus,
    SurveyStartRequest,
    SurveyStatus,
)
from app.gnss.commands import GNSSCommands
from app.gnss.geodesy import ecef_to_llh
from app.gnss.autoflow import _BASE_POSITION_FILE, AutoflowState

if TYPE_CHECKING:
    from app.gnss.autoflow import AutoflowOrchestrator
    from app.gnss.reader import GNSSReader
    from app.gnss.state import GNSSState

logger = logging.getLogger(__name__)

# Router with prefix
router = APIRouter(prefix="/api/v1", tags=["GNSS"])

# Global references (set during app startup)
_gnss_reader: "GNSSReader | None" = None
_gnss_state: "GNSSState | None" = None
_orchestrator: "AutoflowOrchestrator | None" = None


def set_dependencies(
    gnss_reader: "GNSSReader",
    gnss_state: "GNSSState",
    orchestrator: "AutoflowOrchestrator | None" = None,
) -> None:
    """Set GNSS dependencies for routes. Called during FastAPI app startup."""
    global _gnss_reader, _gnss_state, _orchestrator
    _gnss_reader = gnss_reader
    _gnss_state = gnss_state
    _orchestrator = orchestrator
    logger.info("API routes dependencies initialized")


def _get_orchestrator() -> "AutoflowOrchestrator":
    if _orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AutoFlow orchestrator not initialized",
        )
    return _orchestrator


def _get_live_ntrip_status() -> dict[str, Any] | None:
    """Prefer live NTRIP client status from the orchestrator when available."""
    if _orchestrator is None:
        return None
    try:
        status = _orchestrator.get_status()
    except Exception:
        return None
    return status.get("ntrip")


def _get_reader() -> "GNSSReader":
    """Get GNSS reader instance or raise error."""
    if _gnss_reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GNSS reader not initialized",
        )
    return _gnss_reader


def _get_state() -> "GNSSState":
    """Get GNSS state instance or raise error."""
    if _gnss_state is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GNSS state not initialized",
        )
    return _gnss_state


def _send_cfg_command_with_ack(
    reader: "GNSSReader",
    command,
    label: str,
    timeout: float = 8.0,
    require_ack: bool = True,
) -> Optional[bool]:
    """
    Send a receiver config command and wait for ACK/NAK.

    Returns True on ACK, False on NAK, None on timeout.
    """
    result = reader.send_command_and_wait_ack(command, timeout=timeout)
    if result is True:
        logger.info(f"[API] {label}: ACK")
        return True
    if result is False:
        logger.error(f"[API] {label}: NAK")
        if require_ack:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{label} rejected by receiver (NAK)",
            )
        return False

    logger.warning(f"[API] {label}: no ACK within {timeout}s")
    if require_ack:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{label} timed out waiting for receiver ACK",
        )
    return None


# =============================================================================
# Status Endpoints
# =============================================================================


@router.get("/status", response_model=FullStatus, summary="Get full system status")
async def get_full_status() -> dict[str, Any]:
    """
    Get complete GNSS system status.

    Returns position, survey, RTCM, NTRIP, and receiver status
    in a single response.
    """
    state = _get_state()
    reader = _get_reader()

    state_dict = state.to_dict()
    state_dict["reader"] = reader.get_status()
    live_ntrip = _get_live_ntrip_status()
    if live_ntrip is not None:
        state_dict["ntrip"].update({
            "enabled": True,
            "connected": live_ntrip.get("connected", False),
            "host": live_ntrip.get("host", ""),
            "port": live_ntrip.get("port", 0),
            "mountpoint": live_ntrip.get("mountpoint", ""),
            "bytes_sent": live_ntrip.get("bytes_sent", 0),
            "bytes_received": live_ntrip.get("bytes_received", 0),
            "uptime": live_ntrip.get("uptime", 0.0),
            "error_message": live_ntrip.get("last_error"),
            "in_cooldown": live_ntrip.get("in_cooldown", False),
            "cooldown_remaining_seconds": live_ntrip.get("cooldown_remaining_seconds"),
        })
        # Sync live RTCM frame count from NTRIP pusher into rtcm section
        state_dict["rtcm"]["total_messages_sent"] = live_ntrip.get("frames_sent", 0)
        state_dict["rtcm"]["data_rate"] = live_ntrip.get("data_rate_bps", 0.0)

    return state_dict


@router.get("/status/position", response_model=GNSSStatus, summary="Get position status")
async def get_position() -> dict[str, Any]:
    """
    Get current GNSS position and fix status.

    Returns latitude, longitude, altitude, accuracy, fix type,
    satellite count, and other positioning data.
    """
    state = _get_state()
    pos = state.position

    fix_type_str = {
        0: "no_fix",
        1: "dr_only",
        2: "fix_2d",
        3: "fix_3d",
        4: "gnss_dr",
        5: "time_only",
    }.get(pos.fix_type, "unknown")

    return {
        "latitude": pos.latitude,
        "longitude": pos.longitude,
        "altitude": pos.altitude,
        "accuracy": pos.accuracy,
        "vertical_accuracy": pos.vertical_accuracy,
        "fix_type": pos.fix_type,
        "fix_type_str": fix_type_str,
        "num_satellites": pos.num_satellites,
        "carrier_solution": 0,  # Would need NAV-PVT carrSoln
        "ground_speed": pos.ground_speed,
        "heading": pos.heading,
        "pdop": pos.pdop,
        "timestamp": pos.timestamp,
    }


@router.get("/status/survey", response_model=SurveyStatus, summary="Get survey status")
async def get_survey() -> dict[str, Any]:
    """
    Get current survey-in status.

    Returns survey active/valid state, progress, accuracy,
    observation time, and ECEF coordinates.
    """
    state = _get_state()
    survey = state.survey

    return {
        "active": survey.active,
        "valid": survey.valid,
        "in_progress": survey.in_progress,
        "progress": survey.progress,
        "accuracy": survey.accuracy,
        "observation_time": survey.observation_time,
        "mean_accuracy": survey.mean_accuracy,
        "ecef_x": survey.ecef_x,
        "ecef_y": survey.ecef_y,
        "ecef_z": survey.ecef_z,
        "timestamp": survey.timestamp,
    }


@router.get(
    "/status/base-reference",
    response_model=BaseReferenceStatusResponse,
    summary="Get base reference details",
)
async def get_base_reference() -> dict[str, Any]:
    """
    Get the surveyed base reference and the last fixed-base coordinates applied by the API.

    This helps verify what survey-in produced and what fixed coordinates were last set.
    """
    state = _get_state()
    survey = state.survey
    fixed = state.base_reference

    survey_reference = None
    if survey.observation_time > 0 or survey.valid or any(
        abs(value) > 0.0 for value in (survey.ecef_x, survey.ecef_y, survey.ecef_z)
    ):
        lat, lon, height = ecef_to_llh(survey.ecef_x, survey.ecef_y, survey.ecef_z)
        survey_reference = {
            "mode": "SURVEY_IN",
            "source": "NAV-SVIN",
            "valid": survey.valid,
            "active": survey.active,
            "observation_time": survey.observation_time,
            "mean_accuracy": survey.mean_accuracy,
            "ecef": {
                "x": survey.ecef_x,
                "y": survey.ecef_y,
                "z": survey.ecef_z,
            },
            "llh": {
                "latitude": lat,
                "longitude": lon,
                "height_ellipsoid": height,
            },
            "timestamp": survey.timestamp,
        }

    fixed_reference = None
    if fixed.mode:
        fixed_reference = {
            "mode": fixed.mode,
            "source": fixed.source,
            "fixed_pos_acc": fixed.fixed_pos_acc,
            "rtcm_enabled": fixed.rtcm_enabled,
            "save_to_flash": fixed.save_to_flash,
            "timestamp": fixed.timestamp,
        }
        if (
            fixed.latitude is not None
            and fixed.longitude is not None
            and fixed.height_ellipsoid is not None
        ):
            fixed_reference["llh"] = {
                "latitude": fixed.latitude,
                "longitude": fixed.longitude,
                "height_ellipsoid": fixed.height_ellipsoid,
            }
        if (
            fixed.ecef_x is not None
            and fixed.ecef_y is not None
            and fixed.ecef_z is not None
        ):
            fixed_reference["ecef"] = {
                "x": fixed.ecef_x,
                "y": fixed.ecef_y,
                "z": fixed.ecef_z,
            }

    return {
        "fixed_reference": fixed_reference,
        "survey_reference": survey_reference,
    }


@router.get("/status/rtcm", response_model=RTCMStatus, summary="Get RTCM status")
async def get_rtcm() -> dict[str, Any]:
    """
    Get RTCM message output status.

    Returns enabled state, MSM type, message counts, and data rate.
    """
    state = _get_state()
    rtcm = state.rtcm

    # total_messages_sent and data_rate come from the live NTRIP pusher (authoritative).
    # message_counts and last_message_time come from state (parsed per-frame in reader).
    live_ntrip = _get_live_ntrip_status()
    frames = live_ntrip.get("frames_sent", 0) if live_ntrip else rtcm.total_messages_sent
    data_rate = live_ntrip.get("data_rate_bps", rtcm.data_rate) if live_ntrip else rtcm.data_rate

    return {
        "enabled": rtcm.enabled,
        "msm_type": rtcm.msm_type,
        "message_counts": rtcm.message_counts,
        "data_rate": data_rate,
        "total_messages_sent": frames,
        "last_message_time": rtcm.last_message_time,
    }


@router.get("/status/ntrip", response_model=NTRIPStatus, summary="Get NTRIP status")
async def get_ntrip() -> dict[str, Any]:
    """
    Get NTRIP client connection status.

    Returns connection state, host/port, bytes transferred, and uptime.
    """
    state = _get_state()
    ntrip = state.ntrip
    live_ntrip = _get_live_ntrip_status()
    if live_ntrip is not None:
        return {
            "enabled": True,
            "connected": live_ntrip.get("connected", False),
            "host": live_ntrip.get("host", ""),
            "port": live_ntrip.get("port", 0),
            "mountpoint": live_ntrip.get("mountpoint", ""),
            "bytes_sent": live_ntrip.get("bytes_sent", 0),
            "bytes_received": live_ntrip.get("bytes_received", 0),
            "uptime": live_ntrip.get("uptime", 0.0),
            "error_message": live_ntrip.get("last_error"),
            "in_cooldown": live_ntrip.get("in_cooldown", False),
            "cooldown_remaining_seconds": live_ntrip.get("cooldown_remaining_seconds"),
        }

    return {
        "enabled": ntrip.enabled,
        "connected": ntrip.connected,
        "host": ntrip.host,
        "port": ntrip.port,
        "mountpoint": ntrip.mountpoint,
        "bytes_sent": ntrip.bytes_sent,
        "bytes_received": ntrip.bytes_received,
        "uptime": ntrip.uptime,
        "error_message": ntrip.error_message,
    }


@router.get(
    "/status/receiver", response_model=ReceiverStatus, summary="Get receiver status"
)
async def get_receiver() -> dict[str, Any]:
    """
    Get GNSS receiver connection status.

    Returns serial connection state, port, baudrate, and error counts.
    """
    state = _get_state()
    receiver = state.receiver

    return {
        "connected": receiver.connected,
        "serial_port": receiver.serial_port,
        "baudrate": receiver.baudrate,
        "firmware_version": receiver.firmware_version,
        "hardware_version": receiver.hardware_version,
        "error_count": receiver.error_count,
        "nak_count": receiver.nak_count,
        "ack_count": receiver.ack_count,
    }


# =============================================================================
# Command Endpoints
# =============================================================================


@router.post(
    "/command", response_model=CommandResponse, summary="Execute GNSS command"
)
async def execute_command(request: CommandRequest) -> CommandResponse:
    """
    Execute a GNSS command.

    Supported command types:
    - survey_start: Start survey-in mode
    - survey_stop: Stop survey-in mode
    - rtcm_enable: Enable RTCM output
    - rtcm_disable: Disable RTCM output
    - poll_svin: Poll survey-in status
    - poll_pvt: Poll position/velocity/time
    - poll_sat: Poll satellite info
    - base_mode: Configure base station mode
    """
    reader = _get_reader()

    cmd_type = request.type
    params = request.params

    try:
        if cmd_type == "survey_start":
            min_dur = params.get("min_duration", 300)
            acc_limit = params.get("accuracy_limit", 0.10)
            cmd = GNSSCommands.create_survey_start_command(min_dur, acc_limit)
            reader.send_command(cmd)
            logger.info(f"[API] Survey start: min_dur={min_dur}s  acc_limit={acc_limit}m")
            return CommandResponse(
                success=True,
                message=f"Survey start command sent (min_dur={min_dur}s, acc={acc_limit}m)",
                type=cmd_type,
            )

        elif cmd_type == "survey_stop":
            cmd = GNSSCommands.create_survey_stop_command()
            reader.send_command(cmd)
            logger.info("[API] Survey stop command sent")
            return CommandResponse(
                success=True, message="Survey stop command sent", type=cmd_type
            )

        elif cmd_type == "rtcm_enable":
            msm_type = params.get("msm_type", "MSM4")
            cmd = GNSSCommands.create_rtcm_enable_command(msm_type)
            reader.send_command(cmd)
            logger.info(f"[RTCM] Enable {msm_type} command sent")
            return CommandResponse(
                success=True, message=f"RTCM enable command sent ({msm_type})", type=cmd_type
            )

        elif cmd_type == "rtcm_disable":
            cmd = GNSSCommands.create_rtcm_disable_command()
            reader.send_command(cmd)
            logger.info("[RTCM] Disable command sent")
            return CommandResponse(
                success=True, message="RTCM disable command sent", type=cmd_type
            )

        elif cmd_type == "poll_svin":
            cmd = GNSSCommands.create_nav_svin_poll_command()
            reader.send_command(cmd)
            return CommandResponse(
                success=True, message="NAV-SVIN poll sent", type=cmd_type
            )

        elif cmd_type == "poll_pvt":
            cmd = GNSSCommands.create_nav_pvt_poll_command()
            reader.send_command(cmd)
            return CommandResponse(
                success=True, message="NAV-PVT poll sent", type=cmd_type
            )

        elif cmd_type == "poll_sat":
            cmd = GNSSCommands.create_nav_sat_poll_command()
            reader.send_command(cmd)
            return CommandResponse(
                success=True, message="NAV-SAT poll sent", type=cmd_type
            )

        elif cmd_type == "base_mode":
            msm_type = params.get("msm_type", "MSM4")
            survey_mode = params.get("survey_mode", True)
            cmd = GNSSCommands.create_base_mode_command(
                msm_type=msm_type,
                survey_mode=survey_mode,
                min_duration=params.get("min_duration", 300),
                accuracy_limit=params.get("accuracy_limit", 0.10),
            )
            reader.send_command(cmd)
            return CommandResponse(
                success=True,
                message=f"Base mode command sent ({msm_type}, survey={survey_mode})",
                type=cmd_type,
            )

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown command type: {cmd_type}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing command {cmd_type}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Command failed: {str(e)}",
        )


@router.post(
    "/survey/start",
    response_model=CommandResponse,
    summary="Start survey-in mode",
)
async def start_survey(request: SurveyStartRequest) -> dict[str, Any]:
    """
    Start survey-in mode for base station setup.

    The receiver will collect observations until the minimum duration
    is reached and accuracy is within the specified limit.
    
    By default, warns if AutoFlow is already running or RTCM is streaming.
    Pass force=true to abort active AutoFlow and start fresh survey (operator override).
    """
    reader = _get_reader()
    state = _get_state()
    orch = _get_orchestrator()

    # Check if AutoFlow is actively streaming or surveying
    if orch is not None:
        current_state = orch.state
        if current_state in (
            AutoflowState.STREAMING,
            AutoflowState.NTRIP_CONNECT,
            AutoflowState.ENABLING_RTCM,
            AutoflowState.SURVEY,
        ) and not request.force:
            return CommandResponse(
                success=False,
                warning=True,
                message=(
                    f"AutoFlow is currently {current_state.value}. "
                    "Starting a new survey will stop RTCM streaming and "
                    "change the base ARP position. Rover will lose RTK fix. "
                    "Send with force=true to proceed."
                ),
                current_state=current_state.value,
            )
        # If force=true and active, abort AutoFlow first
        if request.force and current_state not in (AutoflowState.IDLE, AutoflowState.FAILED):
            logger.warning(f"[API] Force-survey during {current_state.value} — aborting AutoFlow")
            orch.abort()
            time.sleep(1.0)

    try:
        # Mirror the proven manual flow: disable RTCM, stop any existing TMODE,
        # clear stale survey state, then start a fresh survey-in run.
        _send_cfg_command_with_ack(
            reader,
            GNSSCommands.create_rtcm_disable_command(),
            label="RTCM disable before survey start",
            timeout=8.0,
            require_ack=False,
        )
        time.sleep(0.5)
        _send_cfg_command_with_ack(
            reader,
            GNSSCommands.create_survey_stop_command(),
            label="Survey stop before fresh survey start",
            timeout=8.0,
            require_ack=False,
        )
        time.sleep(0.5)

        state.update_survey(
            active=False,
            valid=False,
            in_progress=False,
            progress=0,
            accuracy=0.0,
            observation_time=0,
            mean_accuracy=0.0,
            ecef_x=0.0,
            ecef_y=0.0,
            ecef_z=0.0,
        )

        cmd = GNSSCommands.create_survey_start_command(
            min_duration=request.min_duration,
            accuracy_limit=request.accuracy_limit,
        )
        _send_cfg_command_with_ack(
            reader,
            cmd,
            label="Survey start",
            timeout=8.0,
            require_ack=True,
        )
        logger.info(f"[API] Survey start: min_dur={request.min_duration}s  acc_limit={request.accuracy_limit}m  force={request.force}")

        return CommandResponse(
            success=True,
            message=f"Survey started: min_duration={request.min_duration}s, accuracy_limit={request.accuracy_limit}m",
            type="survey_start",
        )

    except Exception as e:
        logger.error(f"Error starting survey: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start survey: {str(e)}",
        )


@router.post(
    "/survey/stop",
    response_model=CommandResponse,
    summary="Stop survey-in mode",
)
async def stop_survey() -> CommandResponse:
    """
    Stop survey-in mode.

    Disables TMODE, returning the receiver to normal operation.
    Use this after survey-in is complete to use the computed position.
    """
    reader = _get_reader()

    try:
        cmd = GNSSCommands.create_survey_stop_command()
        _send_cfg_command_with_ack(
            reader,
            cmd,
            label="Survey stop",
            timeout=8.0,
            require_ack=False,
        )
        logger.info("[API] Survey stop command sent")

        return CommandResponse(
            success=True,
            message="Survey stopped successfully",
            type="survey_stop",
        )

    except Exception as e:
        logger.error(f"Error stopping survey: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop survey: {str(e)}",
        )


@router.post(
    "/rtcm/configure",
    response_model=CommandResponse,
    summary="Configure RTCM output",
)
async def configure_rtcm(request: RTCMConfigRequest) -> CommandResponse:
    """
    Configure RTCM message output.

    Enable or disable RTCM3 messages with specified MSM type.
    MSM4 provides standard precision, MSM7 provides higher precision.
    """
    reader = _get_reader()

    try:
        # Doc-compat: enable_beidou=True maps to MSM7, False to MSM4
        msm_type = request.msm_type
        if request.enable_beidou is not None:
            msm_type = "MSM7" if request.enable_beidou else "MSM4"

        if request.enable:
            cmd = GNSSCommands.create_rtcm_enable_command(msm_type)
            action = "enabled"
            logger.info(f"[RTCM] Enable {msm_type} command sent")
        else:
            cmd = GNSSCommands.create_rtcm_disable_command()
            action = "disabled"
            logger.info("[RTCM] Disable command sent")

        reader.send_command(cmd)

        return CommandResponse(
            success=True,
            message=f"RTCM {action} (BeiDou: {msm_type == 'MSM7'})",
            type="rtcm_configure",
        )

    except Exception as e:
        logger.error(f"Error configuring RTCM: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to configure RTCM: {str(e)}",
        )


@router.post(
    "/mode/base",
    response_model=CommandResponse,
    summary="Configure base station mode",
)
async def configure_base_mode(request: BaseModeRequest) -> CommandResponse:
    """
    Configure base station mode.

    Enables survey-in (or fixed mode) and RTCM output for base station
    operation. Use survey_mode=True for automatic position determination,
    or survey_mode=False with ECEF coordinates for a known position.
    """
    reader = _get_reader()

    try:
        state = _get_state()
        if request.survey_mode:
            cmd = GNSSCommands.create_base_mode_command(
                msm_type=request.msm_type,
                survey_mode=True,
                min_duration=request.min_duration,
                accuracy_limit=request.accuracy_limit,
            )
            mode_desc = f"survey mode, {request.msm_type}"
            logger.info(f"[API] Base mode: survey  msm={request.msm_type}  min_dur={request.min_duration}s  acc={request.accuracy_limit}m")
        else:
            # Fixed mode requires coordinates
            if (
                request.ecef_x is None
                or request.ecef_y is None
                or request.ecef_z is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="ECEF coordinates required for fixed mode",
                )

            cmd = GNSSCommands.create_fixed_mode_command(
                ecef_x=request.ecef_x,
                ecef_y=request.ecef_y,
                ecef_z=request.ecef_z,
            )
            lat, lon, height = ecef_to_llh(
                request.ecef_x,
                request.ecef_y,
                request.ecef_z,
            )
            mode_desc = f"fixed mode, {request.msm_type}"
            logger.info(f"[API] Base mode: fixed  msm={request.msm_type}  X={request.ecef_x}  Y={request.ecef_y}  Z={request.ecef_z}")

        reader.send_command(cmd)

        if request.survey_mode:
            state.update_base_reference(
                mode="SURVEY_IN",
                source="base_mode_request",
                latitude=None,
                longitude=None,
                height_ellipsoid=None,
                ecef_x=None,
                ecef_y=None,
                ecef_z=None,
                fixed_pos_acc=request.accuracy_limit,
                rtcm_enabled=True,
                save_to_flash=None,
            )
        else:
            state.update_base_reference(
                mode="FIXED",
                source="base_mode_request_ecef",
                latitude=lat,
                longitude=lon,
                height_ellipsoid=height,
                ecef_x=request.ecef_x,
                ecef_y=request.ecef_y,
                ecef_z=request.ecef_z,
                fixed_pos_acc=request.accuracy_limit,
                rtcm_enabled=True,
                save_to_flash=None,
            )

        return CommandResponse(
            success=True,
            message=f"Base station configured: {mode_desc}",
            type="base_mode",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error configuring base mode: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to configure base mode: {str(e)}",
        )


@router.post(
    "/base/fixed",
    response_model=FixedBaseResponse,
    summary="Configure fixed base station with LLH coordinates",
)
async def configure_fixed_base(request: FixedBaseRequest) -> FixedBaseResponse:
    """
    Configure the receiver as a fixed base station using LLH coordinates.

    This route:
    1. Stops any active autoflow/NTRIP streaming
    2. Disables RTCM output and any existing TMODE
    3. Applies the fixed LLH configuration
    4. Re-enables RTCM with the requested MSM type
    5. Restarts NTRIP streaming using the saved autoflow caster config, if available

    Args:
        request: FixedBaseRequest with latitude, longitude, height, and options

    Returns:
        FixedBaseResponse with applied configuration details

    Note:
        - Default memory layer is RAM (volatile). Use save_to_flash=True for persistence.
        - High-precision (HP) fields are optional and disabled by default.
        - This route uses the AMA0 application path only.
    """
    from pyubx2 import SET_LAYER_BBR, SET_LAYER_FLASH, SET_LAYER_RAM

    reader = _get_reader()
    state = _get_state()
    orch = _orchestrator

    # Resolve ellipsoid height — u-blox CFG_TMODE_HEIGHT requires WGS84 HAE, not MSL
    if request.geoid_separation is not None:
        height_ellipsoid = request.height + request.geoid_separation
        logger.info(
            f"[API] Height conversion: MSL={request.height:.3f}m + N={request.geoid_separation:.3f}m "
            f"→ ellipsoid={height_ellipsoid:.3f}m"
        )
    else:
        height_ellipsoid = request.height

    # Determine memory layers
    layers = SET_LAYER_RAM
    if request.save_to_flash:
        layers = SET_LAYER_RAM | SET_LAYER_BBR | SET_LAYER_FLASH
        layers_str = "RAM+BBR+FLASH"
    else:
        layers = SET_LAYER_RAM | SET_LAYER_BBR
        layers_str = "RAM+BBR"

    try:
        # Step 0: stop any active autoflow/NTRIP so the coordinate switch is clean.
        if orch is not None:
            orch.abort()
        state.update_rtcm_status(enabled=False, msm_type="")
        state.update_ntrip_status(
            enabled=False,
            connected=False,
            host="",
            port=0,
            mountpoint="",
            bytes_sent=0,
            error_message=None,
        )

        # Step 1: Disable RTCM output (prevent broadcasting stale position)
        _send_cfg_command_with_ack(
            reader,
            GNSSCommands.create_rtcm_disable_command(),
            label="RTCM disable before fixed base config",
            timeout=8.0,
            require_ack=False,  # Non-critical, continue even if this fails
        )
        time.sleep(0.5)

        # Step 2: Disable any existing TMODE
        _send_cfg_command_with_ack(
            reader,
            GNSSCommands.create_survey_stop_command(),
            label="TMODE disable before fixed base config",
            timeout=8.0,
            require_ack=True,
        )
        time.sleep(0.5)

        # Step 3: Apply fixed LLH configuration (height must be WGS84 ellipsoid height)
        _send_cfg_command_with_ack(
            reader,
            GNSSCommands.create_fixed_llh_command(
                latitude=request.latitude,
                longitude=request.longitude,
                height=height_ellipsoid,
                fixed_pos_acc=request.fixed_pos_acc,
                use_high_precision=request.use_high_precision,
                lat_hp=request.lat_hp,
                lon_hp=request.lon_hp,
                height_hp=request.height_hp,
                layers=layers,
            ),
            label="Fixed LLH config",
            timeout=8.0,
            require_ack=True,
        )

        # Step 4: Optionally re-enable RTCM output
        rtcm_enabled = False
        if request.enable_rtcm:
            _send_cfg_command_with_ack(
                reader,
                GNSSCommands.create_rtcm_enable_command(msm_type=request.msm_type),
                label=f"RTCM enable {request.msm_type}",
                timeout=10.0,
                require_ack=True,
            )
            rtcm_enabled = True
            state.update_rtcm_status(enabled=True, msm_type=request.msm_type)

        # Step 5: Restart NTRIP streaming using the saved autoflow config.
        if rtcm_enabled and orch is not None:
            autoflow_cfg = orch.get_config_copy()
            if autoflow_cfg.ntrip_host and autoflow_cfg.ntrip_mountpoint:
                orch.start_ntrip_direct(
                    host=autoflow_cfg.ntrip_host,
                    port=autoflow_cfg.ntrip_port,
                    mountpoint=autoflow_cfg.ntrip_mountpoint,
                    password=autoflow_cfg.ntrip_password,
                    username=autoflow_cfg.ntrip_username,
                    ntrip_version=autoflow_cfg.ntrip_version,
                )
                logger.info(
                    f"[API] Fixed base restarted NTRIP: "
                    f"{autoflow_cfg.ntrip_host}:{autoflow_cfg.ntrip_port}/{autoflow_cfg.ntrip_mountpoint}"
                )

        logger.info(
            f"[API] Fixed base configured: LLH({request.latitude:.7f}, {request.longitude:.7f}, "
            f"h_ellipsoid={height_ellipsoid:.3f}m) "
            f"acc={request.fixed_pos_acc:.3f}m layers={layers_str} rtcm={rtcm_enabled}"
        )

        applied_llh: dict[str, float] = {
            "latitude": request.latitude,
            "longitude": request.longitude,
            "height_ellipsoid": height_ellipsoid,
            "height_input": request.height,
        }
        if request.geoid_separation is not None:
            applied_llh["geoid_separation"] = request.geoid_separation

        state.update_base_reference(
            mode="FIXED",
            source="fixed_llh_api",
            latitude=request.latitude,
            longitude=request.longitude,
            height_ellipsoid=height_ellipsoid,
            ecef_x=None,
            ecef_y=None,
            ecef_z=None,
            fixed_pos_acc=request.fixed_pos_acc,
            rtcm_enabled=rtcm_enabled,
            save_to_flash=request.save_to_flash,
        )

        return FixedBaseResponse(
            success=True,
            message="Fixed base station configured successfully",
            effective_mode="FIXED",
            applied_llh=applied_llh,
            applied_accuracy=request.fixed_pos_acc,
            layers_applied=layers_str,
            rtcm_enabled=rtcm_enabled,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error configuring fixed base: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to configure fixed base: {str(e)}",
        )


# =============================================================================
# Reader Control Endpoints
# =============================================================================


@router.get("/reader/status", summary="Get reader thread status")
async def get_reader_status() -> dict[str, Any]:
    """
    Get GNSS reader thread status.

    Returns running state, connection state, message counts,
    and queue sizes.
    """
    reader = _get_reader()
    return reader.get_status()


@router.post("/reader/reconnect", summary="Force reader reconnect")
async def reconnect_reader() -> dict[str, str]:
    """
    Force GNSS reader to reconnect.

    Closes current serial connection and attempts to reopen.
    Useful for recovering from transient serial errors.
    """
    reader = _get_reader()

    try:
        # Stop and restart reader
        reader.stop()
        reader.start()

        return {"status": "reconnect initiated", "port": reader.port}

    except Exception as e:
        logger.error(f"Error reconnecting reader: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reconnect failed: {str(e)}",
        )


# =============================================================================
# AutoFlow Endpoints
# =============================================================================


@router.get(
    "/autoflow/status",
    response_model=AutoflowStatusResponse,
    summary="Get autoflow orchestrator status",
    tags=["AutoFlow"],
)
async def get_autoflow_status() -> dict[str, Any]:
    """
    Get the current autoflow state, config, and NTRIP push status.
    """
    orch = _get_orchestrator()
    return orch.get_status()


@router.post(
    "/autoflow/config",
    response_model=CommandResponse,
    summary="Save autoflow config (and start/stop based on enabled flag)",
    tags=["AutoFlow"],
)
async def save_autoflow_config(request: AutoflowConfigRequest) -> CommandResponse:
    """
    Persist autoflow configuration.
    If enabled transitions from False → True, a run is auto-triggered.
    If enabled transitions from True → False, the current run is aborted.
    """
    from app.gnss.autoflow import AutoflowConfig
    orch = _get_orchestrator()
    current_cfg = orch.get_config_copy()

    # Frontends read the config via a masked password field. If they save the
    # unchanged payload back to the API, preserve the real secret instead of
    # overwriting it with "***".
    password = request.ntrip_password
    if password == "***":
        password = current_cfg.ntrip_password

    cfg = AutoflowConfig(
        enabled=request.enabled,
        min_duration_sec=request.min_duration_sec,
        accuracy_limit_m=request.accuracy_limit_m,
        msm_type=request.msm_type,
        ntrip_host=request.ntrip_host,
        ntrip_port=request.ntrip_port,
        ntrip_mountpoint=request.ntrip_mountpoint,
        ntrip_username=request.ntrip_username,
        ntrip_password=password,
        ntrip_version=request.ntrip_version,
    )
    orch.save_config(cfg)
    logger.info(f"[API] AutoFlow config saved: enabled={cfg.enabled}  msm={cfg.msm_type}")
    return CommandResponse(
        success=True,
        message=f"AutoFlow config saved (enabled={cfg.enabled})",
        type="autoflow_config",
    )


@router.post(
    "/autoflow/start",
    response_model=CommandResponse,
    summary="Manually trigger autoflow run",
    tags=["AutoFlow"],
)
async def start_autoflow() -> CommandResponse:
    """
    Trigger an autoflow run regardless of the enabled flag.
    Ignored if a run is already active.
    """
    orch = _get_orchestrator()
    orch.trigger_run()
    logger.info("[API] AutoFlow start triggered manually")
    return CommandResponse(
        success=True,
        message="AutoFlow run triggered",
        type="autoflow_start",
    )


@router.post(
    "/autoflow/stop",
    response_model=CommandResponse,
    summary="Abort the current autoflow run",
    tags=["AutoFlow"],
)
async def stop_autoflow() -> CommandResponse:
    """
    Abort the current autoflow run and return to IDLE.
    Does not change the enabled flag or saved config.
    """
    orch = _get_orchestrator()
    orch.abort()
    logger.info("[API] AutoFlow aborted via API")
    return CommandResponse(
        success=True,
        message="AutoFlow aborted — returned to IDLE",
        type="autoflow_stop",
    )


@router.get(
    "/autoflow/config",
    response_model=AutoflowConfigResponse,
    summary="Get saved autoflow config",
    tags=["AutoFlow"],
)
async def get_autoflow_config() -> dict[str, Any]:
    """
    Get the saved autoflow configuration (password masked).
    """
    orch = _get_orchestrator()
    return orch._config.to_dict()


@router.post(
    "/autoflow/enable",
    response_model=CommandResponse,
    summary="Enable autoflow (sets enabled=true and triggers run)",
    tags=["AutoFlow"],
)
async def enable_autoflow() -> CommandResponse:
    """
    Enable autoflow by setting enabled=true and triggering a run.
    Loads current saved config, sets enabled=true, saves, and triggers run.
    """
    from app.gnss.autoflow import AutoflowConfig
    orch = _get_orchestrator()
    
    cfg = orch._config
    cfg.enabled = True
    orch.save_config(cfg)
    logger.info("[API] AutoFlow enabled via /autoflow/enable")
    return CommandResponse(
        success=True,
        message="AutoFlow enabled — run triggered",
        type="autoflow_enable",
    )


@router.post(
    "/autoflow/disable",
    response_model=CommandResponse,
    summary="Disable autoflow (sets enabled=false and aborts run)",
    tags=["AutoFlow"],
)
async def disable_autoflow() -> CommandResponse:
    """
    Disable autoflow by setting enabled=false and aborting current run.
    Loads current saved config, sets enabled=false, saves, and aborts.
    """
    from app.gnss.autoflow import AutoflowConfig
    orch = _get_orchestrator()
    
    cfg = orch._config
    cfg.enabled = False
    orch.save_config(cfg)
    logger.info("[API] AutoFlow disabled via /autoflow/disable")
    return CommandResponse(
        success=True,
        message="AutoFlow disabled — run aborted",
        type="autoflow_disable",
    )


@router.post(
    "/receiver/reset",
    response_model=CommandResponse,
    summary="Reset GNSS receiver (UBX-CFG-RST hotstart)",
    tags=["GNSS"],
)
async def reset_receiver(force: bool = False) -> dict[str, Any]:
    """
    Reset the GNSS receiver using UBX-CFG-RST hotstart command.
    
    By default, warns if receiver is actively streaming RTCM corrections.
    Pass force=true to proceed despite active streaming (operator override).
    
    Args:
        force: If True, ignores active AutoFlow streaming and forces reset
    
    Returns:
        CommandResponse or warning dict with current state
    """
    reader = _get_reader()
    orch = _get_orchestrator()
    
    # Check if AutoFlow is actively streaming
    if orch is not None:
        current_state = orch.state
        if current_state in (
            AutoflowState.STREAMING,
            AutoflowState.NTRIP_CONNECT,
            AutoflowState.ENABLING_RTCM,
        ) and not force:
            return CommandResponse(
                success=False,
                warning=True,
                message=(
                    f"Receiver is currently {current_state.value}. "
                    "Resetting will interrupt RTCM streaming and require "
                    "full AutoFlow restart. Send with force=true to proceed."
                ),
                current_state=current_state.value,
            )
        # If force=true and streaming, abort AutoFlow first
        if force and current_state != AutoflowState.IDLE:
            logger.warning(f"[API] Force-reset during {current_state.value} — aborting AutoFlow")
            orch.abort()
            time.sleep(1.0)
    
    try:
        cmd = GNSSCommands.create_reset_command()
        reader.send_command(cmd)
        logger.info("[API] Receiver reset (hotstart) command sent")
        return CommandResponse(
            success=True,
            message="Receiver reset command sent (hotstart)",
            type="receiver_reset",
        )
    except Exception as e:
        logger.error(f"Error resetting receiver: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset receiver: {str(e)}",
        )


# =============================================================================
# Frontend Compatibility Aliases
# =============================================================================


@router.get("/health", summary="Health check (alias)", tags=["Health"])
async def health_check_alias():
    """Health check — mirrors /health for frontend compatibility."""
    reader = _get_reader()
    state = _get_state()
    return {
        "status": "healthy" if reader.is_connected else "degraded",
        "gnss_connected": reader.is_connected,
        "uptime_seconds": 0.0,
        "version": "1.0.0-phase6",
    }


# =============================================================================
# Base Position Management
# =============================================================================


@router.get("/base/saved-position", tags=["Base"])
async def get_saved_position() -> dict:
    """Return saved base position from data/base_position.json."""
    orch = _get_orchestrator()
    pos = orch._load_base_position()
    if pos is None:
        return {"saved": False, "position": None}
    return {"saved": True, "position": pos}


@router.delete("/base/saved-position", tags=["Base"])
async def delete_saved_position(confirm: bool = False) -> dict:
    """
    Delete saved base position — forces resurvey on next boot.
    
    **DANGEROUS**: Deleting the saved position will cause a fresh survey
    on next boot, changing the base ARP coordinates and causing the rover
    to lose RTK fixed solution until a new position is saved.
    
    Args:
        confirm: Must be True to proceed. Default False requires explicit confirmation.
    
    Returns:
        Warning dict if confirm=false, deletion result if confirm=true
    """
    if not confirm:
        return {
            "success": False,
            "warning": True,
            "message": (
                "This will delete the saved base position. "
                "On next boot, a fresh survey will run and the ARP "
                "position will change, causing rover to lose RTK fix. "
                "Send with ?confirm=true to proceed."
            ),
        }
    
    try:
        if _BASE_POSITION_FILE.exists():
            _BASE_POSITION_FILE.unlink()
            logger.info("[API] Saved base position deleted (confirm=true)")
            return {"success": True, "message": "Saved position deleted — resurvey on next boot"}
        return {"success": False, "message": "No saved position found"}
    except Exception as e:
        logger.error(f"Error deleting saved position: {e}")
        return {"success": False, "message": str(e)}


@router.post("/base/confirm-resurvey", tags=["Base"])
async def confirm_resurvey() -> dict:
    """
    User confirms location changed — triggers resurvey immediately.
    
    Returns 409 Conflict if not in AWAITING_CONFIRM state (e.g., no pending
    location change detection). This endpoint is only valid when AutoFlow
    has detected a location change and is awaiting user confirmation.
    
    Returns:
        Success dict if confirmed, or HTTPException 409 if invalid state
    """
    orch = _get_orchestrator()
    if orch is None or orch.state != AutoflowState.AWAITING_CONFIRM:
        current = orch.state.value if orch else "unknown"
        raise HTTPException(
            status_code=409,
            detail=f"Cannot confirm resurvey: not in AWAITING_CONFIRM state (current: {current})",
        )
    
    orch.confirm_resurvey()
    logger.info("[API] User confirmed resurvey via API")
    return {"success": True, "message": "Resurvey confirmed"}


@router.post("/base/skip-resurvey", tags=["Base"])
async def skip_resurvey() -> dict:
    """
    User confirms same site — use saved position, skip resurvey.
    
    Returns 409 Conflict if not in AWAITING_CONFIRM state (e.g., no pending
    location change detection). This endpoint is only valid when AutoFlow
    has detected a location change and is awaiting user confirmation.
    
    Returns:
        Success dict if skipped, or HTTPException 409 if invalid state
    """
    orch = _get_orchestrator()
    if orch is None or orch.state != AutoflowState.AWAITING_CONFIRM:
        current = orch.state.value if orch else "unknown"
        raise HTTPException(
            status_code=409,
            detail=f"Cannot skip resurvey: not in AWAITING_CONFIRM state (current: {current})",
        )
    
    orch.skip_resurvey()
    logger.info("[API] User skipped resurvey via API (using saved position)")
    return {"success": True, "message": "Using saved base position"}


# =============================================================================
# NTRIP Direct Control
# =============================================================================


@router.post("/ntrip/start", response_model=CommandResponse, summary="Start NTRIP streaming")
async def start_ntrip(request: NTRIPStartRequest) -> CommandResponse:
    """
    Start NTRIP correction streaming to a caster.

    Prerequisites: RTCM output must be configured and survey-in completed.
    """
    orch = _get_orchestrator()
    try:
        orch.start_ntrip_direct(
            host=request.host,
            port=request.port,
            mountpoint=request.mountpoint,
            password=request.password,
            username=request.username or "",
            ntrip_version=request.ntrip_version,
        )
        logger.info(f"[API] NTRIP direct start: {request.host}:{request.port}/{request.mountpoint}")
        return CommandResponse(
            success=True,
            message=f"NTRIP streaming started: {request.host}:{request.port}/{request.mountpoint}",
            type="ntrip_start",
        )
    except Exception as e:
        logger.error(f"Error starting NTRIP: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start NTRIP: {str(e)}",
        )


@router.post("/ntrip/stop", response_model=CommandResponse, summary="Stop NTRIP streaming")
async def stop_ntrip() -> CommandResponse:
    """Stop NTRIP correction streaming."""
    orch = _get_orchestrator()
    try:
        orch.stop_ntrip_direct()
        logger.info("[API] NTRIP direct stop")
        return CommandResponse(
            success=True,
            message="NTRIP streaming stopped",
            type="ntrip_stop",
        )
    except Exception as e:
        logger.error(f"Error stopping NTRIP: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop NTRIP: {str(e)}",
        )


# =============================================================================
# LoRa Direct Control
# =============================================================================


@router.post("/lora/start", tags=["LoRa"])
async def start_lora() -> dict:
    """Start LoRa RTCM streaming to rover."""
    orch = _get_orchestrator()
    # Check RTCM is enabled first
    state = _get_state()
    if not state.rtcm.enabled:
        return {
            "success": False,
            "message": "RTCM not enabled — start AutoFlow first",
        }
    success = orch.start_lora()
    return {
        "success": success,
        "message": "LoRa streaming started" if success else "LoRa already streaming",
        "type": "lora_start",
    }


@router.post("/lora/stop", tags=["LoRa"])
async def stop_lora() -> dict:
    """Stop LoRa RTCM streaming."""
    orch = _get_orchestrator()
    orch.stop_lora()
    return {
        "success": True,
        "message": "LoRa streaming stopped",
        "type": "lora_stop",
    }


@router.get("/lora/status", tags=["LoRa"])
async def get_lora_status() -> dict:
    """Get LoRa streaming status."""
    orch = _get_orchestrator()
    return orch.get_lora_status()


@router.get("/status/lora", tags=["Status"])
async def get_lora_status_alias() -> dict:
    """Alias for /lora/status"""
    return await get_lora_status()
