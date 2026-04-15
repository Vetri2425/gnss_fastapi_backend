"""
LoRa RTCM Push Client.

Streams RTCM3 correction bytes from ZED-F9P base station
to rover via Ebyte E22-900T22U LoRa module connected on
USB serial (/dev/ttyUSB0 via CH340).

Architecture:
- Runs in a daemon thread (same pattern as NTRIPPushClient)
- Receives RTCM bytes via put_rtcm() called from reader thread
- Buffers bytes and writes in chunks up to PACKET_SIZE (240 bytes)
- Tracks bytes sent, frames sent, data rate, errors
- Thread-safe status via get_status()
- Clean stop() with no data loss

Hardware:
- Ebyte E22-900T22U, 865.125MHz, Channel 15
- UART baud 115200 via CH340 USB
- Air rate 19.2kbps, Packet size 240 bytes
- Mode: Normal (transparent serial bridge)
"""

import logging
import queue
import threading
import time
from typing import Optional

import serial

from app.config import Config

logger = logging.getLogger(__name__)


class LoRaPushClient:
    """
    Streams RTCM bytes to rover via LoRa serial module.
    Thread-safe. Start with start(), stop with stop().
    """

    def __init__(
        self,
        port: str = None,
        baudrate: int = None,
        packet_size: int = None,
        write_timeout: float = None,
    ):
        self.port = port or Config.LORA_PORT
        self.baudrate = baudrate or Config.LORA_BAUDRATE
        self.packet_size = packet_size or Config.LORA_PACKET_SIZE
        self.write_timeout = write_timeout or Config.LORA_WRITE_TIMEOUT

        # RTCM byte queue — bounded to prevent memory growth
        self._queue: queue.Queue = queue.Queue(maxsize=500)

        # Thread control
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Serial port
        self._serial: Optional[serial.Serial] = None
        self._connected: bool = False

        # Statistics
        self._bytes_sent: int = 0
        self._frames_sent: int = 0
        self._write_errors: int = 0
        self._connect_attempts: int = 0
        self._start_time: Optional[float] = None
        self._last_send_time: Optional[float] = None
        self._bytes_sent_window: list = []  # for data rate calculation
        self._lock = threading.Lock()

        logger.info(
            f"[LORA] LoRaPushClient initialized: "
            f"{self.port}@{self.baudrate} "
            f"packet_size={self.packet_size}B"
        )

    def start(self) -> None:
        """Start the LoRa push thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("[LORA] Already running")
            return
        self._stop_event.clear()
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="LoRaPushClient",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[LORA] Started streaming on {self.port}")

    def stop(self) -> None:
        """Stop the LoRa push thread and close serial port."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        self._close_serial()
        logger.info("[LORA] Stopped")

    def put_rtcm(self, raw_bytes: bytes) -> None:
        """
        Queue RTCM bytes for transmission. Called from reader thread.
        Non-blocking — drops silently if queue is full.
        """
        try:
            self._queue.put_nowait(raw_bytes)
        except queue.Full:
            logger.debug("[LORA] Queue full — RTCM frame dropped")

    def get_status(self) -> dict:
        """Return current status. Thread-safe."""
        with self._lock:
            uptime = (
                time.time() - self._start_time
                if self._start_time else 0.0
            )
            # Calculate data rate over last 10s
            now = time.time()
            self._bytes_sent_window = [
                (t, b) for t, b in self._bytes_sent_window
                if now - t <= 10.0
            ]
            data_rate = (
                sum(b for _, b in self._bytes_sent_window) / 10.0
                if self._bytes_sent_window else 0.0
            )
            return {
                "enabled": True,
                "connected": self._connected,
                "port": self.port,
                "baudrate": self.baudrate,
                "packet_size": self.packet_size,
                "bytes_sent": self._bytes_sent,
                "frames_sent": self._frames_sent,
                "write_errors": self._write_errors,
                "connect_attempts": self._connect_attempts,
                "data_rate_bps": round(data_rate, 1),
                "uptime": round(uptime, 1),
                "last_send_time": self._last_send_time,
                "queue_size": self._queue.qsize(),
            }

    # ── Private ──────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main loop: connect serial, drain queue, write packets."""
        logger.info("[LORA] Run loop started")
        buffer = bytearray()

        while not self._stop_event.is_set():
            # Ensure serial connection
            if not self._connected:
                if not self._connect_serial():
                    self._stop_event.wait(timeout=5.0)
                    continue

            # Drain queue into buffer
            try:
                while True:
                    chunk = self._queue.get_nowait()
                    buffer.extend(chunk)
            except queue.Empty:
                pass

            # Write buffer in packet_size chunks
            while len(buffer) >= self.packet_size:
                packet = bytes(buffer[:self.packet_size])
                buffer = buffer[self.packet_size:]
                self._write_packet(packet)

            # If buffer has data but less than packet_size,
            # wait briefly then flush if no more data coming
            if buffer:
                self._stop_event.wait(timeout=0.05)
                # Try to get more data
                try:
                    while True:
                        chunk = self._queue.get_nowait()
                        buffer.extend(chunk)
                except queue.Empty:
                    pass
                # Flush remaining buffer as partial packet
                if buffer:
                    self._write_packet(bytes(buffer))
                    buffer = bytearray()
            else:
                # Nothing to send — small sleep to prevent CPU spin
                self._stop_event.wait(timeout=0.01)

        # Flush remaining buffer on stop
        if buffer:
            self._write_packet(bytes(buffer))

        logger.info("[LORA] Run loop exited")

    def _connect_serial(self) -> bool:
        """Open serial port to LoRa module."""
        try:
            with self._lock:
                self._connect_attempts += 1
            logger.info(
                f"[LORA] Connecting to {self.port}@{self.baudrate}..."
            )
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1.0,
                write_timeout=self.write_timeout,
                exclusive=True,
            )
            with self._lock:
                self._connected = True
            logger.info(f"[LORA] Connected to {self.port}")
            return True
        except serial.SerialException as e:
            logger.error(f"[LORA] Serial connect failed: {e}")
            with self._lock:
                self._connected = False
            self._serial = None
            return False

    def _close_serial(self) -> None:
        """Close serial port safely."""
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        with self._lock:
            self._connected = False

    def _write_packet(self, packet: bytes) -> None:
        """Write one packet to serial. Handle errors."""
        if not self._serial or not self._connected:
            return
        try:
            self._serial.write(packet)
            self._serial.flush()
            now = time.time()
            with self._lock:
                self._bytes_sent += len(packet)
                self._frames_sent += 1
                self._last_send_time = now
                self._bytes_sent_window.append((now, len(packet)))
            logger.debug(
                f"[LORA] Sent packet {len(packet)}B "
                f"total={self._bytes_sent}B"
            )
        except serial.SerialTimeoutException:
            with self._lock:
                self._write_errors += 1
            logger.warning("[LORA] Write timeout — packet dropped")
        except serial.SerialException as e:
            with self._lock:
                self._write_errors += 1
                self._connected = False
            logger.error(f"[LORA] Write error: {e} — reconnecting")
            self._close_serial()
        except Exception as e:
            with self._lock:
                self._write_errors += 1
            logger.error(f"[LORA] Unexpected write error: {e}")
