"""
GNSS Command Generator.

Correct pyubx2 1.2.x API:
  - All CFG-VALSET via UBXMessage.config_set() with list-of-tuples cfgData.
  - NAV poll commands use POLL mode (2).
  - RTCM keys: CFG_MSGOUT_RTCM_3X_TYPE<num>_<port>  (no 'UBX_' prefix)
  - Protocol keys: CFG_UART1OUTPROT_RTCM3X / CFG_USBOUTPROT_RTCM3X
  - svinAccLimit unit: 0.1 mm  (accuracy_m * 10_000)
"""

import logging
from typing import Optional, Tuple

from pyubx2 import POLL, SET, SET_LAYER_BBR, SET_LAYER_FLASH, SET_LAYER_RAM, TXN_NONE, UBXMessage

logger = logging.getLogger(__name__)


def _llh_to_ubx_scale(
    lat: float,
    lon: float,
    height_m: float,
    use_high_precision: bool = False,
    lat_hp: Optional[float] = None,
    lon_hp: Optional[float] = None,
    height_hp: Optional[float] = None,
) -> Tuple[int, int, int, int, int, int]:
    """
    Convert LLH coordinates to u-blox UBX encoding.

    LAT/LON: degrees × 1e-7 (I4)
    HEIGHT: meters × 100 → cm (I4)

    HP fields (optional, for sub-cm precision):
    LAT_HP/LON_HP: degrees × 1e-9 (I1, range -99 to +99)
    HEIGHT_HP: meters × 10000 → 0.1mm (I1, range -99 to +99)

    Returns:
        Tuple of (lat_scaled, lon_scaled, height_cm, lat_hp, lon_hp, height_hp)
    """
    lat_scaled = int(lat * 1e7)
    lon_scaled = int(lon * 1e7)
    height_cm = int(height_m * 100)

    if use_high_precision:
        # HP fields are the fractional part beyond standard precision
        # LAT/LON HP: 1e-9 deg units, derived from residual after 1e-7 scaling
        if lat_hp is not None:
            lat_hp_val = int(lat_hp * 1e9)
            lat_hp_val = max(-99, min(99, lat_hp_val))
        else:
            lat_hp_val = int((lat * 1e7) - lat_scaled) * 10  # Approximate residual
            lat_hp_val = max(-99, min(99, lat_hp_val))

        if lon_hp is not None:
            lon_hp_val = int(lon_hp * 1e9)
            lon_hp_val = max(-99, min(99, lon_hp_val))
        else:
            lon_hp_val = int((lon * 1e7) - lon_scaled) * 10
            lon_hp_val = max(-99, min(99, lon_hp_val))

        # HEIGHT HP: 0.1mm units from fractional cm
        if height_hp is not None:
            height_hp_val = int(height_hp * 10000)
            height_hp_val = max(-99, min(99, height_hp_val))
        else:
            height_hp_val = int((height_m * 100) - height_cm) * 10
            height_hp_val = max(-99, min(99, height_hp_val))
    else:
        lat_hp_val = 0
        lon_hp_val = 0
        height_hp_val = 0

    return lat_scaled, lon_scaled, height_cm, lat_hp_val, lon_hp_val, height_hp_val

# MSM4 and MSM7 RTCM3 message type numbers
_MSM4 = [1074, 1084, 1094, 1124]   # GPS, GLONASS, Galileo, BeiDou
_MSM7 = [1077, 1087, 1097, 1127]
_ARP  = 1005                        # Reference Station ARP — required for RTK base
_GLO_CPB = 1230                     # GLONASS Code-Phase Biases — required for GLONASS RTK fixed

# Port names as used in configdb message-output keys
_PORTS = ["UART1", "UART2", "USB"]

# Output-protocol enable keys per port (verified against pyubx2 1.2.60 configdb)
_OUTPROT_RTCM3X = {
    "UART1": "CFG_UART1OUTPROT_RTCM3X",
    "UART2": "CFG_UART2OUTPROT_RTCM3X",
    "USB":   "CFG_USBOUTPROT_RTCM3X",
}


