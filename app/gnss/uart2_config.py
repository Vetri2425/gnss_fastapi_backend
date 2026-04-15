"""
ZED-F9P UART2 port configuration via USB (ttyACM0).

Opens the USB port, sends CFG-VALSET to configure UART2 for UBX I/O at
the specified baud rate, and saves the settings to RAM + BBR + FLASH
so they survive warm restarts and power cycles.

CFG keys used (pyubx2 1.2.x / ZED-F9P interface description):
  CFG_UART2_BAUDRATE      0x40530001  U4  — baud rate
  CFG_UART2INPROT_UBX     0x10750001  L   — accept UBX commands from host
  CFG_UART2INPROT_NMEA    0x10750002  L   — NMEA input (disabled)
  CFG_UART2INPROT_RTCM3X  0x10750004  L   — RTCM3X input (disabled — base station)
  CFG_UART2OUTPROT_UBX    0x10760001  L   — send NAV responses to host
  CFG_UART2OUTPROT_NMEA   0x10760002  L   — NMEA output (disabled)
  CFG_UART2OUTPROT_RTCM3X 0x10760004  L   — RTCM3X output (disabled here;
                                             autoflow enables via create_rtcm_enable_command)

Layer mask 7 = RAM(1) | BBR(2) | FLASH(4) — full persistence across power cycles.
"""

import logging
import time

import serial
from pyubx2 import GET, SET_LAYER_BBR, SET_LAYER_FLASH, SET_LAYER_RAM, TXN_NONE, UBXMessage, UBXReader

logger = logging.getLogger(__name__)

_ACK_TIMEOUT = 3.0   # seconds to wait for ACK-ACK / ACK-NAK
_LAYERS = SET_LAYER_RAM | SET_LAYER_BBR | SET_LAYER_FLASH   # = 7


def configure_uart2(
    config_port: str = "/dev/ttyACM0",
    config_baudrate: int = 9600,
    uart2_baudrate: int = 38400,
) -> bool:
    """
    Configure ZED-F9P UART2 via the USB port.

    Sends a single CFG-VALSET that:
      - Sets UART2 baud rate to ``uart2_baudrate`` (must match SERIAL_BAUDRATE)
      - Enables UBX input on UART2  (Pi → receiver: poll/config commands)
      - Enables UBX output on UART2 (receiver → Pi: NAV-PVT, NAV-SVIN, ACK)
      - Enables specific UBX message rates: NAV-PVT, NAV-SVIN, NAV-SAT (for base station)
      - Disables NMEA I/O (not needed; UBXReader filters it anyway)
      - Disables RTCM3X output here (autoflow enables it after survey-in)

    Saves to RAM + BBR + FLASH so the configuration survives warm resets
    and full power cycles without needing a USB connection.

    Args:
        config_port:     USB serial device used for one-time configuration.
        config_baudrate: Baud for the USB port (CDC-ACM ignores it; 9600 is safe).
        uart2_baudrate:  Baud rate to program on UART2 — must match SERIAL_BAUDRATE.

    Returns:
        True  if the receiver sent ACK-ACK (config accepted and saved).
        False if NAK received, timeout, or serial error.
    """
    msg = UBXMessage.config_set(
        layers=_LAYERS,
        transaction=TXN_NONE,
        cfgData=[
            ("CFG_UART2_BAUDRATE",         uart2_baudrate),  # U4
            ("CFG_UART2INPROT_UBX",        True),            # L
            ("CFG_UART2INPROT_NMEA",       False),           # L
            ("CFG_UART2INPROT_RTCM3X",     False),           # L
            ("CFG_UART2OUTPROT_UBX",       True),            # L
            ("CFG_UART2OUTPROT_NMEA",      False),           # L
            ("CFG_UART2OUTPROT_RTCM3X",    False),           # L — autoflow enables this
            # Enable specific UBX message output on UART2 (required for base station)
            ("CFG_MSGOUT_UBX_NAV_PVT_UART2",   1),           # NAV-PVT @ 1 Hz
            ("CFG_MSGOUT_UBX_NAV_SVIN_UART2",  1),           # NAV-SVIN @ 1 Hz
            ("CFG_MSGOUT_UBX_NAV_SAT_UART2",   1),           # NAV-SAT @ 1 Hz (optional)
        ],
    )

    logger.info(
        f"[UART2] Configuring UART2@{uart2_baudrate} via {config_port} "
        f"(layers=0x{_LAYERS:02X}: RAM+BBR+FLASH)"
    )

    try:
        with serial.Serial(config_port, config_baudrate, timeout=1.0) as ser:
            ser.reset_input_buffer()
            raw_cmd = msg.serialize()
            ser.write(raw_cmd)
            ser.flush()
            logger.debug(f"[UART2] CFG-VALSET sent ({len(raw_cmd)} bytes)")

            # Wait for ACK-ACK or ACK-NAK
            ubr = UBXReader(ser, msgmode=GET)
            deadline = time.monotonic() + _ACK_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    _, parsed = ubr.read()
                    if parsed is None:
                        continue
                    ident = parsed.identity
                    if ident == "ACK-ACK":
                        logger.info(
                            f"[UART2] ACK received — UART2 configured at {uart2_baudrate} baud, "
                            f"saved to RAM+BBR+FLASH"
                        )
                        return True
                    if ident == "ACK-NAK":
                        logger.error(
                            "[UART2] NAK received — receiver rejected UART2 configuration; "
                            "check firmware version or key names"
                        )
                        return False
                except Exception:
                    pass  # partial / non-UBX frame — keep reading

            logger.warning(
                f"[UART2] No ACK received within {_ACK_TIMEOUT}s — "
                "configuration may or may not have been applied"
            )
            return False

    except serial.SerialException as e:
        logger.error(f"[UART2] Cannot open config port {config_port}: {e}")
        return False
    except Exception as e:
        logger.error(f"[UART2] Unexpected error during UART2 configuration: {e}")
        return False
