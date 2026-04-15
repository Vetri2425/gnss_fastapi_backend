"""
Threaded UBX Reader.

Implements a threaded reader pattern for continuous UBX message reception
from GNSS receivers using pyubx2. Based on pyubx2/examples/ubxpoller.py.
"""

import logging
import queue
import threading
import time
from typing import Callable, Optional

import serial
from pyubx2 import GET, RTCM3_PROTOCOL, UBX_PROTOCOL, UBXReader

from app.config import Config
from app.gnss.parser import GNSSParser
from app.gnss.state import GNSSState

logger = logging.getLogger(__name__)


class GNSSReader:
    """
    Threaded GNSS UBX message reader.

    Runs a background thread that continuously reads UBX messages from
    a serial port and queues them for processing. Implements thread-safe
    serial access with separate queues for inbound and outbound data.

    Attributes:
        state: Shared GNSS state object
        inbound_queue: Queue for received messages
        outbound_queue: Queue for commands to send
        reader_thread: Background reader thread
    """

    def __init__(
        self,
        state: GNSSState,
        port: str = Config.SERIAL_PORT,
        baudrate: int = Config.SERIAL_BAUDRATE,
        timeout: float = Config.SERIAL_TIMEOUT,
        poll_interval: float = Config.UBX_POLL_INTERVAL,
        queue_maxsize: int = Config.UBX_QUEUE_MAXSIZE,
    ):
        """
        Initialize GNSS reader.

        Args:
            state: GNSSState object for storing parsed data
            port: Serial port path (e.g., "/dev/ttyUSB0" or "COM3")
            baudrate: Serial baud rate (default: 9600)
            timeout: Serial read timeout in seconds
            poll_interval: Time between read iterations in seconds
            queue_maxsize: Maximum queue size (0 = unlimited)
        """
        self.state = state
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.poll_interval = poll_interval

        # Thread-safe queues for message passing
        self.inbound_queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self.outbound_queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)

        # Serial connection
        self.serial: Optional[serial.Serial] = None
        self.ubx_reader: Optional[UBXReader] = None

        # Thread control
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = Config.SERIAL_MAX_RECONNECT_ATTEMPTS
        self._reconnect_delay = Config.SERIAL_RECONNECT_DELAY

        # Statistics
        self._messages_read = 0
        self._parse_errors = 0
        self._last_message_time: Optional[float] = None

        # RTCM3 callbacks — named list, supports multiple simultaneous consumers (e.g. ntrip, lora)
        self._rtcm_callbacks: list = []
        self._rtcm_lock = threading.Lock()

        # ACK tracking for CFG-VALSET style commands that should be confirmed
        self._ack_event = threading.Event()
        self._ack_lock = threading.Lock()
        self._last_ack_result: Optional[bool] = None

        # Serial connection callbacks — set by WebSocket handler
        self._on_serial_connected: Optional[Callable[[str, int], None]] = None
        self._on_serial_disconnected: Optional[Callable[[str], None]] = None
        self._callback_lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """Check if reader thread is running."""
        return self._reader_thread is not None and self._reader_thread.is_alive()

    @property
    def is_connected(self) -> bool:
        """Check if serial connection is open."""
        return self.serial is not None and self.serial.is_open

    @property
    def messages_read(self) -> int:
        """Get total number of messages read."""
        return self._messages_read

    @property
    def parse_errors(self) -> int:
        """Get total number of parse errors."""
        return self._parse_errors

    def start(self) -> None:
        """
        Start the background reader thread.

        Creates and starts a daemon thread that continuously reads
        UBX messages from the serial port.
        """
        if self.is_running:
            logger.warning("GNSS reader thread is already running")
            return

        logger.info(f"Starting GNSS reader thread on {self.port}@{self.baudrate}")

        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="GNSSReader",
            daemon=True,
        )
        self._reader_thread.start()

        logger.info("GNSS reader thread started")

    def stop(self, timeout: float = 5.0) -> None:
        """
        Stop the background reader thread.

        Args:
            timeout: Maximum time to wait for thread to stop
        """
        if not self.is_running:
            logger.debug("GNSS reader thread is not running")
            return

        logger.info("Stopping GNSS reader thread...")

        # Signal thread to stop
        self._stop_event.set()

        # Wait for thread to finish
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=timeout)
            if self._reader_thread.is_alive():
                logger.warning("GNSS reader thread did not stop gracefully")

        # Close serial connection
        self._close_serial()

        logger.info("GNSS reader thread stopped")

    def set_rtcm_callback(self, name: str, callback) -> None:
        """
        Register a named RTCM callback. Thread-safe.
        name: unique identifier (e.g. 'ntrip', 'lora')
        callback: callable(raw_bytes) or None to remove
        """
        with self._rtcm_lock:
            # Remove existing entry with same name
            self._rtcm_callbacks = [
                (n, cb) for n, cb in self._rtcm_callbacks if n != name
            ]
            if callback is not None:
                self._rtcm_callbacks.append((name, callback))
        logger.info(
            f"[READER] RTCM callback {'registered' if callback else 'removed'}: {name}"
        )

    def remove_rtcm_callback(self, name: str) -> None:
        """Remove a named RTCM callback. Thread-safe."""
        with self._rtcm_lock:
            self._rtcm_callbacks = [
                (n, cb) for n, cb in self._rtcm_callbacks if n != name
            ]
        logger.info(f"[READER] RTCM callback removed: {name}")

    def set_serial_connected_callback(self, callback: Optional[Callable[[str, int], None]]) -> None:
        """
        Register (or clear) a callback for serial connection events.
        Called when serial port opens with (port, baudrate).
        Thread-safe.
        """
        with self._callback_lock:
            self._on_serial_connected = callback

    def set_serial_disconnected_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """
        Register (or clear) a callback for serial disconnection events.
        Called when serial port closes with (reason).
        Thread-safe.
        """
        with self._callback_lock:
            self._on_serial_disconnected = callback

    def send_command(self, command) -> None:
        """
        Queue a command to be sent to the GNSS receiver.

        Args:
            command: UBXMessage command to send
        """
        try:
            self.outbound_queue.put_nowait(command)
            logger.debug(f"Command queued: {command.msg_id}")
        except queue.Full:
            logger.warning("Outbound queue is full, command dropped")

    def send_command_and_wait_ack(self, command, timeout: float = 6.0) -> Optional[bool]:
        """
        Queue a command and wait for the next ACK-ACK / ACK-NAK seen by the reader thread.

        Returns:
            True on ACK-ACK, False on ACK-NAK, None on timeout or queueing failure.
        """
        with self._ack_lock:
            self._last_ack_result = None
            self._ack_event.clear()

        try:
            self.outbound_queue.put_nowait(command)
            logger.debug(f"ACK-wait command queued: {command.msg_id}")
        except queue.Full:
            logger.warning("Outbound queue is full, ACK-wait command dropped")
            return None

        if not self._ack_event.wait(timeout=timeout):
            logger.warning(f"Timed out waiting for ACK for {command.msg_id}")
            return None

        with self._ack_lock:
            return self._last_ack_result

    def get_message(self, timeout: float = 0.1) -> Optional[dict]:
        """
        Get a parsed message from the inbound queue.

        Args:
            timeout: Time to wait for a message

        Returns:
            Parsed message dictionary or None if timeout
        """
        try:
            return self.inbound_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _reader_loop(self) -> None:
        """
        Main reader loop running in background thread.

        Continuously reads UBX messages from serial port, parses them,
        updates state, and queues messages for consumers.
        """
        logger.info("Reader loop started")

        while not self._stop_event.is_set():
            try:
                # Ensure serial connection
                if not self.is_connected:
                    self._connect_serial()

                if self.serial is None or self.ubx_reader is None:
                    # Connection failed, wait and retry
                    time.sleep(self._reconnect_delay)
                    continue

                # Read and process messages
                self._process_messages()

                # Send queued commands
                self._send_queued_commands()

                # Small delay to prevent CPU spinning
                time.sleep(self.poll_interval)

            except KeyboardInterrupt:
                logger.info("Reader loop interrupted by user")
                break
            except Exception as e:
                logger.error(f"Error in reader loop: {e}")
                self._handle_reader_error(e)
                time.sleep(self._reconnect_delay)

        logger.info("Reader loop exited")

    def _connect_serial(self) -> None:
        """
        Establish serial connection to GNSS receiver.

        Attempts to open serial port and create UBX reader.
        Updates receiver state on success/failure.
        """
        try:
            logger.info(f"Connecting to {self.port}@{self.baudrate}...")

            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                exclusive=True,
            )

            self.ubx_reader = UBXReader(
                self.serial,
                msgmode=GET,
                protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL,
            )

            # Update receiver state
            self.state.receiver.connected = True
            self.state.receiver.serial_port = self.port
            self.state.receiver.baudrate = self.baudrate
            self._reconnect_attempts = 0

            # Fire connected callback
            with self._callback_lock:
                cb = self._on_serial_connected
            if cb is not None:
                try:
                    cb(self.port, self.baudrate)
                except Exception:
                    pass

            logger.info(f"[SERIAL] Connected: {self.port}@{self.baudrate}")

        except serial.SerialException as e:
            logger.error(f"[SERIAL] Connection failed: {e}")
            self._reconnect_attempts += 1
            self._update_receiver_disconnected(str(e))

        except PermissionError as e:
            logger.error(f"[SERIAL] Permission denied: {e}")
            self._reconnect_attempts += 1
            self._update_receiver_disconnected(f"Permission denied: {e}")

        except Exception as e:
            logger.error(f"Unexpected error connecting to serial: {e}")
            self._reconnect_attempts += 1
            self._update_receiver_disconnected(str(e))

    def _close_serial(self) -> None:
        """Close serial connection and clean up."""
        if self.ubx_reader is not None:
            self.ubx_reader = None

        if self.serial is not None:
            try:
                if self.serial.is_open:
                    self.serial.close()
                    logger.debug("Serial port closed")
            except Exception as e:
                logger.error(f"Error closing serial port: {e}")
            finally:
                self.serial = None

        # Fire disconnected callback
        with self._callback_lock:
            cb = self._on_serial_disconnected
        if cb is not None:
            try:
                cb("Serial connection closed")
            except Exception:
                pass

        self._update_receiver_disconnected("Serial connection closed")

    def _update_receiver_disconnected(self, reason: str) -> None:
        """
        Update receiver state to disconnected.

        Args:
            reason: Reason for disconnection
        """
        self.state.receiver.connected = False
        self.state.increment_error_count()
        logger.warning(f"Receiver disconnected: {reason}")

        if self._reconnect_attempts >= self._max_reconnect_attempts:
            logger.warning(
                f"[READER] {self._reconnect_attempts} reconnect attempts failed. "
                f"Will keep retrying every {self._reconnect_delay}s indefinitely."
            )
            self._reconnect_attempts = 0  # Reset so retries continue forever

    def _process_messages(self) -> None:
        """
        Process available UBX messages from serial port.

        Reads all available messages, parses them, updates state,
        and queues for consumers.
        """
        if self.ubx_reader is None:
            return

        try:
            # UBXReader.read() returns (raw_bytes, parsed_message)
            raw_message = self.ubx_reader.read()

            if raw_message is None:
                # Heartbeat watchdog — detect silent disconnect
                # If connected but no message for 30s, force reconnect
                if self._last_message_time > 0:
                    silent_secs = time.time() - self._last_message_time
                    if silent_secs > 30.0:
                        logger.warning(
                            f"[READER] No messages for {silent_secs:.0f}s "
                            f"— forcing reconnect (silent disconnect)"
                        )
                        self._close_serial()
                return

            # Unpack tuple — raw_bytes is always index 0
            if isinstance(raw_message, tuple) and len(raw_message) >= 2:
                raw_bytes, msg = raw_message[0], raw_message[1]
            else:
                raw_bytes, msg = None, raw_message

            # Forward raw RTCM3 frames to NTRIP push client (sync byte 0xD3)
            if raw_bytes and len(raw_bytes) > 0 and raw_bytes[0] == 0xD3:
                # Parse 12-bit message type: bits [3*8+7..3*8+0] + [4*8+7..4*8+4]
                if len(raw_bytes) >= 5:
                    msg_type = (raw_bytes[3] << 4) | (raw_bytes[4] >> 4)
                    self.state.increment_rtcm_message_type(msg_type)
                with self._rtcm_lock:
                    callbacks = list(self._rtcm_callbacks)
                for name, cb in callbacks:
                    try:
                        cb(raw_bytes)
                    except Exception as e:
                        logger.error(f"[READER] RTCM callback '{name}' error: {e}")
                return  # RTCM3 — no further UBX parsing needed

            # Parse the UBX message
            parsed = self._parse_message(msg)

            if parsed is not None:
                self._messages_read += 1
                self._last_message_time = time.time()

                # Queue for consumers (non-critical, drop silently if full)
                if not self.inbound_queue.full():
                    self.inbound_queue.put_nowait(parsed)

        except serial.SerialException as e:
            logger.error(f"Serial error reading message: {e}")
            self._close_serial()

        except Exception as e:
            self._parse_errors += 1
            logger.error(f"Error processing message: {e}")

    def _parse_message(self, msg) -> Optional[dict]:
        """
        Parse a UBX message and update state.

        Args:
            msg: UBXMessage object

        Returns:
            Parsed message dictionary or None
        """
        if msg is None:
            return None

        try:
            msg_id = msg.identity  # always a str e.g. "NAV-PVT" for all msg types

            # Parse based on message type
            if msg_id == "NAV-SVIN":
                parsed = GNSSParser.parse_nav_svin(
                    msg,
                    min_duration=Config.SURVEY_MIN_DURATION,
                )
                self._update_survey_state(parsed)

            elif msg_id == "NAV-PVT":
                parsed = GNSSParser.parse_nav_pvt(msg)
                self._update_position_state(parsed)

            elif msg_id == "NAV-SAT":
                parsed = GNSSParser.parse_nav_sat(msg)

            elif msg_id == "ACK-ACK":
                parsed = GNSSParser.parse_ack(msg)
                self.state.increment_ack_count()
                with self._ack_lock:
                    self._last_ack_result = True
                    self._ack_event.set()
                logger.debug(f"ACK received for {msg_id}")

            elif msg_id == "ACK-NAK":
                parsed = GNSSParser.parse_ack(msg)
                self.state.increment_nak_count()
                with self._ack_lock:
                    self._last_ack_result = False
                    self._ack_event.set()
                logger.warning(f"NAK received for {msg_id}")

            elif msg_id.startswith("INF-"):
                parsed = GNSSParser.parse_inf(msg)

            else:
                # Generic message
                parsed = GNSSParser.parse_message(msg)

            # Add raw message to state
            self.state.add_raw_message(parsed)

            return parsed

        except Exception as e:
            self._parse_errors += 1
            logger.error(f"Error parsing message: {e}")
            return None

    def _update_position_state(self, pvt_data: dict) -> None:
        """
        Update position state from NAV-PVT data.

        Args:
            pvt_data: Parsed NAV-PVT data
        """
        self.state.update_position(
            latitude=pvt_data.get("latitude", 0.0),
            longitude=pvt_data.get("longitude", 0.0),
            altitude=pvt_data.get("altitude", 0.0),
            accuracy=pvt_data.get("accuracy", 0.0),
            vertical_accuracy=pvt_data.get("vertical_accuracy", 0.0),
            fix_type=pvt_data.get("fix_type", 0),
            num_satellites=pvt_data.get("num_satellites", 0),
            hdop=pvt_data.get("hdop", 0.0),
            vdop=pvt_data.get("vdop", 0.0),
            pdop=pvt_data.get("pdop", 0.0),
            velocity_north=pvt_data.get("velocity_north", 0.0),
            velocity_east=pvt_data.get("velocity_east", 0.0),
            velocity_down=pvt_data.get("velocity_down", 0.0),
            ground_speed=pvt_data.get("ground_speed", 0.0),
            heading=pvt_data.get("heading", 0.0),
        )

    def _update_survey_state(self, svin_data: dict) -> None:
        """
        Update survey state from NAV-SVIN data.

        Args:
            svin_data: Parsed NAV-SVIN data
        """
        active = svin_data.get("active", False)
        valid = svin_data.get("valid", False)
        obs = svin_data.get("observation_time", 0)
        acc = svin_data.get("mean_accuracy", svin_data.get("accuracy", 0.0))
        ecef_x = svin_data.get("ecef_x", 0.0)
        ecef_y = svin_data.get("ecef_y", 0.0)
        ecef_z = svin_data.get("ecef_z", 0.0)

        # Once survey-in completes, some receivers report an empty NAV-SVIN
        # snapshot (inactive, invalid, zeroed fields). Preserve the last
        # meaningful survey result so REST clients can still read the final
        # surveyed coordinates after AutoFlow advances into fixed/streaming mode.
        existing = self.state.survey
        is_empty_reset = (
            not active
            and not valid
            and obs == 0
            and ecef_x == 0.0
            and ecef_y == 0.0
            and ecef_z == 0.0
        )
        has_existing_result = (
            existing.valid
            or existing.observation_time > 0
            or any(abs(value) > 0.0 for value in (existing.ecef_x, existing.ecef_y, existing.ecef_z))
        )
        if is_empty_reset and has_existing_result:
            logger.debug("[POLL] Ignoring empty NAV-SVIN reset; keeping last survey result")
            return

        self.state.update_survey(
            active=active,
            valid=valid,
            in_progress=svin_data.get("in_progress", False),
            progress=svin_data.get("progress", 0),
            accuracy=svin_data.get("accuracy", 0.0),
            observation_time=obs,
            ecef_x=ecef_x,
            ecef_y=ecef_y,
            ecef_z=ecef_z,
            mean_accuracy=acc,
        )

        if valid:
            logger.info(
                f"[POLL] Survey VALID!  acc={acc:.3f}m  obs={obs}s"
                f"  ECEF: X={ecef_x:.2f}  Y={ecef_y:.2f}  Z={ecef_z:.2f}"
            )
        elif active:
            logger.info(
                f"[POLL] dur={obs}s  acc={acc:.3f}m  active={active}  valid={valid}"
            )

    def _send_queued_commands(self) -> None:
        """Send any queued commands to the GNSS receiver."""
        while not self.outbound_queue.empty():
            try:
                command = self.outbound_queue.get_nowait()
                self._send_command(command)
            except queue.Empty:
                break

    def _send_command(self, command) -> bool:
        """
        Send a command to the GNSS receiver.

        Args:
            command: UBXMessage command to send

        Returns:
            True if command was sent successfully
        """
        if self.serial is None or not self.serial.is_open:
            logger.warning("Cannot send command: serial not connected")
            return False

        try:
            # Serialize and send command
            raw = command.serialize()
            self.serial.write(raw)
            self.serial.flush()

            logger.debug(f"Command sent: {command.msg_id} ({len(raw)} bytes)")
            return True

        except serial.SerialException as e:
            logger.error(f"Serial error sending command: {e}")
            self._close_serial()
            return False

        except Exception as e:
            logger.error(f"Error sending command: {e}")
            return False

    def _handle_reader_error(self, error: Exception) -> None:
        """
        Handle errors in the reader loop.

        Args:
            error: Exception that occurred
        """
        self.state.increment_error_count()

        if isinstance(error, serial.SerialException):
            logger.error(f"Serial exception in reader: {error}")
            self._close_serial()
        elif isinstance(error, PermissionError):
            logger.error(f"Permission error in reader: {error}")
            self._close_serial()
        else:
            logger.error(f"Unhandled error in reader: {error}")

    def get_status(self) -> dict:
        """
        Get reader status information.

        Returns:
            Dictionary with reader status and statistics
        """
        return {
            "is_running": self.is_running,
            "is_connected": self.is_connected,
            "port": self.port,
            "baudrate": self.baudrate,
            "messages_read": self._messages_read,
            "parse_errors": self._parse_errors,
            "reconnect_attempts": self._reconnect_attempts,
            "max_reconnect_attempts": self._max_reconnect_attempts,
            "last_message_time": self._last_message_time,
            "inbound_queue_size": self.inbound_queue.qsize(),
            "outbound_queue_size": self.outbound_queue.qsize(),
        }
