"""
WebSocket Event Handlers.

Socket.IO event handlers for real-time GNSS data streaming,
command execution, and room management for multiple rovers.
"""

import asyncio
import concurrent.futures
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from socketio import AsyncServer

if TYPE_CHECKING:
    from app.gnss.autoflow import AutoflowOrchestrator
    from app.gnss.reader import GNSSReader
    from app.gnss.state import GNSSState

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """
    Socket.IO event handler for GNSS WebSocket connections.

    Manages client connections, disconnections, room subscriptions,
    and real-time data broadcasting for GNSS receivers.

    Attributes:
        sio: Async Socket.IO server instance
        gnss_reader: GNSSReader instance for message access
        gnss_state: GNSSState instance for state access
    """

    def __init__(
        self,
        sio: AsyncServer,
        gnss_reader: "GNSSReader",
        gnss_state: "GNSSState",
        orchestrator: "AutoflowOrchestrator | None" = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.sio = sio
        self.gnss_reader = gnss_reader
        self.gnss_state = gnss_state
        self.orchestrator = orchestrator
        self.loop = loop or asyncio.get_event_loop()

        # Register event handlers
        self._register_handlers()

        # Wire up serial connection callbacks
        self._setup_serial_callbacks()

        logger.info("WebSocket handler initialized")

    def _setup_serial_callbacks(self) -> None:
        """Wire up serial connection callbacks to emit WebSocket events."""
        import asyncio

        def on_serial_connected(port: str, baudrate: int) -> None:
            """Called when serial port opens."""
            if self.loop is None or self.loop.is_closed():
                return
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.sio.emit("serial_connected", {"port": port, "baudrate": baudrate}),
                    self.loop,
                )
                future.add_done_callback(self._on_emit_done)
            except RuntimeError:
                pass

        def on_serial_disconnected(reason: str) -> None:
            """Called when serial port closes."""
            if self.loop is None or self.loop.is_closed():
                return
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.sio.emit("serial_disconnected", {"reason": reason}),
                    self.loop,
                )
                future.add_done_callback(self._on_emit_done)
            except RuntimeError:
                pass

        self.gnss_reader.set_serial_connected_callback(on_serial_connected)
        self.gnss_reader.set_serial_disconnected_callback(on_serial_disconnected)

    @staticmethod
    def _on_emit_done(future: concurrent.futures.Future) -> None:
        """Callback for Socket.IO emit completion."""
        exc = future.exception()
        if exc:
            logger.error(f"WebSocket emit error: {exc}")

    def _register_handlers(self) -> None:
        """Register Socket.IO event handlers."""

        @self.sio.event
        async def connect(sid: str, environ: dict) -> None:
            """
            Handle client connection.

            Args:
                sid: Socket ID
                environ: Connection environment
            """
            logger.info(f"Client connected: {sid}")

            # Send initial state
            await self._send_initial_state(sid)

        @self.sio.event
        async def disconnect(sid: str) -> None:
            """
            Handle client disconnection.

            Args:
                sid: Socket ID
            """
            logger.info(f"Client disconnected: {sid}")

            # Clean up rooms
            rooms = await self.sio.rooms(sid)
            for room in rooms:
                if room != sid:  # Don't leave default room
                    await self.sio.leave_room(sid, room)
                    logger.debug(f"Client {sid} left room: {room}")

        @self.sio.event
        async def join_room(sid: str, room: str) -> None:
            """
            Handle client joining a room.

            Args:
                sid: Socket ID
                room: Room name to join
            """
            await self.sio.enter_room(sid, room)
            logger.info(f"Client {sid} joined room: {room}")

            # Send current state to new room member
            await self._send_room_state(sid, room)

        @self.sio.event
        async def leave_room(sid: str, room: str) -> None:
            """
            Handle client leaving a room.

            Args:
                sid: Socket ID
                room: Room name to leave
            """
            await self.sio.leave_room(sid, room)
            logger.info(f"Client {sid} left room: {room}")

        @self.sio.on("subscribe")
        async def subscribe(sid: str, data: dict) -> None:
            """
            Handle subscription to data streams.

            Args:
                sid: Socket ID
                data: Subscription data {stream: "position"|"survey"|"rtcm"|"all"}
            """
            stream = data.get("stream", "all")
            room = data.get("room", "default")

            # Join room for the stream
            stream_room = f"{room}_{stream}"
            await self.sio.enter_room(sid, stream_room)

            logger.info(f"Client {sid} subscribed to {stream} in room {room}")

            await self.sio.emit(
                "subscribed",
                {"stream": stream, "room": room},
                to=sid,
            )

        @self.sio.on("unsubscribe")
        async def unsubscribe(sid: str, data: dict) -> None:
            """
            Handle unsubscription from data streams.

            Args:
                sid: Socket ID
                data: Unsubscription data {stream: "position"|"survey"|"rtcm"|"all"}
            """
            stream = data.get("stream", "all")
            room = data.get("room", "default")

            stream_room = f"{room}_{stream}"
            await self.sio.leave_room(sid, stream_room)

            logger.info(f"Client {sid} unsubscribed from {stream} in room {room}")

            await self.sio.emit(
                "unsubscribed",
                {"stream": stream, "room": room},
                to=sid,
            )

        @self.sio.on("command")
        async def command(sid: str, data: dict) -> None:
            """
            Handle command execution request.

            Args:
                sid: Socket ID
                data: Command data {type: "survey_start"|"survey_stop"|"rtcm_enable"|...}
            """
            cmd_type = data.get("type")
            params = data.get("params", {})

            logger.info(f"Command request from {sid}: {cmd_type}")

            result = await self._execute_command(cmd_type, params)

            await self.sio.emit(
                "command_response",
                {
                    "type": cmd_type,
                    "success": result["success"],
                    "message": result["message"],
                    "data": result.get("data"),
                },
                to=sid,
            )

        @self.sio.on("get_status")
        async def get_status(sid: str) -> None:
            """
            Handle status request.

            Args:
                sid: Socket ID
            """
            status = self.get_full_status()
            await self.sio.emit("status", status, to=sid)

    async def _send_initial_state(self, sid: str) -> None:
        """Send full initial state to newly connected client."""
        state = self.gnss_state.to_dict()
        state["reader_status"] = self.gnss_reader.get_status()
        if self.orchestrator:
            state["autoflow"] = self.orchestrator.get_status()

        await self.sio.emit("initial_state", state, to=sid)

    async def _send_room_state(self, sid: str, room: str) -> None:
        """
        Send current state to client joining a room.

        Args:
            sid: Socket ID
            room: Room name
        """
        state = self.gnss_state.to_dict()
        state["room"] = room

        await self.sio.emit("room_state", state, to=sid)

    async def _execute_command(
        self, cmd_type: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Execute a GNSS command.

        Args:
            cmd_type: Command type
            params: Command parameters

        Returns:
            Command result dictionary
        """
        from app.gnss.commands import GNSSCommands

        try:
            if cmd_type == "survey_start":
                min_dur = params.get("min_duration", 300)
                acc_limit = params.get("accuracy_limit", 0.10)
                cmd = GNSSCommands.create_survey_start_command(min_dur, acc_limit)
                self.gnss_reader.send_command(cmd)
                return {
                    "success": True,
                    "message": f"Survey start command sent (min_dur={min_dur}s, acc={acc_limit}m)",
                }

            elif cmd_type == "survey_stop":
                cmd = GNSSCommands.create_survey_stop_command()
                self.gnss_reader.send_command(cmd)
                return {"success": True, "message": "Survey stop command sent"}

            elif cmd_type == "rtcm_enable":
                msm_type = params.get("msm_type", "MSM4")
                cmd = GNSSCommands.create_rtcm_enable_command(msm_type)
                self.gnss_reader.send_command(cmd)
                return {
                    "success": True,
                    "message": f"RTCM enable command sent ({msm_type})",
                }

            elif cmd_type == "rtcm_disable":
                cmd = GNSSCommands.create_rtcm_disable_command()
                self.gnss_reader.send_command(cmd)
                return {"success": True, "message": "RTCM disable command sent"}

            elif cmd_type == "poll_svin":
                cmd = GNSSCommands.create_nav_svin_poll_command()
                self.gnss_reader.send_command(cmd)
                return {"success": True, "message": "NAV-SVIN poll sent"}

            elif cmd_type == "poll_pvt":
                cmd = GNSSCommands.create_nav_pvt_poll_command()
                self.gnss_reader.send_command(cmd)
                return {"success": True, "message": "NAV-PVT poll sent"}

            elif cmd_type == "poll_sat":
                cmd = GNSSCommands.create_nav_sat_poll_command()
                self.gnss_reader.send_command(cmd)
                return {"success": True, "message": "NAV-SAT poll sent"}

            elif cmd_type == "base_mode":
                msm_type = params.get("msm_type", "MSM4")
                survey_mode = params.get("survey_mode", True)
                cmd = GNSSCommands.create_base_mode_command(
                    msm_type=msm_type,
                    survey_mode=survey_mode,
                    min_duration=params.get("min_duration", 300),
                    accuracy_limit=params.get("accuracy_limit", 0.10),
                )
                self.gnss_reader.send_command(cmd)
                return {
                    "success": True,
                    "message": f"Base mode command sent ({msm_type}, survey={survey_mode})",
                }

            else:
                return {
                    "success": False,
                    "message": f"Unknown command type: {cmd_type}",
                }

        except Exception as e:
            logger.error(f"Error executing command {cmd_type}: {e}")
            return {
                "success": False,
                "message": f"Command failed: {str(e)}",
            }

    async def broadcast_position(self, room: str = "default") -> None:
        """
        Broadcast position data to all clients.

        Args:
            room: Room to broadcast to
        """
        position = self.gnss_state.position
        data = {
            "type": "position",
            "latitude": position.latitude,
            "longitude": position.longitude,
            "altitude": position.altitude,
            "accuracy": position.accuracy,
            "fix_type": position.fix_type,
            "num_satellites": position.num_satellites,
            "timestamp": position.timestamp.isoformat(),
        }

        await self.sio.emit("gnss_data", data, room=f"{room}_position")
        await self.sio.emit("gnss_data", data, room=f"{room}_all")

    async def broadcast_survey(self, room: str = "default") -> None:
        """
        Broadcast survey data to all clients.

        Args:
            room: Room to broadcast to
        """
        survey = self.gnss_state.survey
        data = {
            "type": "survey",
            "active": survey.active,
            "valid": survey.valid,
            "in_progress": survey.in_progress,
            "progress": survey.progress,
            "accuracy": survey.accuracy,
            "observation_time": survey.observation_time,
            "mean_accuracy": survey.mean_accuracy,
            "timestamp": survey.timestamp.isoformat(),
        }

        await self.sio.emit("gnss_data", data, room=f"{room}_survey")
        await self.sio.emit("gnss_data", data, room=f"{room}_all")

    async def broadcast_rtcm(self, room: str = "default") -> None:
        """
        Broadcast RTCM status to all clients.

        Args:
            room: Room to broadcast to
        """
        rtcm = self.gnss_state.rtcm
        data = {
            "type": "rtcm",
            "enabled": rtcm.enabled,
            "msm_type": rtcm.msm_type,
            "data_rate": rtcm.data_rate,
            "total_messages_sent": rtcm.total_messages_sent,
        }

        await self.sio.emit("gnss_data", data, room=f"{room}_rtcm")
        await self.sio.emit("gnss_data", data, room=f"{room}_all")

    async def broadcast_status(self, room: str = "default") -> None:
        """
        Broadcast full status to all clients.

        Args:
            room: Room to broadcast to
        """
        status = self.get_full_status()
        await self.sio.emit("status", status, room=room)

    def get_full_status(self) -> dict[str, Any]:
        """
        Get full GNSS system status.

        Returns:
            Dictionary with complete status information
        """
        state = self.gnss_state.to_dict()
        state["reader_status"] = self.gnss_reader.get_status()
        state["timestamp"] = datetime.utcnow().isoformat()
        return state

    async def broadcast_autoflow(self) -> None:
        """Broadcast current autoflow state to all clients."""
        if self.orchestrator:
            await self.sio.emit("autoflow_state", self.orchestrator.get_status())

    async def broadcast_lora_status(self) -> None:
        """Broadcast LoRa status to all connected clients."""
        if self.orchestrator:
            status = self.orchestrator.get_lora_status()
            await self.sio.emit("lora_status", status)

    async def broadcast_all(self, room: str = "default") -> None:
        """Broadcast all data types to all clients."""
        await self.broadcast_position(room)
        await self.broadcast_survey(room)
        await self.broadcast_rtcm(room)
        await self.broadcast_autoflow()
        await self.broadcast_lora_status()
