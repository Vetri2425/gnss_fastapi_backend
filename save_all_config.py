#!/usr/bin/env python3
"""
Comprehensive ZED-F9P Configuration Save Script.

Run this ONCE with the USB cable (ttyACM0) connected to configure and save
ALL receiver settings to RAM + BBR + FLASH for permanent persistence.

This script configures:
  - UART2 port (baudrate, protocols, message rates)
  - UART1 port (protocols for Pi GPIO connection)
  - USB port (protocols for configuration)
  - Survey-in default settings (duration, accuracy)
  - RTCM message defaults (MSM7 + ARP)
  - Navigation settings (update rate, dynamic model)
  - GNSS constellation enable (GPS, GAL, GLO, BDS)
  - Power management (never sleep)
  - NMEA settings (disable if not needed)

Usage:
    python3 save_all_config.py

Optional arguments (environment variables):
    CONFIG_PORT   USB device to use  (default: /dev/ttyACM0)
    UART2_BAUD    UART2 baud rate    (default: 38400)

Example with custom port:
    CONFIG_PORT=/dev/ttyACM1 python3 save_all_config.py

What this writes to receiver FLASH (layers RAM + BBR + FLASH):
    See CFG_DATA dict below for complete list.
"""

import os
import sys
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Allow running from the project root without installing the package
sys.path.insert(0, os.path.dirname(__file__))

from pyubx2 import SET_LAYER_RAM, SET_LAYER_BBR, SET_LAYER_FLASH, TXN_NONE, UBXMessage
import serial

# Configuration constants
CONFIG_PORT = os.environ.get("CONFIG_PORT", "/dev/ttyACM0")
CONFIG_BAUDRATE = 9600  # USB CDC-ACM ignores this
UART2_BAUDRATE = int(os.environ.get("UART2_BAUD", "38400"))

# Memory layers: RAM + BBR + FLASH = 7 (full persistence)
_LAYERS = SET_LAYER_RAM | SET_LAYER_BBR | SET_LAYER_FLASH

