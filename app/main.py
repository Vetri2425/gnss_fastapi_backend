"""
FastAPI Application Main Module.

Main entry point for the GNSS FastAPI backend. Sets up FastAPI app,
Socket.IO server, GNSS reader, and all middleware.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import socketio
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from uvicorn.config import LOGGING_CONFIG

from app.api.routes import router as api_router, set_dependencies
from app.config import Config
from app.gnss.autoflow import AutoflowOrchestrator
from app.gnss.reader import GNSSReader
from app.gnss.state import GNSSState
from app.websocket.handlers import WebSocketHandler

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format=Config.LOG_FORMAT,
)

# Configure uvicorn logging
LOGGING_CONFIG["formatters"]["default"]["fmt"] = Config.LOG_FORMAT
LOGGING_CONFIG["formatters"]["access"]["fmt"] = Config.LOG_FORMAT
LOGGING_CONFIG["loggers"]["uvicorn.access"]["handlers"] = []
LOGGING_CONFIG["loggers"]["uvicorn.access"]["propagate"] = False
LOGGING_CONFIG["loggers"]["uvicorn.access"]["level"] = "WARNING"

logger = logging.getLogger(__name__)

# Global instances
gnss_state: GNSSState | None = None
gnss_reader: GNSSReader | None = None
ws_handler: WebSocketHandler | None = None
sio: socketio.AsyncServer | None = None
orchestrator: AutoflowOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager — startup and shutdown."""
    global gnss_state, gnss_reader, ws_handler, sio, orchestrator

    logger.info("=" * 60)
    logger.info("GNSS FastAPI Backend Starting")
    logger.info("=" * 60)

    # Initialize GNSS state
    gnss_state = GNSSState()
    logger.info("GNSS state initialized")

    # Initialize GNSS reader
    gnss_reader = GNSSReader(
        state=gnss_state,
        port=Config.SERIAL_PORT,
        baudrate=Config.SERIAL_BAUDRATE,
        timeout=Config.SERIAL_TIMEOUT,
        poll_interval=Config.UBX_POLL_INTERVAL,
    )
    logger.info(f"GNSS reader initialized: {Config.SERIAL_PORT}@{Config.SERIAL_BAUDRATE}")

    # Initialize Socket.IO server
    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=Config.WS_CORS_ORIGINS,  # "*" or list — both valid for socket.io
        ping_timeout=60,
        ping_interval=25,
        logger=Config.DEBUG,
        engineio_logger=Config.DEBUG,
    )

    # Capture the running event loop for the autoflow orchestrator thread bridge
    _loop = asyncio.get_event_loop()

    # Initialize AutoFlow orchestrator
    orchestrator = AutoflowOrchestrator(
        gnss_state=gnss_state,
        gnss_reader=gnss_reader,
        sio=sio,
        loop=_loop,
    )

    # Set API dependencies (includes orchestrator)
    set_dependencies(gnss_reader, gnss_state, orchestrator=orchestrator)
    logger.info("API dependencies configured")

    # Initialize WebSocket handler (pass orchestrator for initial_state + broadcast)
    ws_handler = WebSocketHandler(sio, gnss_reader, gnss_state, orchestrator=orchestrator, loop=_loop)
    logger.info("WebSocket handler initialized")

    # Mount Socket.IO at /ws
    app.mount(Config.WS_PATH, socketio.ASGIApp(sio))
    logger.info(f"Socket.IO mounted at {Config.WS_PATH}")

    # Start GNSS reader thread
    gnss_reader.start()
    logger.info("GNSS reader thread started")

    # Start autoflow orchestrator thread (boot-check runs inside start())
    orchestrator.start()

    # Start broadcast task
    broadcast_task = asyncio.create_task(broadcast_loop())
    logger.info("Broadcast loop started")

    # Setup signal handlers
    setup_signal_handlers()

    logger.info("=" * 60)
    logger.info("GNSS FastAPI Backend Ready")
    logger.info(f"HTTP API: http://{Config.HOST}:{Config.PORT}")
    logger.info(f"WebSocket: ws://{Config.HOST}:{Config.PORT}{Config.WS_PATH}")
    logger.info(f"Serial Port: {Config.SERIAL_PORT}@{Config.SERIAL_BAUDRATE}")
    logger.info("=" * 60)

    # [BOOT] config summary
    logger.info("[BOOT] Config values:")
    logger.info(f"[BOOT]   serial:  port={Config.SERIAL_PORT}  baud={Config.SERIAL_BAUDRATE}")
    logger.info(f"[BOOT]   survey:  min_duration={Config.SURVEY_MIN_DURATION}s  accuracy={Config.SURVEY_ACCURACY_THRESHOLD}m")
    logger.info(f"[BOOT]   rtcm:    type={Config.RTCM_MSM_TYPE}  interval={Config.RTCM_MESSAGE_INTERVAL}ms")
    logger.info(f"[BOOT]   ntrip:   enabled={Config.NTRIP_ENABLED}  host={Config.NTRIP_HOST or '(not set)'}  mount={Config.NTRIP_MOUNTPOINT or '(not set)'}")

    yield

    # Shutdown
    logger.info("Shutting down GNSS FastAPI Backend...")

    # Cancel broadcast task
    broadcast_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass

    # Stop autoflow orchestrator (stops NTRIP client too)
    if orchestrator:
        orchestrator.stop(timeout=10.0)
        logger.info("AutoFlow orchestrator stopped")

    # Stop GNSS reader
    if gnss_reader:
        gnss_reader.stop(timeout=5.0)
        logger.info("GNSS reader stopped")

    # Socket.IO clients are disconnected automatically by uvicorn ASGI shutdown

    logger.info("Shutdown complete")


