"""
GNSS Message Parser.

Parses UBX messages from pyubx2 into structured data for
NAV-SVIN (survey-in), NAV-PVT (position/velocity/time), and other messages.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from pyubx2 import UBXMessage

logger = logging.getLogger(__name__)


class GNSSParser:
    """
    Parser for UBX protocol messages.

    Converts pyubx2 UBXMessage objects into structured dictionaries
    suitable for state updates and API responses.
    """

    # Fix type mappings — per u-blox UBX-NAV-PVT interface description
    # fixType 5 = time only (base station timing mode, NOT RTK float)
    # RTK float/fixed is determined by carrSoln (0=none, 1=float, 2=fixed)
    FIX_TYPE_MAP = {
        0: "no_fix",
        1: "dr_only",
        2: "fix_2d",
        3: "fix_3d",
        4: "gnss_dr",
        5: "time_only",
    }

    # Carrier solution mappings
    CARR_SOLN_MAP = {
        0: "no_rt",
        1: "rt_float",
        2: "rt_fixed",
    }

    @staticmethod
    def parse_nav_svin(msg: UBXMessage, min_duration: int = 10) -> dict[str, Any]:
        """
        Parse NAV-SVIN (Survey-In) message.

        Args:
            msg: UBXMessage object containing NAV-SVIN data
            min_duration: Survey-in minimum duration in seconds for progress calculation

        Returns:
            Dictionary with parsed survey-in data
        """
        try:
            # Extract survey-in data from message
            # meanAcc is in 0.1mm units per u-blox spec (NOT cm like meanX/Y/Z)
            mean_acc_meters = getattr(msg, "meanAcc", 0) / 10000.0  # 0.1mm → meters (÷10,000)

            mean_x = getattr(msg, "meanX", 0)
            mean_y = getattr(msg, "meanY", 0)
            mean_z = getattr(msg, "meanZ", 0)
            mean_x_hp = getattr(msg, "meanXHP", 0)
            mean_y_hp = getattr(msg, "meanYHP", 0)
            mean_z_hp = getattr(msg, "meanZHP", 0)

            data = {
                "version": getattr(msg, "version", 0),
                "active": bool(getattr(msg, "active", 0)),
                "valid": bool(getattr(msg, "valid", 0)),
                "observation_time": getattr(msg, "dur", 0),  # Duration in seconds
                "mean_accuracy": mean_acc_meters,  # Convert 0.1mm units to meters
                "num_obs": getattr(msg, "obs", getattr(msg, "numObs", 0)),
                # ECEF coordinates are cm plus a signed 0.1 mm high-precision component.
                "ecef_x": (mean_x / 100.0) + (mean_x_hp / 10000.0),
                "ecef_y": (mean_y / 100.0) + (mean_y_hp / 10000.0),
                "ecef_z": (mean_z / 100.0) + (mean_z_hp / 10000.0),
                "ecef_x_hp": mean_x_hp / 10000.0,
                "ecef_y_hp": mean_y_hp / 10000.0,
                "ecef_z_hp": mean_z_hp / 10000.0,
                "accuracy": mean_acc_meters,  # meters
            }

            # Calculate progress using the configured survey duration.
            if data["observation_time"] > 0 and min_duration > 0:
                progress = min(100, int((data["observation_time"] / min_duration) * 100))
                data["progress"] = progress
            else:
                data["progress"] = 0

            data["in_progress"] = data["active"] and not data["valid"]
            data["timestamp"] = datetime.utcnow().isoformat()

            logger.debug(
                f"Parsed NAV-SVIN: active={data['active']}, valid={data['valid']}, "
                f"accuracy={data['accuracy']:.4f}m, obs_time={data['observation_time']}s"
            )

            return data

        except Exception as e:
            logger.error(f"Error parsing NAV-SVIN message: {e}")
            return {
                "active": False,
                "valid": False,
                "in_progress": False,
                "progress": 0,
                "accuracy": 0.0,
                "observation_time": 0,
                "error": str(e),
            }

    @staticmethod
    def parse_nav_pvt(msg: UBXMessage) -> dict[str, Any]:
        """
        Parse NAV-PVT (Position Velocity Time) message.

        Args:
            msg: UBXMessage object containing NAV-PVT data

        Returns:
            Dictionary with parsed position/velocity/time data
        """
        try:
            # pyubx2 pre-scales lat/lon (1e-7), headMot (1e-5), pDOP (0.01)
            # hMSL/hAcc/vAcc/vel*/gSpeed have no scale — raw mm or mm/s
            lat = getattr(msg, "lat", 0.0)
            lon = getattr(msg, "lon", 0.0)
            alt = getattr(msg, "hMSL", 0) / 1000.0
            h_acc = getattr(msg, "hAcc", 0) / 1000.0
            v_acc = getattr(msg, "vAcc", 0) / 1000.0

            vel_north = getattr(msg, "velN", 0) / 1000.0
            vel_east = getattr(msg, "velE", 0) / 1000.0
            vel_down = getattr(msg, "velD", 0) / 1000.0
            ground_speed = getattr(msg, "gSpeed", 0) / 1000.0
            heading = getattr(msg, "headMot", 0.0)

            fix_type = getattr(msg, "fixType", 0)
            num_sats = getattr(msg, "numSV", 0)
            carr_soln = getattr(msg, "carrSoln", 0)

            p_dop = getattr(msg, "pDOP", 0.0)

            data = {
                "latitude": lat,
                "longitude": lon,
                "altitude": alt,
                "accuracy": h_acc,
                "vertical_accuracy": v_acc,
                "fix_type": fix_type,
                "fix_type_str": GNSSParser.FIX_TYPE_MAP.get(fix_type, "unknown"),
                "num_satellites": num_sats,
                "carrier_solution": carr_soln,
                "carrier_solution_str": GNSSParser.CARR_SOLN_MAP.get(carr_soln, "unknown"),
                "velocity_north": vel_north,
                "velocity_east": vel_east,
                "velocity_down": vel_down,
                "ground_speed": ground_speed,
                "heading": heading,
                "pdop": p_dop,
                "timestamp": datetime.utcnow().isoformat(),
            }

            logger.debug(
                f"Parsed NAV-PVT: lat={lat:.6f}, lon={lon:.6f}, alt={alt:.2f}m, "
                f"acc={h_acc:.2f}m, sats={num_sats}, fix={fix_type}"
            )

            return data

        except Exception as e:
            logger.error(f"Error parsing NAV-PVT message: {e}")
            return {
                "latitude": 0.0,
                "longitude": 0.0,
                "altitude": 0.0,
                "accuracy": 0.0,
                "fix_type": 0,
                "num_satellites": 0,
                "error": str(e),
            }

    @staticmethod
    def parse_nav_sat(msg: UBXMessage) -> dict[str, Any]:
        """
        Parse NAV-SAT (Satellite Information) message.

        Args:
            msg: UBXMessage object containing NAV-SAT data

        Returns:
            Dictionary with parsed satellite data
        """
        try:
            satellites = []
            num_sv = getattr(msg, "numSV", getattr(msg, "numSvs", 0))

            # Iterate through satellite data
            for i in range(num_sv):
                sv_data = {
                    "gnss_id": getattr(msg, f"gnssId_{i}", 0),
                    "sv_id": getattr(msg, f"svId_{i}", 0),
                    "cno": getattr(msg, f"cno_{i}", 0),  # Carrier-to-noise ratio
                    "elev": getattr(msg, f"elev_{i}", 0),  # Elevation in degrees
                    "azim": getattr(msg, f"azim_{i}", 0),  # Azimuth in degrees
                    "pr_res": getattr(msg, f"prRes_{i}", 0) / 10.0,  # Pseudorange residual
                    "used": bool(getattr(msg, f"svUsed_{i}", getattr(msg, f"used_{i}", 0))),
                }
                satellites.append(sv_data)

            data = {
                "num_satellites": num_sv,
                "satellites": satellites,
                "timestamp": datetime.utcnow().isoformat(),
            }

            logger.debug(f"Parsed NAV-SAT: {num_sv} satellites")
            return data

        except Exception as e:
            logger.error(f"Error parsing NAV-SAT message: {e}")
            return {"num_satellites": 0, "satellites": [], "error": str(e)}

    @staticmethod
    def parse_ack(msg: UBXMessage) -> dict[str, Any]:
        """
        Parse ACK-ACK or ACK-NAK message.

        Args:
            msg: UBXMessage object containing ACK data

        Returns:
            Dictionary with parsed ACK/NAK data
        """
        try:
            msg_class = getattr(msg, "msgClass", 0)
            msg_id = getattr(msg, "msgID", 0)
            is_ack = msg.msg_id == "ACK-ACK"

            # Map message class to name
            msg_class_names = {
                0x01: "NAV",
                0x02: "RXM",
                0x03: "TRK",
                0x04: "INF",
                0x05: "ACK",
                0x06: "CFG",
                0x09: "UBX",
                0x0A: "MON",
                0x0B: "MGA",
            }
            class_name = msg_class_names.get(msg_class, f"0x{msg_class:02X}")

            data = {
                "is_ack": is_ack,
                "msg_class": msg_class,
                "msg_class_name": class_name,
                "msg_id": msg_id,
                "msg_id_hex": f"0x{msg_id:02X}",
                "timestamp": datetime.utcnow().isoformat(),
            }

            status = "ACK" if is_ack else "NAK"
            logger.debug(f"Parsed {status} for {class_name}-0x{msg_id:02X}")

            return data

        except Exception as e:
            logger.error(f"Error parsing ACK message: {e}")
            return {"is_ack": False, "error": str(e)}

    @staticmethod
    def parse_inf(msg: UBXMessage) -> dict[str, Any]:
        """
        Parse INF (Information) message.

        Args:
            msg: UBXMessage object containing INF data

        Returns:
            Dictionary with parsed INF message data
        """
        try:
            inf_type = msg.msg_id  # INF-DEBUG, INF-ERROR, INF-NOTICE, etc.
            payload = getattr(msg, "payload", b"")
            message = payload.decode("utf-8", errors="replace").strip("\x00")

            data = {
                "inf_type": inf_type,
                "message": message,
                "timestamp": datetime.utcnow().isoformat(),
            }

            logger.info(f"INF message [{inf_type}]: {message}")
            return data

        except Exception as e:
            logger.error(f"Error parsing INF message: {e}")
            return {"inf_type": "UNKNOWN", "message": "", "error": str(e)}

    @classmethod
    def parse_message(cls, msg: UBXMessage) -> dict[str, Any]:
        """
        Parse any UBX message to a dictionary.

        Dispatches to the appropriate parser based on message type.

        Args:
            msg: UBXMessage object to parse

        Returns:
            Dictionary with parsed message data
        """
        msg_class = getattr(msg, "msgClass", 0)
        msg_id_name = msg.identity  # always a str e.g. "NAV-PVT"

        # Route to appropriate parser
        if msg_id_name == "NAV-SVIN":
            return cls.parse_nav_svin(msg)
        elif msg_id_name == "NAV-PVT":
            return cls.parse_nav_pvt(msg)
        elif msg_id_name == "NAV-SAT":
            return cls.parse_nav_sat(msg)
        elif msg_id_name in ("ACK-ACK", "ACK-NAK"):
            return cls.parse_ack(msg)
        elif msg_id_name.startswith("INF-"):
            return cls.parse_inf(msg)
        else:
            # Generic parsing for unknown message types
            return {
                "msg_class": msg_class,
                "msg_id": msg_id_name,
                "payload": str(getattr(msg, "payload", b"")),
                "timestamp": datetime.utcnow().isoformat(),
            }