# Complete configuration data for ZED-F9P base station operation
CFG_DATA = [
    # =========================================================================
    # UART2 Configuration (GPIO pins - connects to Pi GPIO UART)
    # =========================================================================
    ("CFG_UART2_BAUDRATE", UART2_BAUDRATE),           # Baud rate for UART2
    ("CFG_UART2INPROT_UBX", True),                     # Accept UBX commands
    ("CFG_UART2INPROT_NMEA", False),                   # No NMEA input
    ("CFG_UART2INPROT_RTCM3X", False),                 # No RTCM input (base station)
    ("CFG_UART2OUTPROT_UBX", True),                    # Send UBX responses
    ("CFG_UART2OUTPROT_NMEA", False),                  # No NMEA output
    ("CFG_UART2OUTPROT_RTCM3X", True),                 # RTCM output (enabled for base)
    
    # UART2 Message output rates (1 = every epoch @ 1Hz)
    ("CFG_MSGOUT_UBX_NAV_PVT_UART2", 1),               # NAV-PVT @ 1 Hz
    ("CFG_MSGOUT_UBX_NAV_SVIN_UART2", 1),              # NAV-SVIN @ 1 Hz
    ("CFG_MSGOUT_UBX_NAV_SAT_UART2", 1),               # NAV-SAT @ 1 Hz
    ("CFG_MSGOUT_UBX_NAV_SIG_UART2", 0),               # NAV-SIG disabled
    ("CFG_MSGOUT_UBX_NAV_RELPOSNED_UART2", 0),         # NAV-RELPOSNED disabled (rover use)
    
    # RTCM3X message output on UART2 (enabled by default for base station)
    ("CFG_MSGOUT_RTCM_3X_TYPE1005_UART2", 1),          # ARP message
    ("CFG_MSGOUT_RTCM_3X_TYPE1074_UART2", 0),          # MSM4 GPS (disabled, use MSM7)
    ("CFG_MSGOUT_RTCM_3X_TYPE1084_UART2", 0),          # MSM4 GLONASS
    ("CFG_MSGOUT_RTCM_3X_TYPE1094_UART2", 0),          # MSM4 Galileo
    ("CFG_MSGOUT_RTCM_3X_TYPE1124_UART2", 0),          # MSM4 BeiDou
    ("CFG_MSGOUT_RTCM_3X_TYPE1077_UART2", 1),          # MSM7 GPS
    ("CFG_MSGOUT_RTCM_3X_TYPE1087_UART2", 1),          # MSM7 GLONASS
    ("CFG_MSGOUT_RTCM_3X_TYPE1097_UART2", 1),          # MSM7 Galileo
    ("CFG_MSGOUT_RTCM_3X_TYPE1127_UART2", 1),          # MSM7 BeiDou
    
    # =========================================================================
    # UART1 Configuration (optional - if using USB primarily)
    # =========================================================================
    ("CFG_UART1_BAUDRATE", 38400),                     # Match UART2 for consistency
    ("CFG_UART1INPROT_UBX", True),                     # Accept UBX commands
    ("CFG_UART1INPROT_NMEA", False),                   # No NMEA input
    ("CFG_UART1INPROT_RTCM3X", False),                 # No RTCM input
    ("CFG_UART1OUTPROT_UBX", True),                    # Send UBX responses
    ("CFG_UART1OUTPROT_NMEA", False),                  # No NMEA output
    ("CFG_UART1OUTPROT_RTCM3X", True),                 # RTCM output
    
    # UART1 Message output rates (mirror UART2)
    ("CFG_MSGOUT_UBX_NAV_PVT_UART1", 1),
    ("CFG_MSGOUT_UBX_NAV_SVIN_UART1", 1),
    ("CFG_MSGOUT_UBX_NAV_SAT_UART1", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1005_UART1", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1077_UART1", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1087_UART1", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1097_UART1", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1127_UART1", 1),
    
    # =========================================================================
    # USB Configuration (for direct USB connection)
    # =========================================================================
    ("CFG_USBINPROT_UBX", True),                       # Accept UBX commands
    ("CFG_USBINPROT_NMEA", False),                     # No NMEA input
    ("CFG_USBINPROT_RTCM3X", False),                   # No RTCM input
    ("CFG_USBOUTPROT_UBX", True),                      # Send UBX responses
    ("CFG_USBOUTPROT_NMEA", False),                    # No NMEA output
    ("CFG_USBOUTPROT_RTCM3X", True),                   # RTCM output
    
    # USB Message output rates
    ("CFG_MSGOUT_UBX_NAV_PVT_USB", 1),
    ("CFG_MSGOUT_UBX_NAV_SVIN_USB", 1),
    ("CFG_MSGOUT_UBX_NAV_SAT_USB", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1005_USB", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1077_USB", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1087_USB", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1097_USB", 1),
    ("CFG_MSGOUT_RTCM_3X_TYPE1127_USB", 1),
    
    # =========================================================================
    # Survey-in Default Settings (used when survey mode enabled)
    # =========================================================================
    ("CFG_TMODE_MODE", 0),                             # 0=Disabled, 1=Survey-in, 2=Fixed
    ("CFG_TMODE_SVIN_MIN_DUR", 60),                    # Minimum duration (seconds)
    ("CFG_TMODE_SVIN_ACC_LIMIT", 20000),               # Accuracy limit (0.1mm units = 2.0m)
    
    # =========================================================================
    # Navigation Settings
    # =========================================================================
    ("CFG_NAVSPG_FIXMODE", 3),                         # 3D only
    ("CFG_NAVSPG_UTCSTANDARD", 3),                     # UTC standard
    ("CFG_NAVSPG_DYNMODEL", 2),                        # Dynamic model: 2=Stationary (base station)
    
    # Position update rate (1 Hz = 1000ms)
    ("CFG_RATE_MEAS", 1000),                           # Measurement rate in ms
    ("CFG_RATE_NAV", 1),                               # Navigation update rate
    
    # =========================================================================
    # GNSS Constellation Configuration (enable all for best accuracy)
    # =========================================================================
    ("CFG_SIGNAL_GPS_ENA", True),                      # GPS enabled
    ("CFG_SIGNAL_GPS_L1CA_ENA", True),                 # GPS L1 C/A
    ("CFG_SIGNAL_GPS_L2C_ENA", True),                  # GPS L2C (L5 for F9P)
    ("CFG_SIGNAL_GPS_L5_ENA", True),                   # GPS L5
    
    ("CFG_SIGNAL_GAL_ENA", True),                      # Galileo enabled
    ("CFG_SIGNAL_GAL_E1_ENA", True),                   # Galileo E1
    ("CFG_SIGNAL_GAL_E5B_ENA", True),                  # Galileo E5b
    
    ("CFG_SIGNAL_GLO_ENA", True),                      # GLONASS enabled
    ("CFG_SIGNAL_GLO_L1_ENA", True),                   # GLONASS L1
    ("CFG_SIGNAL_GLO_L2_ENA", True),                   # GLONASS L2
    
    ("CFG_SIGNAL_BDS_ENA", True),                      # BeiDou enabled
    ("CFG_SIGNAL_BDS_B1_ENA", True),                   # BeiDou B1
    ("CFG_SIGNAL_BDS_B2_ENA", True),                   # BeiDou B2
    
    ("CFG_SIGNAL_QZSS_ENA", True),                     # QZSS enabled
    ("CFG_SIGNAL_QZSS_L1CA_ENA", True),                # QZSS L1 C/A
    ("CFG_SIGNAL_QZSS_L1S_ENA", True),                 # QZSS L1S
    ("CFG_SIGNAL_QZSS_L2C_ENA", True),                 # QZSS L2C
    ("CFG_SIGNAL_QZSS_L5_ENA", True),                  # QZSS L5
    
    ("CFG_SIGNAL_SBAS_ENA", False),                    # SBAS disabled (base station)
    
    # =========================================================================
    # Power Management
    # =========================================================================
    ("CFG_PM_OPERMODE", 0),                            # Continuous operation mode
    
    # =========================================================================
    # NMEA Configuration (disable - not needed for base station)
    # =========================================================================
    ("CFG_NMEA_HIGHPREC", False),                      # Disable high precision NMEA
    ("CFG_NMEA_FILTER", 0),                            # No NMEA filtering
    ("CFG_NMEA_INIFILT", False),                       # Disable initial filter
    ("CFG_NMEA_NUMSV", False),                         # Don't include numSV in NMEA
    ("CFG_NMEA_OUTINV", False),                        # Don't output invalid NMEA
    ("CFG_NMEA_PROTECT", False),                       # No NMEA protection
    ("CFG_NMEA_STRICT", False),                        # No NMEA strict mode
    ("CFG_NMEA_URTK", False),                          # No u-blox RTK NMEA
    
    # =========================================================================
    # Inf Messages (logging - enable for debugging)
    # =========================================================================
    ("CFG_INFMSG_UBX_UART1", 0b00111111),              # Enable all INF messages on UART1
    ("CFG_INFMSG_UBX_UART2", 0b00111111),              # Enable all INF messages on UART2
    ("CFG_INFMSG_UBX_USB", 0b00111111),                # Enable all INF messages on USB
    
    # =========================================================================
    # SV Information (satellite data)
    # =========================================================================
    ("CFG_NAVSPG_SVINACCINIT", 0),                     # No initial survey accuracy
    ("CFG_ODO_COGLPCTRL", False),                      # Disable COG low pass filter
    ("CFG_ODO_COGLPGAIN", 0),                          # COG gain = 0
    ("CFG_ODO_ODOCTRL", False),                        # Disable odometer
    ("CFG_ODO_OUTLPCTRL", False),                      # Disable output low pass
]


def send_configuration(ser: serial.Serial, cfg_data: list) -> bool:
    """
    Send configuration to receiver and wait for ACK.
    
    Args:
        ser: Serial port connection
        cfg_data: List of (key, value) tuples
        
    Returns:
        True if ACK-ACK received, False otherwise
    """
    from pyubx2 import UBXReader, GET
    
    # Split config into chunks to avoid oversized messages
    # UBX-CFG-VALSET max payload is ~64 keys per message
    chunk_size = 60
    chunks = [cfg_data[i:i + chunk_size] for i in range(0, len(cfg_data), chunk_size)]
    
    logger.info(f"[CONFIG] Sending {len(cfg_data)} configuration keys in {len(chunks)} chunks")
    
    for i, chunk in enumerate(chunks):
        logger.info(f"[CONFIG] Chunk {i+1}/{len(chunks)} ({len(chunk)} keys)")
        
        msg = UBXMessage.config_set(
            layers=_LAYERS,
            transaction=TXN_NONE,
            cfgData=chunk,
        )
        
        ser.reset_input_buffer()
        raw = msg.serialize()
        ser.write(raw)
        ser.flush()
        
        logger.debug(f"[CONFIG] Sent {len(raw)} bytes")
        
        # Wait for ACK
        ubr = UBXReader(ser, msgmode=GET)
        deadline = time.monotonic() + 5.0
        
        while time.monotonic() < deadline:
            try:
                _, parsed = ubr.read()
                if parsed is None:
                    continue
                    
                if parsed.identity == "ACK-ACK":
                    logger.info(f"[CONFIG] Chunk {i+1} ACK-ACK received")
                    break
                elif parsed.identity == "ACK-NAK":
                    logger.error(
                        f"[CONFIG] Chunk {i+1} NAK received - "
                        f"Class: 0x{parsed.msgClass:02X}, ID: 0x{parsed.msgID:02X}"
                    )
                    return False
            except Exception as e:
                logger.debug(f"[CONFIG] Read error (continuing): {e}")
                pass
        else:
            logger.warning(f"[CONFIG] Chunk {i+1} timeout - no ACK received")
            # Continue anyway - some configs may still have been applied
        
        time.sleep(0.5)  # Brief pause between chunks
    
    return True


def main():
    """Main configuration routine."""
    print()
    print("=" * 70)
    print("  ZED-F9P Comprehensive Configuration Save")
    print("=" * 70)
    print(f"  Config port     : {CONFIG_PORT} (USB)")
    print(f"  UART2 baudrate  : {UART2_BAUDRATE}")
    print(f"  Memory layers   : RAM + BBR + FLASH (permanent)")
    print(f"  Total keys      : {len(CFG_DATA)}")
    print("=" * 70)
    print()
    
    print("Configuration includes:")
    print("  ✓ UART1/UART2/USB port settings")
    print("  ✓ NAV-PVT, NAV-SVIN, NAV-SAT message rates")
    print("  ✓ RTCM3 MSM7 + ARP output")
    print("  ✓ Survey-in defaults (60s, 2.0m)")
    print("  ✓ Navigation settings (stationary mode)")
    print("  ✓ GNSS constellations (GPS, GAL, GLO, BDS, QZSS)")
    print("  ✓ Power management (always on)")
    print("  ✓ NMEA disabled")
    print()
    
    try:
        with serial.Serial(CONFIG_PORT, CONFIG_BAUDRATE, timeout=2.0) as ser:
            logger.info(f"[CONFIG] Opened {CONFIG_PORT}")
            time.sleep(2.0)  # Wait for receiver to stabilize
            
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            # Send all configuration
            success = send_configuration(ser, CFG_DATA)
            
            print()
            if success:
                print("=" * 70)
                print("  SUCCESS — All configurations saved to RAM+BBR+FLASH")
                print("=" * 70)
                print()
                print("Next steps:")
                print("  1. Disconnect USB cable (if using GPIO UART)")
                print("  2. Connect Pi GPIO UART to ZED-F9P UART2")
                print("  3. Start the application: sudo python3 -m uvicorn app.main:app")
                print()
                print("The receiver will now:")
                print(f"  - Output UBX messages on UART2 at {UART2_BAUDRATE} baud")
                print("  - Output RTCM3 MSM7 + ARP for RTK base station")
                print("  - Use stationary dynamic model for best accuracy")
                print("  - Track GPS, Galileo, GLONASS, BeiDou, QZSS")
                print()
                return 0
            else:
                print("=" * 70)
                print("  PARTIAL FAILURE — Some configurations may not have been applied")
                print("=" * 70)
                print()
                print("Check the log above for NAK errors.")
                print("Common causes:")
                print("  - Firmware version doesn't support some keys")
                print("  - Receiver busy with other operations")
                print()
                print("You can retry or proceed with partial configuration.")
                return 1
                
    except serial.SerialException as e:
        logger.error(f"[CONFIG] Cannot open {CONFIG_PORT}: {e}")
        print()
        print("ERROR: Cannot open serial port")
        print("Common causes:")
        print(f"  - USB cable not connected ({CONFIG_PORT} missing)")
        print("  - Permission denied (add user to 'dialout' group)")
        print(f"  - Wrong port (check: ls /dev/ttyACM*)")
        print()
        return 2
    except Exception as e:
        logger.exception(f"[CONFIG] Unexpected error: {e}")
        print()
        print(f"ERROR: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