async def broadcast_loop() -> None:
    """
    Background task to broadcast GNSS data to WebSocket clients.

    Runs continuously, broadcasting position, survey, and status
    updates to connected clients at regular intervals.
    """
    if ws_handler is None:
        return

    broadcast_interval = 1.0  # seconds

    try:
        while True:
            await asyncio.sleep(broadcast_interval)

            # Broadcast all data types
            try:
                await ws_handler.broadcast_all("default")
            except Exception as e:
                logger.error(f"Error in broadcast: {e}")

    except asyncio.CancelledError:
        logger.debug("Broadcast loop cancelled")
        raise


def setup_signal_handlers() -> None:
    """No-op: uvicorn manages SIGTERM/SIGINT itself for clean shutdown."""
    pass




# =============================================================================
# Plain WebSocket /ws/status  (frontend compatibility — no socket.io client needed)
# =============================================================================

_ws_status_clients: list["WebSocket"] = []


async def _ws_status_sender(ws: "WebSocket") -> None:
    """Send status_update JSON every 1.5s to a single plain WS client."""
    try:
        while True:
            await asyncio.sleep(1.5)
            if gnss_state is None or gnss_reader is None:
                continue
            s = gnss_state.to_dict()
            import time as _time
            
            # Build AutoFlow status fields
            autoflow_data = None
            location_change_pending = {
                "active": False,
                "distance_metres": None,
                "auto_resurvey_in_seconds": None,
            }
            
            if orchestrator is not None:
                try:
                    orch_status = orchestrator.get_status()
                    autoflow_data = {
                        "state": orch_status.get("state"),
                        "enabled": orch_status.get("enabled", False),
                        "last_error": orch_status.get("last_error"),
                        "stuck_retries": orch_status.get("stuck_retries", 0),
                        "survey_elapsed": orch_status.get("survey_elapsed"),
                    }
                    
                    # If state is AWAITING_CONFIRM, mark location_change_pending as active
                    if orch_status.get("state") == "AWAITING_CONFIRM":
                        location_change_pending["active"] = True
                        # Note: distance and countdown not persisted; client should listen to
                        # Socket.IO location_changed event for full details
                except Exception as e:
                    logger.warning(f"[WS/status] Error getting orchestrator status: {e}")
                    autoflow_data = {
                        "state": None,
                        "enabled": False,
                        "last_error": str(e),
                        "stuck_retries": 0,
                        "survey_elapsed": None,
                    }
            else:
                # Orchestrator not ready yet (startup phase)
                autoflow_data = {
                    "state": None,
                    "enabled": False,
                    "last_error": None,
                    "stuck_retries": 0,
                    "survey_elapsed": None,
                }
            
            # Build base position fields from gnss_state.base_reference
            br = s.get("base_reference", {})
            base_position = {
                "saved": bool(br.get("ecef_x") is not None),
                "source": br.get("source"),
                "ecef_x": br.get("ecef_x"),
                "ecef_y": br.get("ecef_y"),
                "ecef_z": br.get("ecef_z"),
                "accuracy": br.get("fixed_pos_acc"),
                "surveyed_at": br.get("timestamp"),
            }
            
            # LoRa status
            lora_status = {}
            if orchestrator:
                try:
                    lora_status = orchestrator.get_lora_status()
                except Exception:
                    lora_status = {"enabled": False, "connected": False}

            payload = {
                "type": "status_update",
                "timestamp": _time.time(),
                "gnss": {
                    "connected": gnss_reader.is_connected,
                    "fix_type": {0:"No Fix",1:"DR Only",2:"2D Fix",3:"3D Fix",
                                 4:"GNSS+DR",5:"RTK Float",6:"RTK Fixed"}.get(
                                     s["position"]["fix_type"], "Unknown"),
                    "latitude": s["position"]["latitude"],
                    "longitude": s["position"]["longitude"],
                    "altitude_msl": s["position"]["altitude"],
                    "num_satellites": s["position"]["num_satellites"],
                    "horizontal_accuracy": s["position"]["accuracy"],
                },
                "survey": {
                    "active": s["survey"]["active"],
                    "valid": s["survey"]["valid"],
                    "progress_seconds": s["survey"]["observation_time"],
                    "accuracy_m": s["survey"]["mean_accuracy"],
                    "observations": s["survey"]["observation_time"],
                    "mean_x_m": s["survey"].get("ecef_x", 0.0),
                    "mean_y_m": s["survey"].get("ecef_y", 0.0),
                    "mean_z_m": s["survey"].get("ecef_z", 0.0),
                },
                "rtcm": {
                    "enabled": s["rtcm"]["enabled"],
                    "total_messages": s["rtcm"]["total_messages_sent"],
                    "total_bytes": 0,
                    "data_rate_bps": s["rtcm"]["data_rate"],
                    "message_counts": s["rtcm"]["message_counts"],
                },
                "ntrip": {
                    "enabled": s["ntrip"]["enabled"],
                    "connected": s["ntrip"]["connected"],
                    "host": s["ntrip"]["host"],
                    "port": s["ntrip"]["port"],
                    "mountpoint": s["ntrip"]["mountpoint"],
                    "bytes_sent": s["ntrip"]["bytes_sent"],
                    "uptime_seconds": s["ntrip"]["uptime"],
                },
                "autoflow": autoflow_data,
                "base_position": base_position,
                "location_change_pending": location_change_pending,
                "lora": lora_status,
            }
            import json as _json
            await ws.send_text(_json.dumps(payload))
    except (WebSocketDisconnect, Exception):
        pass

