"""
NTRIP Base Station Push Client.

Persistent TCP socket that pushes RTCM3 bytes to an NTRIP caster.
  - NTRIP 1.0: SOURCE <password> /<mountpoint>  →  "ICY 200 OK"  →  raw bytes
  - NTRIP 2.0: HTTP POST with chunked transfer encoding

Runs its own background daemon thread. Feed RTCM bytes via put_rtcm()
from any thread (the GNSSReader serial thread).
"""

import base64
import logging
import queue
import random
import select
import socket
import struct
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class NTRIPPushClient:
    """
    Sync-socket NTRIP push client (base station → caster).

    Thread-safe: put_rtcm() is safe to call from the GNSSReader thread.
    Handles reconnection internally with configurable delay.
    """

    def __init__(
        self,
        host: str,
        port: int,
        mountpoint: str,
        password: str,
        username: str = "",
        ntrip_version: int = 1,
        agent: str = "DYX-GNSS/1.0",
        queue_maxsize: int = 500,
        max_retries: int = 0,
        base_delay: float = 5.0,
        max_delay: float = 60.0,
    ):
        self.host = host
        self.port = port
        self.mountpoint = mountpoint.lstrip("/")
        self.password = password
        self.username = username
        self.ntrip_version = ntrip_version
        self.agent = agent

        # Exponential backoff params
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._rtcm_queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._give_up = False
        self._failure_count = 0

        # Connection callbacks — set by autoflow orchestrator
        self._on_connected: Optional[Callable[[], None]] = None
        self._on_disconnected: Optional[Callable[[], None]] = None
        self._callback_lock = threading.Lock()

        # Stats
        self.bytes_sent: int = 0
        self.bytes_received: int = 0
        self.frames_sent: int = 0
        self.connect_attempts: int = 0
        self.last_error: Optional[str] = None
        self._session_attempts: int = 0
        self._connected_since: float = 0.0
        self._first_frame_logged: bool = False
        self._last_stats_log: float = 0.0

        # Data rate tracking
        self._bytes_in_window: int = 0
        self._window_start: float = time.monotonic()
        self.data_rate_bps: float = 0.0
        self.last_send_time: float = 0.0
        self.last_receive_time: float = 0.0

        # Cooldown tracking
        self._cooldown_deadline: Optional[float] = None
        self._in_cooldown: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def stale(self) -> bool:
        if not self._connected:
            return False
        return self.last_send_time > 0 and (time.monotonic() - self.last_send_time) > 30.0

    def put_rtcm(self, data: bytes) -> None:
        """
        Queue RTCM3 bytes for sending. Called from GNSSReader serial thread.
        Drops silently when queue is full — next RTCM frame arrives within seconds.
        """
        try:
            self._rtcm_queue.put_nowait(data)
        except queue.Full:
            logger.warning("[NTRIP] RTCM queue full — frame dropped (network can't keep up)")

    def start(self) -> None:
        """Start the push thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="NTRIPPush",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[NTRIP] Push thread started → {self.host}:{self.port}/{self.mountpoint}")

    def stop(self, timeout: float = 5.0) -> None:
        """Signal stop, wait for thread, close socket."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._close_socket()
        logger.info("[NTRIP] Push client stopped")

    def get_status(self) -> dict:
        return {
            "connected": self._connected,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "frames_sent": self.frames_sent,
            "connect_attempts": self.connect_attempts,
            "last_error": self.last_error,
            "host": self.host,
            "port": self.port,
            "mountpoint": self.mountpoint,
            "gave_up": self._give_up,
            "data_rate_bps": self.data_rate_bps,
            "uptime": (time.monotonic() - self._connected_since) if self._connected and self._connected_since else 0.0,
            "stale": self.stale,
            "in_cooldown": self._in_cooldown,
            "cooldown_remaining_seconds": (
                max(0, int(self._cooldown_deadline - time.monotonic()))
                if self._cooldown_deadline and self._in_cooldown
                else None
            ),
        }

    def set_connected_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """
        Register (or clear) a callback for NTRIP connection events.
        Called when connected to caster.
        Thread-safe.
        """
        with self._callback_lock:
            self._on_connected = callback

    def set_disconnected_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """
        Register (or clear) a callback for NTRIP disconnection events.
        Called when disconnected from caster.
        Thread-safe.
        """
        with self._callback_lock:
            self._on_disconnected = callback

    # ── Thread internals ──────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._connect()
                self._session_attempts = 0
                self._failure_count = 0
                self._push_loop()
            except Exception as e:
                self.last_error = str(e)
                self._connected = False
                self._failure_count += 1
                logger.error(f"[NTRIP] {e}")
            finally:
                self._close_socket()

            if self._stop_event.is_set():
                break

            if self.max_retries > 0 and self._failure_count >= self.max_retries:
                # NTRIP max retries reached — enter 10-minute cooldown before retry
                logger.warning(
                    f"[NTRIP] {self._failure_count} consecutive failures. "
                    f"Entering 10-minute cooldown before retry. "
                    f"Base station remains operational — RTCM streaming on serial."
                )
                self._give_up = True  # Signal to AutoFlow that we're in cooldown
                cooldown_secs = 600  # 10 minutes
                self._in_cooldown = True
                self._cooldown_deadline = time.monotonic() + cooldown_secs

                # Wait in 5-second chunks for clean stop() handling
                waited = 0
                while waited < cooldown_secs:
                    if self._stop_event.is_set():
                        return  # Clean exit if stop() called during cooldown
                    time.sleep(5)
                    waited += 5
                
                # After cooldown — reset counters and retry
                logger.info("[NTRIP] Cooldown complete — resetting and retrying connection")
                self._give_up = False
                self._in_cooldown = False
                self._cooldown_deadline = None
                self._failure_count = 0
                self._session_attempts = 0
                # Loop continues — will attempt reconnect
                continue

            # Exponential backoff with ±20% jitter to prevent thundering herd
            base = min(self.base_delay * (2 ** min(self._session_attempts, 6)), self.max_delay)
            delay = base * random.uniform(0.8, 1.2)
            logger.info(f"[NTRIP] Reconnecting in {delay:.1f}s...")
            self._stop_event.wait(timeout=delay)

    def _connect(self) -> None:
        self._session_attempts += 1
        self.connect_attempts += 1
        # Try v2 first (HTTP POST), fall back to v1 (SOURCE method)
        versions = [2, 1] if self.ntrip_version != 2 else [2]

        errors: list[str] = []
        for version in versions:
            logger.info(
                f"[NTRIP] Connecting to {self.host}:{self.port}/{self.mountpoint} "
                f"(NTRIP {version}.0, session attempt {self._session_attempts})"
            )
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=10)
                self._configure_socket(self._sock)
                if version == 2:
                    self._handshake_v2()
                else:
                    self._handshake_v1()
                self.ntrip_version = version
                break
            except Exception as exc:
                errors.append(f"v{version}: {exc}")
                self._close_socket()
                # Try next version, if any
        else:
            # All versions exhausted
            raise ConnectionError("; ".join(errors))

        self._connected = True
        self._connected_since = time.monotonic()
        self.last_error = None
        dropped = self._clear_rtcm_queue()
        if dropped:
            logger.info(f"[NTRIP] Dropped {dropped} queued RTCM frames before resuming stream")
        logger.info(f"[NTRIP] Connected → /{self.mountpoint}")

        # Fire connected callback
        with self._callback_lock:
            cb = self._on_connected
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _handshake_v1(self) -> None:
        """
        NTRIP 1.0 server push.

        Emlid-compatible casters accept the classic SOURCE request and may return
        either ``ICY 200 OK`` or an HTTP 200 status line. The DYX_BASE reference
        also sends Basic auth alongside the SOURCE line, so we do the same.
        """
        auth = base64.b64encode(
            f"{self.username}:{self.password}".encode("ascii")
        ).decode("ascii")
        request = (
            f"SOURCE {self.password} /{self.mountpoint}\r\n"
            f"Source-Agent: NTRIP {self.agent}\r\n"
            f"Authorization: Basic {auth}\r\n"
            f"\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = self._recv_handshake_response()
        first_line = response.splitlines()[0] if response else ""
        if "ICY 200 OK" not in response and "HTTP/1.0 200" not in first_line and "HTTP/1.1 200" not in first_line:
            raise ConnectionError(f"NTRIP 1.0 rejected: {response.strip()!r}")

    def _handshake_v2(self) -> None:
        """NTRIP 2.0: HTTP POST with Basic auth, expects HTTP 200."""
        creds = base64.b64encode(
            f"{self.username}:{self.password}".encode()
        ).decode("ascii")
        request = (
            f"POST /{self.mountpoint} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"User-Agent: NTRIP {self.agent}\r\n"
            f"Authorization: Basic {creds}\r\n"
            f"Transfer-Encoding: chunked\r\n"
            f"Content-Type: gnss/data\r\n"
            f"\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = self._recv_handshake_response()
        first_line = response.splitlines()[0] if response else ""
        if "HTTP/1.0 200" not in first_line and "HTTP/1.1 200" not in first_line:
            raise ConnectionError(f"NTRIP 2.0 rejected: {response.strip()!r}")

    def _recv_handshake_response(self, timeout: float = 10.0) -> str:
        """
        Read the caster handshake response.

        We read until the HTTP header terminator, an ICY line, or timeout. Some
        casters fragment the response, so a single recv() is not reliable.
        """
        if self._sock is None:
            raise ConnectionError("Socket not initialized for handshake")

        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        self._sock.settimeout(1.0)
        try:
            while time.monotonic() < deadline:
                try:
                    chunk = self._sock.recv(512)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                self.bytes_received += len(chunk)
                self.last_receive_time = time.monotonic()
                chunks.append(chunk)
                response = b"".join(chunks).decode("ascii", errors="ignore")
                if "ICY 200 OK" in response or "\r\n\r\n" in response:
                    return response
        finally:
            self._sock.settimeout(None)
        return b"".join(chunks).decode("ascii", errors="ignore")

    def _configure_socket(self, sock: socket.socket) -> None:
        """Apply socket options that keep long-lived push sessions healthy."""
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        if hasattr(socket, "TCP_KEEPINTVL"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        if hasattr(socket, "TCP_KEEPCNT"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        if hasattr(socket, "TCP_NODELAY"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if hasattr(socket, "SO_SNDTIMEO"):
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_SNDTIMEO,
                struct.pack("ll", 15, 0),
            )

    def _clear_rtcm_queue(self) -> int:
        """Discard stale RTCM accumulated while disconnected."""
        dropped = 0
        while True:
            try:
                self._rtcm_queue.get_nowait()
                dropped += 1
            except queue.Empty:
                return dropped

    def _push_loop(self) -> None:
        """Drain queue and write RTCM bytes to socket until disconnect or stop."""
        while self._connected and not self._stop_event.is_set():
            self._drain_incoming()
            try:
                data = self._rtcm_queue.get(timeout=5.0)
            except queue.Empty:
                self._drain_incoming()
                continue  # keepalive — socket stays open

            try:
                if self.ntrip_version == 2:
                    chunk = f"{len(data):X}\r\n".encode() + data + b"\r\n"
                    self._sock.sendall(chunk)
                else:
                    self._sock.sendall(data)
                self.bytes_sent += len(data)
                self.frames_sent += 1

                # Log first frame
                if not self._first_frame_logged:
                    self._first_frame_logged = True
                    logger.info(
                        f"[NTRIP] >>> First RTCM frame sent  "
                        f"size={len(data)}B  mountpoint=/{self.mountpoint}"
                    )

                # Data rate tracking
                now_mono = time.monotonic()
                self.last_send_time = now_mono
                self._bytes_in_window += len(data)
                window_elapsed = now_mono - self._window_start
                if window_elapsed >= 1.0:
                    self.data_rate_bps = self._bytes_in_window / window_elapsed
                    self._bytes_in_window = 0
                    self._window_start = now_mono

                # Periodic stats every 30 s
                now_wall = time.time()
                if now_wall - self._last_stats_log >= 5.0:
                    uptime = now_mono - self._connected_since if self._connected_since else 0.0
                    logger.info(
                        f"[NTRIP] streaming  frames={self.frames_sent:,}  "
                        f"bytes={self.bytes_sent:,}  "
                        f"rate={self.data_rate_bps:.0f} B/s  "
                        f"uptime={uptime:.0f}s"
                    )
                    self._last_stats_log = now_wall

            except socket.timeout as e:
                logger.error(f"[NTRIP] Send timed out: {e}")
                self._connected = False
                break
            except (OSError, BrokenPipeError, ConnectionResetError) as e:
                logger.error(f"[NTRIP] Send error: {e}")
                self._connected = False
                break

    def _drain_incoming(self) -> None:
        """Non-blocking read of any caster-side bytes after connection."""
        if self._sock is None or not self._connected:
            return
        try:
            while True:
                readable, _, _ = select.select([self._sock], [], [], 0)
                if not readable:
                    return
                chunk = self._sock.recv(4096)
                if not chunk:
                    self._connected = False
                    return
                self.bytes_received += len(chunk)
                self.last_receive_time = time.monotonic()
        except (BlockingIOError, InterruptedError):
            return
        except (OSError, ConnectionResetError) as e:
            logger.error(f"[NTRIP] Receive error: {e}")
            self._connected = False

    def _close_socket(self) -> None:
        was_connected = self._connected
        self._connected = False
        self._connected_since = 0.0
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        # Fire disconnected callback
        if was_connected:
            with self._callback_lock:
                cb = self._on_disconnected
            if cb is not None:
                try:
                    cb()
                except Exception:
                    pass
