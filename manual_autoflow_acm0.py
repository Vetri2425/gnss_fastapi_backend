#!/usr/bin/env python3
"""
Manual AutoFlow runner over /dev/ttyACM0.

Runs the base-station flow directly against the receiver:
1. Disable RTCM and stop TMODE
2. Start survey-in using saved autoflow config
3. Wait for survey validity
4. Enable RTCM output per config
5. Push RTCM to the configured NTRIP caster and verify traffic
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import serial
from pyubx2 import RTCM3_PROTOCOL, UBX_PROTOCOL, UBXReader

from app.gnss.commands import GNSSCommands
from app.gnss.ntrip_push import NTRIPPushClient


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("manual_autoflow_acm0")


CONFIG_PATH = Path("data/autoflow_config.json")
PORT = "/dev/ttyACM0"
BAUD = 9600
TIMEOUT = 2


def ident_of(parsed) -> str:
    return getattr(parsed, "identity", type(parsed).__name__)


def mask_config(cfg: dict) -> dict:
    masked = dict(cfg)
    if "ntrip_password" in masked and masked["ntrip_password"]:
        masked["ntrip_password"] = "***"
    return masked


def wait_for_ack(ubr: UBXReader, timeout: float = 5.0):
    end = time.time() + timeout
    seen: list[str] = []
    while time.time() < end:
        raw, parsed = ubr.read()
        if parsed is None:
            continue
        ident = ident_of(parsed)
        seen.append(ident)
        if ident == "ACK-ACK":
            return True, seen, parsed
        if ident == "ACK-NAK":
            return False, seen, parsed
    return None, seen, None


def open_reader():
    ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)
    ubr = UBXReader(ser, protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL)
    ser.reset_input_buffer()
    time.sleep(0.25)
    return ser, ubr


def send_command_for_ack(label: str, cmd, timeout: float = 6.0):
    attempts = 3
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with serial.Serial(PORT, BAUD, timeout=TIMEOUT) as ser:
                ubr = UBXReader(ser, protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL)
                ser.reset_input_buffer()
                time.sleep(0.25)
                ser.write(cmd.serialize())
                ser.flush()
                ack, seen, _ = wait_for_ack(ubr, timeout=timeout)
                print(f"  {label}: ack={ack} seen={seen[:8]} attempt={attempt}")
                return ack, seen
        except serial.SerialException as exc:
            last_error = exc
            print(f"  {label}: serial retry {attempt}/{attempts} after error: {exc}")
            time.sleep(2.0)
    raise last_error


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print("CONFIG", json.dumps(mask_config(cfg), indent=2))

    survey_timeout = 86400

    print("\nSTEP 1: Disable RTCM and stop TMODE")
    send_command_for_ack("disable_rtcm", GNSSCommands.create_rtcm_disable_command(), timeout=8)
    time.sleep(1.0)
    send_command_for_ack("stop_survey", GNSSCommands.create_survey_stop_command(), timeout=8)
    time.sleep(1.0)

    print("\nSTEP 2: Start survey-in")
    send_command_for_ack(
        "survey_start",
        GNSSCommands.create_survey_start_command(
            min_duration=cfg["min_duration_sec"],
            accuracy_limit=cfg["accuracy_limit_m"],
        ),
        timeout=8,
    )

    print("\nSTEP 3: Monitor survey until valid")
    with serial.Serial(PORT, BAUD, timeout=TIMEOUT) as ser:
        ubr = UBXReader(ser, protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL)
        ser.reset_input_buffer()
        time.sleep(0.25)
        survey_valid = False
        last_report = 0.0
        start = time.time()
        last_svin = None
        while time.time() - start < survey_timeout:
            raw, parsed = ubr.read()
            if parsed is None:
                continue
            ident = ident_of(parsed)
            if ident != "NAV-SVIN":
                continue
            last_svin = parsed
            dur = getattr(parsed, "dur", 0)
            mean_acc_m = getattr(parsed, "meanAcc", 0) / 10000.0
            active = bool(getattr(parsed, "active", 0))
            valid = bool(getattr(parsed, "valid", 0))
            obs = getattr(parsed, "obs", 0)
            now = time.time()
            if now - last_report >= 2.0 or valid:
                print(
                    f"  survey: dur={dur}s obs={obs} active={active} "
                    f"valid={valid} meanAcc={mean_acc_m:.4f}m"
                )
                last_report = now
            if valid:
                survey_valid = True
                break

        if not survey_valid:
            print("\nRESULT: Survey did not become valid within timeout.")
            if last_svin is not None:
                print(
                    "LAST_SVIN",
                    {
                        "dur": getattr(last_svin, "dur", None),
                        "obs": getattr(last_svin, "obs", None),
                        "active": bool(getattr(last_svin, "active", 0)),
                        "valid": bool(getattr(last_svin, "valid", 0)),
                        "meanAcc_m": getattr(last_svin, "meanAcc", 0) / 10000.0,
                    },
                )
            return 2

    print("\nSTEP 4: Enable RTCM per config")
    send_command_for_ack(
        f"rtcm_enable_{cfg['msm_type']}",
        GNSSCommands.create_rtcm_enable_command(cfg["msm_type"]),
        timeout=10,
    )
    time.sleep(2.0)

    print("\nSTEP 5: Start NTRIP push and verify RTCM flow")
    push = NTRIPPushClient(
        host=cfg["ntrip_host"],
        port=cfg["ntrip_port"],
        mountpoint=cfg["ntrip_mountpoint"],
        username=cfg.get("ntrip_username", "") or "",
        password=cfg["ntrip_password"],
        ntrip_version=cfg.get("ntrip_version", 1),
        max_retries=1,
        base_delay=2.0,
        max_delay=5.0,
    )
    push.start()

    rtcm_counts: dict[str, int] = {}
    rtcm_bytes = 0
    with serial.Serial(PORT, BAUD, timeout=TIMEOUT) as ser:
        ubr = UBXReader(ser, protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL)
        ser.reset_input_buffer()
        time.sleep(0.25)
        verify_start = time.time()
        while time.time() - verify_start < 20:
            raw, parsed = ubr.read()
            if parsed is None:
                continue
            ident = ident_of(parsed)
            if ident.isdigit():
                rtcm_counts[ident] = rtcm_counts.get(ident, 0) + 1
                rtcm_bytes += len(raw)
                push.put_rtcm(raw)
            time.sleep(0.001)

    time.sleep(2.0)
    status = push.get_status()
    push.stop(timeout=3.0)

    print("  rtcm_counts", rtcm_counts)
    print("  rtcm_bytes", rtcm_bytes)
    print(
        "  ntrip_status",
        {
            k: v
            for k, v in status.items()
            if k
            in {
                "connected",
                "bytes_sent",
                "connect_attempts",
                "host",
                "port",
                "mountpoint",
                "gave_up",
                "data_rate_bps",
                "stale",
                "last_error",
            }
        },
    )

    print("\nRESULT: Manual flow completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
