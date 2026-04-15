#!/usr/bin/env python3
"""Complete GNSS FastAPI Backend Endpoint Test Report"""

import requests
import json
from datetime import datetime

BASE_URL = "http://127.0.0.1:8000"

def test_get(url, name):
    """Test GET endpoint"""
    try:
        resp = requests.get(url, timeout=5)
        status = resp.status_code
        if status == 200:
            print(f"✓ PASS  {name}")
            print(f"        URL: {url}")
            print(f"        Status: {status}")
            return True
        elif status == 404:
            print(f"✗ FAIL  {name}")
            print(f"        URL: {url}")
            print(f"        Status: {status} (Not Found)")
            return False
        else:
            print(f"⚠ ERROR {name}")
            print(f"        URL: {url}")
            print(f"        Status: {status}")
            return False
    except Exception as e:
        print(f"✗ FAIL  {name}")
        print(f"        URL: {url}")
        print(f"        Error: {e}")
        return False

def test_post(url, name, data=None):
    """Test POST endpoint"""
    try:
        resp = requests.post(url, json=data, timeout=5)
        status = resp.status_code
        if status == 200:
            print(f"✓ PASS  {name}")
            print(f"        URL: {url}")
            print(f"        Status: {status}")
            return True
        elif status == 404:
            print(f"✗ FAIL  {name}")
            print(f"        URL: {url}")
            print(f"        Status: {status} (Not Found)")
            return False
        else:
            print(f"⚠ ERROR {name}")
            print(f"        URL: {url}")
            print(f"        Status: {status}")
            return False
    except Exception as e:
        print(f"✗ FAIL  {name}")
        print(f"        URL: {url}")
        print(f"        Error: {e}")
        return False

def main():
    print("=" * 80)
    print("           GNSS FASTAPI BACKEND - COMPLETE ENDPOINT TEST REPORT")
    print("=" * 80)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # GNSS Connection Status
    print("## GNSS CONNECTION STATUS")
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/status/receiver", timeout=5)
        if resp.status_code == 200:
            d = resp.json()
            print(f"  Serial Port:    {d.get('serial_port', 'N/A')}")
            print(f"  Baudrate:       {d.get('baudrate', 'N/A')}")
            print(f"  Connected:      {d.get('connected', False)}")
            print(f"  ACK Count:      {d.get('ack_count', 0)}")
            print(f"  NAK Count:      {d.get('nak_count', 0)}")
            print(f"  Error Count:    {d.get('error_count', 0)}")
    except Exception as e:
        print(f"  Error fetching receiver status: {e}")
    print()

    results = {"status": [], "config": [], "command": []}

    # Status Endpoints (Agent 1)
    print("=" * 80)
    print("                         STATUS ENDPOINTS (GET) - Agent 1")
    print("=" * 80)
    status_endpoints = [
        ("/health", "Health Check"),
        ("/info", "App Info"),
        ("/api/v1/status", "Full Status"),
        ("/api/v1/status/position", "Position Status"),
        ("/api/v1/status/survey", "Survey Status"),
        ("/api/v1/status/rtcm", "RTCM Status"),
        ("/api/v1/status/ntrip", "NTRIP Status"),
        ("/api/v1/status/receiver", "Receiver Status"),
        ("/api/v1/reader/status", "Reader Status"),
        ("/api/v1/autoflow/status", "AutoFlow Status"),
    ]
    for path, name in status_endpoints:
        results["status"].append(test_get(f"{BASE_URL}{path}", name))
    print()

    # Config Endpoints (Agent 2)
    print("=" * 80)
    print("                         CONFIG ENDPOINTS (GET/POST) - Agent 2")
    print("=" * 80)
    
    # GET /autoflow/config
    results["config"].append(test_get(f"{BASE_URL}/api/v1/autoflow/config", "GET /api/v1/autoflow/config"))
    
    # POST /autoflow/config
    config_data = {
        "enabled": False,
        "min_duration_sec": 60,
        "accuracy_limit_m": 0.5,
        "msm_type": "MSM4",
        "ntrip_host": "",
        "ntrip_port": 2101,
        "ntrip_mountpoint": "",
        "ntrip_username": "",
        "ntrip_password": "",
        "ntrip_version": 1
    }
    results["config"].append(test_post(f"{BASE_URL}/api/v1/autoflow/config", "POST /api/v1/autoflow/config", config_data))
    
    # POST /autoflow/enable
    results["config"].append(test_post(f"{BASE_URL}/api/v1/autoflow/enable", "POST /api/v1/autoflow/enable"))
    
    # POST /autoflow/disable
    results["config"].append(test_post(f"{BASE_URL}/api/v1/autoflow/disable", "POST /api/v1/autoflow/disable"))
    print()

    # Command Endpoints (Agent 3)
    print("=" * 80)
    print("                         COMMAND ENDPOINTS (POST) - Agent 3")
    print("=" * 80)
    
    command_endpoints = [
        ("/api/v1/receiver/reset", "Receiver Reset", {}),
        ("/api/v1/survey/start", "Survey Start", {"min_duration": 60, "accuracy_limit": 0.5}),
        ("/api/v1/survey/stop", "Survey Stop", {}),
        ("/api/v1/rtcm/configure", "RTCM Configure", {"enable": True, "msm_type": "MSM4"}),
        ("/api/v1/mode/base", "Base Mode", {"msm_type": "MSM4", "survey_mode": True, "min_duration": 60, "accuracy_limit": 0.5}),
        ("/api/v1/autoflow/start", "AutoFlow Start", {}),
        ("/api/v1/autoflow/stop", "AutoFlow Stop", {}),
        ("/api/v1/reader/reconnect", "Reader Reconnect", {}),
    ]
    for path, name, data in command_endpoints:
        results["command"].append(test_post(f"{BASE_URL}{path}", name, data))
    print()

    # Summary
    print("=" * 80)
    print("                              SUMMARY")
    print("=" * 80)
    
    status_pass = sum(results["status"])
    status_total = len(results["status"])
    config_pass = sum(results["config"])
    config_total = len(results["config"])
    command_pass = sum(results["command"])
    command_total = len(results["command"])
    total_pass = status_pass + config_pass + command_pass
    total_endpoints = status_total + config_total + command_total
    
    print(f"GNSS Connection:      {'CONNECTED' if d.get('connected') else 'DISCONNECTED'} (/dev/ttyAMA0 @ 9600 baud)")
    print(f"Status Endpoints:     {status_pass}/{status_total} {'PASS' if status_pass == status_total else 'FAIL'} ({100*status_pass//status_total if status_total else 0}%)")
    print(f"Config Endpoints:     {config_pass}/{config_total} {'PASS' if config_pass == config_total else 'FAIL'} ({100*config_pass//config_total if config_total else 0}%)")
    print(f"Command Endpoints:    {command_pass}/{command_total} {'PASS' if command_pass == command_total else 'FAIL'} ({100*command_pass//command_total if command_total else 0}%)")
    print(f"TOTAL:                {total_pass}/{total_endpoints} ENDPOINTS WORKING ({100*total_pass//total_endpoints if total_endpoints else 0}%)")
    print("=" * 80)

if __name__ == "__main__":
    main()
