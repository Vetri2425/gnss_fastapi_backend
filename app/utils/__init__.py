"""
Utilities module for GNSS backend.

Provides serial port detection, reconnection logic, and other helper functions.
"""

from .serial_utils import detect_serial_ports, test_serial_connection

__all__ = ["detect_serial_ports", "test_serial_connection"]
