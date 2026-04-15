"""
Serial Port Utilities.

Functions for detecting available serial ports, testing connections,
and managing serial reconnection logic.
"""

import logging
import sys
from typing import Optional

import serial
from serial.tools import list_ports

logger = logging.getLogger(__name__)


def detect_serial_ports(
    include_hwid: bool = True,
    include_description: bool = True,
) -> list[dict]:
    """
    Detect all available serial ports on the system.

    Scans for available serial ports and returns detailed information
    about each port including device path, description, and hardware ID.

    Args:
        include_hwid: Include hardware ID in results
        include_description: Include description in results

    Returns:
        List of dictionaries with port information:
        [
            {
                "device": "/dev/ttyUSB0",
                "name": "ttyUSB0",
                "description": "USB Serial",
                "hwid": "USB VID:PID=2341:0043 SER=ABC123 LOCATION=1-1",
                "is_usb": True,
            },
            ...
        ]
    """
    ports = []

    try:
        for port in list_ports.comports():
            port_info = {
                "device": port.device,
                "name": port.name,
            }

            if include_description:
                port_info["description"] = port.description

            if include_hwid:
                port_info["hwid"] = port.hwid
                port_info["is_usb"] = "USB" in port.hwid.upper() or (
                    hasattr(port, "vid") and port.vid is not None
                )

                # Add vendor/product IDs if available
                if hasattr(port, "vid") and port.vid is not None:
                    port_info["vendor_id"] = f"0x{port.vid:04X}"

                if hasattr(port, "pid") and port.pid is not None:
                    port_info["product_id"] = f"0x{port.pid:04X}"

                if hasattr(port, "serial_number") and port.serial_number:
                    port_info["serial_number"] = port.serial_number

            ports.append(port_info)

        logger.debug(f"Detected {len(ports)} serial ports")

    except Exception as e:
        logger.error(f"Error detecting serial ports: {e}")

    return ports


def test_serial_connection(
    port: str,
    baudrate: int = 9600,
    timeout: float = 1.0,
) -> dict:
    """
    Test a serial connection without keeping it open.

    Attempts to open the specified serial port and returns
    connection status and any error information.

    Args:
        port: Serial port path (e.g., "/dev/ttyUSB0" or "COM3")
        baudrate: Baud rate to test
        timeout: Read timeout in seconds

    Returns:
        Dictionary with test results:
        {
            "success": True,
            "port": "/dev/ttyUSB0",
            "baudrate": 9600,
            "message": "Connection successful",
        }
        or
        {
            "success": False,
            "port": "/dev/ttyUSB0",
            "error": "Permission denied",
            "error_type": "PermissionError",
        }
    """
    result = {
        "success": False,
        "port": port,
        "baudrate": baudrate,
    }

    ser: Optional[serial.Serial] = None

    try:
        logger.debug(f"Testing serial connection: {port}@{baudrate}")

        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=timeout,
        )

        # Connection successful
        result["success"] = True
        result["message"] = "Connection successful"

        # Try to read any available data (non-blocking)
        if ser.in_waiting > 0:
            data = ser.read(min(ser.in_waiting, 100))
            result["bytes_available"] = len(data)
            logger.debug(f"Read {len(data)} bytes from port")

        logger.info(f"Serial connection test successful: {port}")

    except serial.SerialException as e:
        result["error"] = str(e)
        result["error_type"] = "SerialException"
        logger.warning(f"Serial exception testing {port}: {e}")

    except PermissionError as e:
        result["error"] = f"Permission denied: {e}"
        result["error_type"] = "PermissionError"
        logger.error(f"Permission denied accessing {port}: {e}")

    except FileNotFoundError as e:
        result["error"] = f"Port not found: {e}"
        result["error_type"] = "FileNotFoundError"
        logger.warning(f"Port not found: {port}")

    except Exception as e:
        result["error"] = str(e)
        result["error_type"] = type(e).__name__
        logger.error(f"Unexpected error testing {port}: {e}")

    finally:
        if ser is not None and ser.is_open:
            ser.close()

    return result


def find_usb_serial_ports() -> list[dict]:
    """
    Find only USB serial ports.

    Filters detected ports to return only USB-connected serial devices,
    which are typically GNSS receivers, FTDI adapters, etc.

    Returns:
        List of USB serial port dictionaries
    """
    all_ports = detect_serial_ports()
    usb_ports = [p for p in all_ports if p.get("is_usb", False)]

    logger.debug(f"Found {len(usb_ports)} USB serial ports")

    return usb_ports


