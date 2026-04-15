#!/usr/bin/env python3
"""
One-time ZED-F9P UART2 setup script.

Run this ONCE with the USB cable (ttyACM0) connected to configure the
receiver's UART2 port and save the settings to FLASH.  After that the
USB cable is no longer needed — the application uses ttyAMA0 exclusively.

Usage:
    python3 setup_uart2.py

Optional arguments (environment variables):
    CONFIG_PORT   USB device to use  (default: /dev/ttyACM0)
    UART2_BAUD    UART2 baud rate    (default: 38400 — must match SERIAL_BAUDRATE)

Example with custom port:
    CONFIG_PORT=/dev/ttyACM1 python3 setup_uart2.py

What this writes to receiver FLASH (layers RAM + BBR + FLASH):
    CFG_UART2_BAUDRATE      38400   baud rate on UART2 GPIO pins
    CFG_UART2INPROT_UBX     True    accept UBX commands from Pi  (poll, config)
    CFG_UART2INPROT_NMEA    False
    CFG_UART2INPROT_RTCM3X  False
    CFG_UART2OUTPROT_UBX    True    send NAV-PVT / NAV-SVIN / ACK to Pi
    CFG_UART2OUTPROT_NMEA   False
    CFG_UART2OUTPROT_RTCM3X False   autoflow enables this after survey-in
"""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Allow running from the project root without installing the package
sys.path.insert(0, os.path.dirname(__file__))

from app.gnss.uart2_config import configure_uart2

CONFIG_PORT = os.environ.get("CONFIG_PORT", "/dev/ttyACM0")
UART2_BAUD  = int(os.environ.get("UART2_BAUD", "38400"))

print()
print("=" * 56)
print("  ZED-F9P UART2 One-Time Setup")
print("=" * 56)
print(f"  Config port : {CONFIG_PORT}  (USB)")
print(f"  UART2 baud  : {UART2_BAUD}")
print("  Layers      : RAM + BBR + FLASH  (permanent)")
print("=" * 56)
print()

ok = configure_uart2(
    config_port=CONFIG_PORT,
    uart2_baudrate=UART2_BAUD,
)

print()
if ok:
    print("SUCCESS — UART2 configured and saved to FLASH.")
    print("You can now disconnect the USB cable.")
    print(f"The application will use /dev/ttyAMA0 @ {UART2_BAUD} baud.")
else:
    print("FAILED — see log above for details.")
    print("Common causes:")
    print("  - USB cable not connected  (/dev/ttyACM0 missing)")
    print("  - Permission denied        (add user to 'dialout' group)")
    print("  - Wrong CONFIG_PORT        (check: ls /dev/ttyACM*)")
    sys.exit(1)