# Create FastAPI application
app = FastAPI(
    title="GNSS FastAPI Backend",
    description="""
## GNSS Receiver Management API

A FastAPI-based backend service for managing u-blox GNSS receivers
using the UBX protocol via pyubx2.

### Features

* **Real-time Position Data**: Stream NAV-PVT data via WebSocket
* **Survey-in Control**: Start/stop survey-in mode for base stations
* **RTCM Configuration**: Enable/disable RTCM3 message output
* **Base Station Mode**: Configure survey or fixed base station modes
* **Status Monitoring**: Monitor receiver status, errors, and statistics

### WebSocket API

Connect to `/ws` for real-time data streaming:

```python
import socketio

sio = socketio.Client()

@sio.event
def connect():
    print("Connected to GNSS backend")

@sio.on("gnss_data")
def on_gnss_data(data):
    print(f"Received: {data}")

sio.connect("ws://localhost:8000/ws")
```

### REST API

Use `/api/v1/*` endpoints for command and control.
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if Config.WS_CORS_ORIGINS == "*" else Config.WS_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming HTTP requests with method, path, and body."""
    body_str = ""
    if request.method in ("POST", "PUT", "PATCH"):
        body_bytes = await request.body()
        if body_bytes:
            try:
                body_str = f" body={json.loads(body_bytes)}"
            except Exception:
                body_str = f" body={body_bytes.decode(errors='replace')}"
    logger.info(f"[API] --> {request.method} {request.url.path}{body_str}")
    return await call_next(request)


# =============================================================================
# HTTP Endpoints
# =============================================================================


@app.get("/", tags=["Root"])
async def root() -> dict:
    """
    Root endpoint with API information.

    Returns basic information about the API and available endpoints.
    """
    return {
        "name": "GNSS FastAPI Backend",
        "version": "1.0.0",
        "description": "GNSS receiver management using UBX protocol",
        "endpoints": {
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
            "api": "/api/v1",
            "websocket": Config.WS_PATH,
        },
    }


@app.get("/health", tags=["Health"])
async def health_check() -> JSONResponse:
    """
    Health check endpoint.

    Returns the health status of the backend service with proper HTTP codes:
      healthy   → 200
      degraded  → 207
      unhealthy → 503
    """
    if gnss_reader is None:
        status = "unhealthy"
        code = 503
    elif not gnss_reader.is_running:
        status = "degraded"
        code = 207
    elif not gnss_reader.is_connected:
        status = "degraded"
        code = 207
    else:
        status = "healthy"
        code = 200

    return JSONResponse(
        status_code=code,
        content={
            "status": status,
            "reader_running": gnss_reader.is_running if gnss_reader else False,
            "reader_connected": gnss_reader.is_connected if gnss_reader else False,
        },
    )


@app.get("/info", tags=["Info"])
async def get_info() -> dict:
    """
    Get application information.

    Returns configuration and runtime information about the backend.
    """
    info = {
        "config": {
            "serial_port": Config.SERIAL_PORT,
            "serial_baudrate": Config.SERIAL_BAUDRATE,
            "websocket_path": Config.WS_PATH,
            "debug": Config.DEBUG,
        },
    }

    if gnss_reader:
        info["reader"] = gnss_reader.get_status()

    if gnss_state:
        info["state"] = gnss_state.to_dict()

    return info


# =============================================================================
# Frontend Compatibility Aliases (Root-Level)
# =============================================================================

@app.get("/survey", tags=["Aliases"])
async def survey_alias() -> dict:
    """
    Survey-in status alias.

    Mirrors /api/v1/status/survey for frontend compatibility.
    Returns current survey-in progress, accuracy, and ECEF coordinates.
    """
    if gnss_state is None:
        return {
            "active": False,
            "valid": False,
            "in_progress": False,
            "progress": 0,
            "accuracy": 0.0,
            "observation_time": 0,
            "mean_accuracy": 0.0,
            "ecef_x": 0.0,
            "ecef_y": 0.0,
            "ecef_z": 0.0,
            "timestamp": None,
        }
    
    survey = gnss_state.survey
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


@app.get("/rtcm", tags=["Aliases"])
async def rtcm_alias() -> dict:
    """
    RTCM status alias.

    Mirrors /api/v1/status/rtcm for frontend compatibility.
    Returns RTCM output status, message counts, and data rates.
    """
    if gnss_state is None:
        return {
            "enabled": False,
            "msm_type": "",
            "message_counts": {},
            "data_rate": 0.0,
            "total_messages_sent": 0,
            "last_message_time": None,
        }
    
    rtcm = gnss_state.rtcm
    
    # Get live NTRIP status if orchestrator available
    live_ntrip_data = None
    if orchestrator is not None:
        try:
            status = orchestrator.get_status()
            live_ntrip_data = status.get("ntrip")
        except Exception:
            pass
    
    frames = live_ntrip_data.get("frames_sent", 0) if live_ntrip_data else rtcm.total_messages_sent
    data_rate = live_ntrip_data.get("data_rate_bps", rtcm.data_rate) if live_ntrip_data else rtcm.data_rate

    return {
        "enabled": rtcm.enabled,
        "msm_type": rtcm.msm_type,
        "message_counts": rtcm.message_counts,
        "data_rate": data_rate,
        "total_messages_sent": frames,
        "last_message_time": rtcm.last_message_time,
    }


@app.get("/ntrip", tags=["Aliases"])
async def ntrip_alias() -> dict:
    """
    NTRIP client status alias.

    Mirrors /api/v1/status/ntrip for frontend compatibility.
    Returns NTRIP connection state, host/port, bytes transferred, and uptime.
    """
    if gnss_state is None:
        return {
            "enabled": False,
            "connected": False,
            "host": "",
            "port": 0,
            "mountpoint": "",
            "bytes_sent": 0,
            "bytes_received": 0,
            "uptime": 0.0,
            "error_message": None,
        }
    
    # Get live NTRIP status from orchestrator if available
    if orchestrator is not None:
        try:
            status = orchestrator.get_status()
            live_ntrip = status.get("ntrip")
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
                }
        except Exception:
            pass
    
    ntrip = gnss_state.ntrip
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


@app.websocket("/ws/status")
async def ws_status_endpoint(websocket: WebSocket):
    """
    Plain WebSocket status feed — sends status_update JSON every 1.5s.
    Frontend compat: no socket.io-client required.
    """
    await websocket.accept()
    _ws_status_clients.append(websocket)
    logger.info(f"[WS/status] Client connected: {websocket.client}")
    try:
        sender_task = asyncio.create_task(_ws_status_sender(websocket))
        # Keep alive until client disconnects
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                pass  # no message from client, that's fine
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        sender_task.cancel()
        if websocket in _ws_status_clients:
            _ws_status_clients.remove(websocket)
        logger.info(f"[WS/status] Client disconnected: {websocket.client}")


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> None:
    """
    Main entry point for running the server directly.

    Starts uvicorn server with the FastAPI application.
    """
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=Config.DEBUG,
        log_level=Config.LOG_LEVEL.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