def find_port_by_hwid(
    hwid_substring: str,
    case_sensitive: bool = False,
) -> Optional[str]:
    """
    Find a serial port by hardware ID substring.

    Searches for a port whose hardware ID contains the specified
    substring. Useful for finding specific devices.

    Args:
        hwid_substring: Hardware ID substring to search for
        case_sensitive: Whether search is case-sensitive

    Returns:
        Port device path if found, None otherwise
    """
    all_ports = detect_serial_ports()

    for port in all_ports:
        hwid = port.get("hwid", "")

        if case_sensitive:
            if hwid_substring in hwid:
                logger.debug(f"Found port by HWID: {port['device']}")
                return port["device"]
        else:
            if hwid_substring.lower() in hwid.lower():
                logger.debug(f"Found port by HWID: {port['device']}")
                return port["device"]

    logger.debug(f"No port found matching HWID: {hwid_substring}")
    return None


def find_port_by_vendor_product(
    vendor_id: int,
    product_id: int,
) -> Optional[str]:
    """
    Find a serial port by vendor and product ID.

    Searches for a USB device with matching VID/PID.

    Args:
        vendor_id: USB vendor ID (e.g., 0x2341 for Arduino)
        product_id: USB product ID

    Returns:
        Port device path if found, None otherwise
    """
    all_ports = detect_serial_ports()

    for port in all_ports:
        port_vid = port.get("vendor_id")
        port_pid = port.get("product_id")

        if port_vid and port_pid:
            try:
                vid = int(port_vid, 16)
                pid = int(port_pid, 16)

                if vid == vendor_id and pid == product_id:
                    logger.debug(
                        f"Found port by VID/PID: {port['device']} "
                        f"({vendor_id:#06X}:{product_id:#06X})"
                    )
                    return port["device"]

            except (ValueError, TypeError):
                continue

    logger.debug(
        f"No port found matching VID/PID: {vendor_id:#06X}:{product_id:#06X}"
    )
    return None


def get_port_info(port: str) -> Optional[dict]:
    """
    Get detailed information about a specific serial port.

    Args:
        port: Serial port path to query

    Returns:
        Port information dictionary or None if not found
    """
    all_ports = detect_serial_ports()

    for p in all_ports:
        if p["device"] == port:
            return p

    return None


def is_port_available(port: str) -> bool:
    """
    Check if a serial port is available (not in use).

    Attempts to open the port exclusively to determine if it's
    already in use by another process.

    Args:
        port: Serial port path to check

    Returns:
        True if port is available, False if in use or doesn't exist
    """
    ser: Optional[serial.Serial] = None

    try:
        ser = serial.Serial(
            port=port,
            baudrate=9600,
            timeout=0.1,
            exclusive=True,  # Request exclusive access
        )
        return True

    except (serial.SerialException, OSError, IOError):
        return False

    finally:
        if ser is not None and ser.is_open:
            ser.close()


def get_common_baudrates() -> list[int]:
    """
    Get list of common serial baud rates.

    Returns:
        List of standard baud rates
    """
    return [
        9600,
        19200,
        38400,
        57600,
        115200,
        230400,
        460800,
        921600,
    ]


def auto_detect_gnss_port() -> Optional[str]:
    """
    Attempt to auto-detect a GNSS receiver serial port.

    Looks for common GNSS receiver USB vendor IDs and returns
    the first matching port.

    Returns:
        Detected port path or None if no GNSS receiver found
    """
    # Common GNSS receiver vendor IDs
    gnss_vendors = [
        0x1546,  # u-blox
        0x067B,  # Prolific (common in GNSS dongles)
        0x0403,  # FTDI (common in GNSS modules)
        0x2341,  # Arduino (some GNSS shields)
        0x1A86,  # QinHeng (CH340, common in cheap GNSS)
        0x0483,  # STMicroelectronics
    ]

    all_ports = detect_serial_ports()

    for port in all_ports:
        vid_str = port.get("vendor_id", "")
        if vid_str:
            try:
                vid = int(vid_str, 16)
                if vid in gnss_vendors:
                    logger.info(f"Auto-detected GNSS receiver at {port['device']}")
                    return port["device"]
            except (ValueError, TypeError):
                continue

    # Fall back to first USB serial port
    usb_ports = find_usb_serial_ports()
    if usb_ports:
        logger.info(f"No specific GNSS receiver found, using first USB port: {usb_ports[0]['device']}")
        return usb_ports[0]["device"]

    logger.warning("No suitable serial port found for GNSS receiver")
    return None
