#!/usr/bin/env python3
"""
GNSS FastAPI Backend — Endpoint Test Suite
==========================================
Tests all REST API endpoints against a running service.

Usage:
    python test_api.py                    # safe read-only tests only
    python test_api.py --hardware         # include tests that send UBX commands
    python test_api.py --host 192.168.1.x # test remote host
    python test_api.py --port 8001        # custom port
"""

import argparse
import json
import sys
import time
from typing import Any

import requests

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
SKIP = f"{YELLOW}SKIP{RESET}"
WARN = f"{YELLOW}WARN{RESET}"


# ── Result tracking ───────────────────────────────────────────────────────────
results: list[dict] = []


def record(name: str, status: str, detail: str = "", latency_ms: float = 0.0) -> None:
    results.append({"name": name, "status": status, "detail": detail, "latency_ms": latency_ms})
    icon = {"PASS": PASS, "FAIL": FAIL, "SKIP": SKIP, "WARN": WARN}.get(status, status)
    lat  = f"{DIM}({latency_ms:.0f}ms){RESET}" if latency_ms else ""
    print(f"  {icon}  {name} {lat}")
    if detail and status in ("FAIL", "WARN"):
        print(f"       {DIM}{detail}{RESET}")


# ── Core request helper ───────────────────────────────────────────────────────
def req(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    expect_status: int = 200,
    required_fields: list[str] | None = None,
    forbidden_values: dict[str, Any] | None = None,
    expect_json: bool = True,
    name: str = "",
    skip: bool = False,
    skip_reason: str = "",
) -> dict | None:
    label = name or f"{method} {url}"

    if skip:
        record(label, "SKIP", skip_reason)
        return None

    t0 = time.monotonic()
    try:
        resp = requests.request(
            method,
            url,
            json=body,
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        latency = (time.monotonic() - t0) * 1000
    except requests.ConnectionError as e:
        record(label, "FAIL", f"Connection refused: {e}")
        return None
    except requests.Timeout:
        record(label, "FAIL", "Request timed out (>10s)")
        return None

    # Status check
    if resp.status_code != expect_status:
        record(label, "FAIL", f"status={resp.status_code} (expected {expect_status})  body={resp.text[:200]}", latency)
        return None

    # Parse JSON
    if not expect_json:
        record(label, "PASS", latency_ms=latency)
        return None
    try:
        data = resp.json()
    except Exception:
        record(label, "WARN", f"Non-JSON response: {resp.text[:100]}", latency)
        return None

    # Required field check
    if required_fields:
        missing = [f for f in required_fields if f not in data]
        if missing:
            record(label, "FAIL", f"Missing fields: {missing}", latency)
            return None

    # Forbidden value check (e.g. field must not equal some bad value)
    if forbidden_values:
        for field, bad_val in forbidden_values.items():
            if data.get(field) == bad_val:
                record(label, "WARN", f"Field '{field}' = {bad_val!r} (unexpected)", latency)
                return data

    record(label, "PASS", latency_ms=latency)
    return data


def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─'*55}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*55}{RESET}")


# ── Test groups ───────────────────────────────────────────────────────────────

def test_root(base: str) -> None:
    section("Root / Health / Info")
    req("GET", f"{base}/",          name="GET /  (root info)",
        required_fields=["name", "version", "endpoints"])
    req("GET", f"{base}/health",    name="GET /health",
        required_fields=["status", "reader_running", "reader_connected"])
    req("GET", f"{base}/info",      name="GET /info",
        required_fields=["config"])
    req("GET", f"{base}/openapi.json", name="GET /openapi.json",
        required_fields=["openapi", "paths"])
    req("GET", f"{base}/docs",      name="GET /docs  (Swagger UI)",  expect_json=False)
    req("GET", f"{base}/redoc",     name="GET /redoc  (ReDoc UI)",   expect_json=False)


def test_status(base: str) -> None:
    section("Status Endpoints")
    req("GET", f"{base}/api/v1/status",
        name="GET /api/v1/status  (full)",
        required_fields=["position", "survey", "rtcm", "ntrip", "receiver"])

    req("GET", f"{base}/api/v1/status/position",
        name="GET /status/position",
        required_fields=["latitude", "longitude", "altitude", "fix_type", "fix_type_str",
                         "num_satellites", "accuracy", "timestamp"])

    req("GET", f"{base}/api/v1/status/survey",
        name="GET /status/survey",
        required_fields=["active", "valid", "in_progress", "progress",
                         "accuracy", "observation_time", "ecef_x", "ecef_y", "ecef_z"])

    req("GET", f"{base}/api/v1/status/rtcm",
        name="GET /status/rtcm",
        required_fields=["enabled", "msm_type", "message_counts", "data_rate",
                         "total_messages_sent"])

    req("GET", f"{base}/api/v1/status/ntrip",
        name="GET /status/ntrip",
        required_fields=["enabled", "connected", "host", "port",
                         "mountpoint", "bytes_sent", "bytes_received"])

    req("GET", f"{base}/api/v1/status/receiver",
        name="GET /status/receiver",
        required_fields=["connected", "serial_port", "baudrate",
                         "error_count", "nak_count", "ack_count"])


def test_reader(base: str, hardware: bool) -> None:
    section("Reader Endpoints")
    req("GET", f"{base}/api/v1/reader/status",
        name="GET /reader/status",
        required_fields=["is_running", "is_connected", "messages_read",
                         "parse_errors", "inbound_queue_size", "outbound_queue_size"])

    req("POST", f"{base}/api/v1/reader/reconnect",
        name="POST /reader/reconnect",
        skip=not hardware,
        skip_reason="--hardware flag required (restarts serial thread)")


def test_commands_readonly(base: str) -> None:
    section("Generic Command — Read-Only Polls")
    req("POST", f"{base}/api/v1/command",
        body={"type": "poll_pvt", "params": {}},
        name="POST /command  poll_pvt",
        required_fields=["success", "message"])

    req("POST", f"{base}/api/v1/command",
        body={"type": "poll_svin", "params": {}},
        name="POST /command  poll_svin",
        required_fields=["success", "message"])

    req("POST", f"{base}/api/v1/command",
        body={"type": "poll_sat", "params": {}},
        name="POST /command  poll_sat",
        required_fields=["success", "message"])


def test_commands_validation(base: str) -> None:
    section("Generic Command — Validation Errors")
    req("POST", f"{base}/api/v1/command",
        body={"type": "unknown_cmd", "params": {}},
        name="POST /command  unknown type → 400",
        expect_status=400)

    req("POST", f"{base}/api/v1/command",
        body={},
        name="POST /command  missing type field → 422",
        expect_status=422)

    req("POST", f"{base}/api/v1/command",
        body="not-json",
        name="POST /command  bad body type → 422",
        expect_status=422)


def test_survey(base: str, hardware: bool) -> None:
    section("Survey Endpoints")
    req("POST", f"{base}/api/v1/survey/start",
        body={"min_duration": 300, "accuracy_limit": 0.10},
        name="POST /survey/start",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required (sends UBX command to receiver)")

    req("POST", f"{base}/api/v1/survey/stop",
        name="POST /survey/stop",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required")

    # Validation: min_duration below minimum (60)
    req("POST", f"{base}/api/v1/survey/start",
        body={"min_duration": 10, "accuracy_limit": 0.10},
        name="POST /survey/start  min_duration too low → 422",
        expect_status=422)

    # Validation: accuracy_limit out of range
    req("POST", f"{base}/api/v1/survey/start",
        body={"min_duration": 300, "accuracy_limit": 99.0},
        name="POST /survey/start  accuracy_limit too high → 422",
        expect_status=422)


def test_rtcm(base: str, hardware: bool) -> None:
    section("RTCM Endpoints")
    req("POST", f"{base}/api/v1/rtcm/configure",
        body={"msm_type": "MSM4", "enable": True},
        name="POST /rtcm/configure  MSM4 enable",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required")

    req("POST", f"{base}/api/v1/rtcm/configure",
        body={"msm_type": "MSM7", "enable": False},
        name="POST /rtcm/configure  MSM7 disable",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required")

    # Validation: bad msm_type
    req("POST", f"{base}/api/v1/rtcm/configure",
        body={"msm_type": "MSM9", "enable": True},
        name="POST /rtcm/configure  bad msm_type → 422",
        expect_status=422)


def test_base_mode(base: str, hardware: bool) -> None:
    section("Base Mode Endpoint")
    req("POST", f"{base}/api/v1/mode/base",
        body={"msm_type": "MSM4", "survey_mode": True,
              "min_duration": 300, "accuracy_limit": 0.10},
        name="POST /mode/base  survey mode",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required")

    # Fixed mode missing coordinates → 400
    req("POST", f"{base}/api/v1/mode/base",
        body={"msm_type": "MSM4", "survey_mode": False},
        name="POST /mode/base  fixed mode missing ECEF → 400",
        expect_status=400,
        skip=not hardware,
        skip_reason="--hardware flag required")


def test_autoflow(base: str, hardware: bool) -> None:
    section("AutoFlow — Status & Config (read)")
    req("GET", f"{base}/api/v1/autoflow/status",
        name="GET /autoflow/status",
        required_fields=["state", "enabled", "last_error"])

    req("GET", f"{base}/api/v1/autoflow/config",
        name="GET /autoflow/config",
        required_fields=["enabled", "min_duration_sec", "accuracy_limit_m",
                         "msm_type", "ntrip_host", "ntrip_port", "ntrip_mountpoint",
                         "ntrip_username"])

    section("AutoFlow — Config Save (non-destructive)")
    # Save config with enabled=false — safe, no hardware side effects
    req("POST", f"{base}/api/v1/autoflow/config",
        body={
            "enabled": False,
            "min_duration_sec": 300,
            "accuracy_limit_m": 0.10,
            "msm_type": "MSM4",
            "ntrip_host": "",
            "ntrip_port": 2101,
            "ntrip_mountpoint": "",
            "ntrip_username": "",
            "ntrip_password": "",
            "ntrip_version": 1,
        },
        name="POST /autoflow/config  (enabled=false, safe save)",
        required_fields=["success", "message"])

    # Validation: bad msm_type
    req("POST", f"{base}/api/v1/autoflow/config",
        body={"enabled": False, "msm_type": "MSM9",
              "min_duration_sec": 300, "accuracy_limit_m": 0.10,
              "ntrip_host": "", "ntrip_port": 2101, "ntrip_mountpoint": "",
              "ntrip_username": "", "ntrip_password": "", "ntrip_version": 1},
        name="POST /autoflow/config  bad msm_type → 422",
        expect_status=422)

    # Validation: min_duration_sec too low
    req("POST", f"{base}/api/v1/autoflow/config",
        body={"enabled": False, "msm_type": "MSM4",
              "min_duration_sec": 10, "accuracy_limit_m": 0.10,
              "ntrip_host": "", "ntrip_port": 2101, "ntrip_mountpoint": "",
              "ntrip_username": "", "ntrip_password": "", "ntrip_version": 1},
        name="POST /autoflow/config  min_duration_sec too low → 422",
        expect_status=422)

    section("AutoFlow — Control (hardware)")
    req("POST", f"{base}/api/v1/autoflow/enable",
        name="POST /autoflow/enable",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required (triggers autoflow run)")

    req("POST", f"{base}/api/v1/autoflow/disable",
        name="POST /autoflow/disable",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required (aborts autoflow run)")

    req("POST", f"{base}/api/v1/autoflow/start",
        name="POST /autoflow/start",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required")

    req("POST", f"{base}/api/v1/autoflow/stop",
        name="POST /autoflow/stop",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required")


def test_receiver(base: str, hardware: bool) -> None:
    section("Receiver Reset")
    req("POST", f"{base}/api/v1/receiver/reset",
        name="POST /receiver/reset  (UBX hotstart)",
        required_fields=["success", "message"],
        skip=not hardware,
        skip_reason="--hardware flag required (resets GNSS chip)")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary() -> int:
    passed  = [r for r in results if r["status"] == "PASS"]
    failed  = [r for r in results if r["status"] == "FAIL"]
    warned  = [r for r in results if r["status"] == "WARN"]
    skipped = [r for r in results if r["status"] == "SKIP"]
    total   = len(results)

    print(f"\n{BOLD}{'═'*55}{RESET}")
    print(f"{BOLD}  TEST SUMMARY{RESET}")
    print(f"{BOLD}{'═'*55}{RESET}")
    print(f"  Total    : {total}")
    print(f"  {GREEN}Passed   : {len(passed)}{RESET}")
    print(f"  {RED}Failed   : {len(failed)}{RESET}")
    print(f"  {YELLOW}Warnings : {len(warned)}{RESET}")
    print(f"  {YELLOW}Skipped  : {len(skipped)}{RESET}")

    if passed:
        avg_lat = sum(r["latency_ms"] for r in passed) / len(passed)
        max_lat = max(r["latency_ms"] for r in passed)
        print(f"\n  Latency  : avg={avg_lat:.0f}ms  max={max_lat:.0f}ms")

    if failed:
        print(f"\n{RED}{BOLD}  Failed tests:{RESET}")
        for r in failed:
            print(f"  {RED}✗{RESET}  {r['name']}")
            if r["detail"]:
                print(f"     {DIM}{r['detail']}{RESET}")

    if warned:
        print(f"\n{YELLOW}{BOLD}  Warnings:{RESET}")
        for r in warned:
            print(f"  {YELLOW}!{RESET}  {r['name']}: {r['detail']}")

    print(f"\n{BOLD}{'═'*55}{RESET}")

    if failed:
        print(f"  {RED}{BOLD}RESULT: FAILED ({len(failed)} test(s) failed){RESET}\n")
        return 1
    else:
        print(f"  {GREEN}{BOLD}RESULT: ALL PASSED{RESET}\n")
        return 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="GNSS FastAPI endpoint test suite")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--port", default=8000, type=int, help="Server port (default: 8000)")
    parser.add_argument("--hardware", action="store_true",
                        help="Include tests that send UBX commands to the receiver")
    args = parser.parse_args()

    base = f"http://{args.host}:{args.port}"

    print(f"\n{BOLD}GNSS FastAPI Backend — Endpoint Test Suite{RESET}")
    print(f"Target : {CYAN}{base}{RESET}")
    print(f"Mode   : {'hardware + safe' if args.hardware else 'safe (read-only)'}")
    if not args.hardware:
        print(f"{DIM}  Tip: run with --hardware to include UBX command tests{RESET}")

    # Quick connectivity check
    try:
        requests.get(f"{base}/health", timeout=3)
    except requests.ConnectionError:
        print(f"\n{RED}ERROR: Cannot connect to {base} — is the service running?{RESET}")
        print(f"  sudo systemctl status gnss-backend\n")
        sys.exit(2)

    test_root(base)
    test_status(base)
    test_reader(base, args.hardware)
    test_commands_readonly(base)
    test_commands_validation(base)
    test_survey(base, args.hardware)
    test_rtcm(base, args.hardware)
    test_base_mode(base, args.hardware)
    test_autoflow(base, args.hardware)
    test_receiver(base, args.hardware)

    sys.exit(print_summary())


if __name__ == "__main__":
    main()