class GNSSCommands:
    """UBX command generator. All CFG-VALSET use UBXMessage.config_set()."""

    @staticmethod
    def create_survey_start_command(
        min_duration: int = 10,
        accuracy_limit: float = 0.10,
    ) -> UBXMessage:
        """
        CFG-VALSET: start survey-in mode.
        accuracy_limit in metres → 0.1 mm units (× 10_000).
        """
        accuracy_01mm = int(accuracy_limit * 10_000)
        msg = UBXMessage.config_set(
            layers=SET_LAYER_RAM,
            transaction=TXN_NONE,
            cfgData=[
                ("CFG_TMODE_MODE", 1),                    # 1 = Survey-In
                ("CFG_TMODE_SVIN_MIN_DUR", min_duration),
                ("CFG_TMODE_SVIN_ACC_LIMIT", accuracy_01mm),
            ],
        )
        logger.info(f"[CMD] Survey start: min_dur={min_duration}s  acc_limit={accuracy_limit}m ({accuracy_01mm} × 0.1mm)")
        return msg

    @staticmethod
    def create_survey_stop_command() -> UBXMessage:
        """CFG-VALSET: disable TMODE (stop survey-in / exit base mode)."""
        msg = UBXMessage.config_set(
            layers=SET_LAYER_RAM,
            transaction=TXN_NONE,
            cfgData=[("CFG_TMODE_MODE", 0)],  # 0 = Disabled
        )
        logger.info("[CMD] Survey stop (TMODE disabled)")
        return msg

    @staticmethod
    def create_fixed_mode_command(
        ecef_x: float,
        ecef_y: float,
        ecef_z: float,
        ecef_x_hp: float = 0.0,
        ecef_y_hp: float = 0.0,
        ecef_z_hp: float = 0.0,
    ) -> UBXMessage:
        """
        CFG-VALSET: fixed base station mode (ECEF coordinates).
        ecef_x/y/z in metres (stored as cm, I4).
        ecef_*_hp: fractional-metre offset stored as 0.1 mm (I1, range −99..+99).
        """
        msg = UBXMessage.config_set(
            layers=SET_LAYER_RAM,
            transaction=TXN_NONE,
            cfgData=[
                ("CFG_TMODE_MODE", 2),                               # 2 = Fixed
                ("CFG_TMODE_POS_TYPE", 0),                           # 0 = ECEF
                ("CFG_TMODE_ECEF_X", int(ecef_x * 100)),             # m → cm
                ("CFG_TMODE_ECEF_Y", int(ecef_y * 100)),
                ("CFG_TMODE_ECEF_Z", int(ecef_z * 100)),
                ("CFG_TMODE_ECEF_X_HP", int(ecef_x_hp * 10_000) % 100),  # 0.1 mm
                ("CFG_TMODE_ECEF_Y_HP", int(ecef_y_hp * 10_000) % 100),
                ("CFG_TMODE_ECEF_Z_HP", int(ecef_z_hp * 10_000) % 100),
            ],
        )
        logger.info(f"[CMD] Fixed mode: ({ecef_x:.3f}, {ecef_y:.3f}, {ecef_z:.3f}) m ECEF")
        return msg

    @staticmethod
    def create_fixed_llh_command(
        latitude: float,
        longitude: float,
        height: float,
        fixed_pos_acc: float = 0.10,
        use_high_precision: bool = False,
        lat_hp: Optional[float] = None,
        lon_hp: Optional[float] = None,
        height_hp: Optional[float] = None,
        layers: int = SET_LAYER_RAM,
    ) -> UBXMessage:
        """
        CFG-VALSET: fixed base station mode (LLH coordinates).

        Args:
            latitude: Latitude in decimal degrees (−90 to +90)
            longitude: Longitude in decimal degrees (−180 to +180)
            height: Ellipsoid height above WGS84 ellipsoid in meters (NOT MSL/orthometric)
            fixed_pos_acc: Fixed position accuracy in meters (default: 0.10m)
            use_high_precision: Enable HP fields for sub-cm precision
            lat_hp: Optional high-precision lat offset in degrees (±1e-9)
            lon_hp: Optional high-precision lon offset in degrees (±1e-9)
            height_hp: Optional high-precision height offset in meters
            layers: Memory layers (default: SET_LAYER_RAM)

        Returns:
            UBXMessage CFG-VALSET command
        """
        lat_scaled, lon_scaled, height_cm, lat_hp_val, lon_hp_val, height_hp_val = _llh_to_ubx_scale(
            latitude, longitude, height, use_high_precision, lat_hp, lon_hp, height_hp
        )
        acc_01mm = int(fixed_pos_acc * 10_000)  # meters → 0.1mm

        cfg_data = [
            ("CFG_TMODE_MODE", 2),                    # 2 = Fixed
            ("CFG_TMODE_POS_TYPE", 1),                # 1 = LLH
            ("CFG_TMODE_LAT", lat_scaled),            # deg × 1e-7
            ("CFG_TMODE_LON", lon_scaled),            # deg × 1e-7
            ("CFG_TMODE_HEIGHT", height_cm),          # m → cm
            ("CFG_TMODE_FIXED_POS_ACC", acc_01mm),    # m → 0.1mm
        ]

        if use_high_precision:
            cfg_data.extend([
                ("CFG_TMODE_LAT_HP", lat_hp_val),     # deg × 1e-9
                ("CFG_TMODE_LON_HP", lon_hp_val),     # deg × 1e-9
                ("CFG_TMODE_HEIGHT_HP", height_hp_val),  # m → 0.1mm
            ])

        msg = UBXMessage.config_set(
            layers=layers,
            transaction=TXN_NONE,
            cfgData=cfg_data,
        )
        logger.info(
            f"[CMD] Fixed LLH: lat={latitude:.7f} lon={longitude:.7f} h={height:.3f}m "
            f"acc={fixed_pos_acc:.3f}m layers={layers}"
        )
        return msg

    @staticmethod
    def create_rtcm_enable_command(msm_type: str = "MSM4") -> UBXMessage:
        """
        CFG-VALSET: enable RTCM3 output on UART1, UART2, and USB.
        Includes Reference Station ARP (1005) required for rover RTK.
        Also enables RTCM3X output protocol on all three ports.
        Explicitly disables the non-selected MSM family first so stale
        receiver config cannot leave MSM4 and MSM7 active at the same time.
        UBX and NMEA protocols are left untouched so the Pi retains
        UBX communication on UART1 for status monitoring.
        """
        selected = _MSM7 if msm_type.upper() == "MSM7" else _MSM4
        deselected = _MSM4 if msm_type.upper() == "MSM7" else _MSM7
        cfg_data: list = []

        # First clear the opposite MSM family on all ports.
        for msg_type in deselected:
            for port in _PORTS:
                cfg_data.append((f"CFG_MSGOUT_RTCM_3X_TYPE{msg_type}_{port}", 0))

        # Enable only the selected MSM family plus ARP and GLONASS CPB at 1 per epoch on each port.
        for msg_type in selected + [_ARP, _GLO_CPB]:
            for port in _PORTS:
                cfg_data.append((f"CFG_MSGOUT_RTCM_3X_TYPE{msg_type}_{port}", 1))

        # Enable RTCM3X output protocol on each port
        for port in _PORTS:
            cfg_data.append((_OUTPROT_RTCM3X[port], True))

        msg = UBXMessage.config_set(
            layers=SET_LAYER_RAM,
            transaction=TXN_NONE,
            cfgData=cfg_data,
        )
        logger.info(f"[CMD] RTCM enable {msm_type} on UART1/UART2/USB ({len(cfg_data)} keys)")
        return msg

    @staticmethod
    def create_rtcm_disable_command() -> UBXMessage:
        """CFG-VALSET: disable all RTCM3 MSM + ARP messages on all ports."""
        cfg_data: list = []
        for msg_type in _MSM4 + _MSM7 + [_ARP, _GLO_CPB]:
            for port in _PORTS:
                cfg_data.append((f"CFG_MSGOUT_RTCM_3X_TYPE{msg_type}_{port}", 0))
        msg = UBXMessage.config_set(
            layers=SET_LAYER_RAM,
            transaction=TXN_NONE,
            cfgData=cfg_data,
        )
        logger.info(f"[CMD] RTCM disable all ({len(cfg_data)} keys)")
        return msg

    @staticmethod
    def create_nav_svin_poll_command() -> UBXMessage:
        """NAV-SVIN poll: request current survey-in status from receiver."""
        return UBXMessage("NAV", "NAV-SVIN", POLL)

    @staticmethod
    def create_nav_pvt_poll_command() -> UBXMessage:
        """NAV-PVT poll: request position / velocity / time."""
        return UBXMessage("NAV", "NAV-PVT", POLL)

    @staticmethod
    def create_nav_sat_poll_command() -> UBXMessage:
        """NAV-SAT poll: request satellite status."""
        return UBXMessage("NAV", "NAV-SAT", POLL)

    @staticmethod
    def create_reset_command(
        nav_bbr_mask: int = 0x0000,
        reset_mode: int = 1,
    ) -> UBXMessage:
        """
        UBX-CFG-RST: Reset receiver.
        
        Args:
            nav_bbr_mask: Navigation BBR mask (default: 0x0000 = hot start).
                         0x0001 = Ephemeris, 0x0002 = Almanac, 0x0004 = Health,
                         0x0008 = Klobuchar, 0x0010 = Position, 0x0020 = Clock Drift,
                         0x0040 = Oscillator, 0x0080 = UTC, 0x0100 = RTC, 0x0200 = AOP
            reset_mode: Reset mode (0=HW reset, 1=Controlled SW reset, 2=HW/SW reset,
                       4=Controlled GNSS reset, 8=HW GNSS reset, 9=Controlled GNSS reset)
                       Default: 1 (hotstart / controlled SW reset)
        """
        msg = UBXMessage("CFG", "CFG-RST", SET, navBbrMask=nav_bbr_mask, resetMode=reset_mode)
        logger.info(f"[CMD] Reset: navBbrMask=0x{nav_bbr_mask:04X}  resetMode={reset_mode}")
        return msg

    @classmethod
    def create_base_mode_command(
        cls,
        msm_type: str = "MSM4",
        survey_mode: bool = True,
        min_duration: int = 300,
        accuracy_limit: float = 0.10,
    ) -> UBXMessage:
        """Convenience: start survey-in or stop TMODE."""
        if survey_mode:
            return cls.create_survey_start_command(min_duration, accuracy_limit)
        return cls.create_survey_stop_command()
