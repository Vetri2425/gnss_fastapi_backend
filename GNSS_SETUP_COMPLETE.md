# GNSS FastAPI Backend — Complete Setup Summary

**Date:** March 30, 2026
**Status:** ✅ FULLY OPERATIONAL

---

## Hardware Setup

### Wiring
- **ZED-F9P USB** → Pi5 USB port → `/dev/ttyACM0` (CDC-ACM)
- **ZED-F9P UART2** → Pi5 GPIO UART1 (pins 8/10) → `/dev/ttyAMA0` @ 38400 baud
- **ZED-F9P UART1** — not connected

### UART2 Configuration (One-Time)
Run **once** with USB connected:
```bash
python3 setup_uart2.py
```
Configures receiver UART2 for UBX I/O and saves to FLASH. After this, USB is not needed.

**What it sets:**
- CFG_UART2_BAUDRATE = 38400
- CFG_UART2INPROT_UBX = True (Pi → receiver: commands)
- CFG_UART2OUTPROT_UBX = True (receiver → Pi: NAV-PVT, NAV-SVIN)
- Saves to RAM + BBR + FLASH (survives power cycles)

---

## Application Architecture

### Startup Sequence
```
main.py lifespan
  ├─ GNSSState (thread-safe state holder)
  ├─ GNSSReader (opens ttyAMA0, reads UBX messages)
  ├─ Socket.IO server (WebSocket for frontend)
  ├─ AutoflowOrchestrator (base station state machine)
  └─ WebSocket handler (broadcasts GNSS data)
```

### Core Modules
| Module | Purpose |
|---|---|
| `config.py` | Env var config (port, baud, survey params, NTRIP) |
| `gnss/reader.py` | Threaded UBX reader on ttyAMA0 |
| `gnss/parser.py` | Parse NAV-PVT, NAV-SVIN, ACK, INF messages |
| `gnss/state.py` | Thread-safe GNSS state (position, survey, RTCM, NTRIP) |
| `gnss/commands.py` | UBX command generator (survey start/stop, RTCM enable) |
| `gnss/autoflow.py` | Base station orchestrator (SURVEY → RTCM → NTRIP → STREAMING) |
| `gnss/ntrip_push.py` | NTRIP caster push client |
| `gnss/uart2_config.py` | One-time UART2 setup (used by setup_uart2.py) |

### AutoFlow State Machine

```
IDLE
  └─► WAITING_SERIAL (wait for ttyAMA0 connection)
        └─► SURVEY (send TMODE=1, poll NAV-SVIN every 5s)
              └─► ENABLING_RTCM (send CFG-VALSET enable MSM7 + ARP)
                    └─► NTRIP_CONNECT (if host configured, connect to caster)
                          └─► STREAMING (push RTCM to caster)
                    └─► STREAMING (if no NTRIP, RTCM on serial only)
  └─► FAILED (unrecoverable error)
```

**Key Features:**
- Phase 1b (new): Stop any active RTCM + TMODE before starting fresh survey
- Survey stall detection: auto-restarts after 10 stuck polls
- NTRIP auto-reconnect on disconnect
- All phases logged to journalctl

### RTCM Messages Enabled

| Message | Name | Rate | Purpose |
|---|---|---|---|
| 1005 | Reference Station ARP | 1 Hz | Base position (required for rover RTK) |
| 1077 | GPS MSM7 | 1 Hz | GPS observations (full) |
| 1087 | GLONASS MSM7 | 1 Hz | GLONASS observations (full) |
| 1097 | Galileo MSM7 | 1 Hz | Galileo observations (full) |
| 1127 | BeiDou MSM7 | 1 Hz | BeiDou observations (full) |
| 1230 | GLONASS biases | 1 Hz | Bias correction |

Output ports: **UART1, UART2, USB** (all enabled by autoflow)

---

## Configuration (Environment Variables)

### Serial Port
```bash
SERIAL_PORT=/dev/ttyAMA0          # GPIO UART
SERIAL_BAUDRATE=38400             # Must match UART2 config
SERIAL_TIMEOUT=1.0
```

### Survey-In Parameters
```bash
SURVEY_MIN_DURATION=120            # seconds (default 300)
SURVEY_ACCURACY_THRESHOLD=2.0      # meters
```

### RTCM Output
```bash
RTCM_MSM_TYPE=MSM7                 # or MSM4
RTCM_MESSAGE_INTERVAL=1000         # ms (not used; uses 1 Hz from CFG)
```

### NTRIP (Base Station Push)
```bash
NTRIP_ENABLED=false                # set True to enable push
NTRIP_HOST=caster.emlid.com
NTRIP_PORT=2101
NTRIP_MOUNTPOINT=MP23960
NTRIP_USERNAME=u98264
NTRIP_PASSWORD=953ztv
NTRIP_VERSION=1
```

Persisted in `data/autoflow_config.json`.

---

## API Endpoints

### REST (HTTP)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/status` | GET | Full system state (position, survey, RTCM, NTRIP, receiver, reader) |
| `/api/v1/survey` | GET | Survey-in status |
| `/api/v1/autoflow/status` | GET | AutoFlow state + error + NTRIP status |
| `/api/v1/autoflow/config` | GET | Current config (no password) |
| `/api/v1/autoflow/start` | POST | Trigger run (ignores if already running) |
| `/api/v1/autoflow/stop` | POST | Abort run, return to IDLE |
| `/api/v1/autoflow/enable` | POST | Set enabled=true, trigger run, save config |
| `/api/v1/autoflow/disable` | POST | Set enabled=false, abort, save config |

### WebSocket (Socket.IO at `/ws`)

| Event | Direction | Content |
|---|---|---|
| `gnss_data` | → client | Position, survey, RTCM, NTRIP, receiver, reader |
| `autoflow_state` | → client | Autoflow state change + config + NTRIP |
| `autoflow_progress` | → client | Survey progress (obs_time, accuracy, active, valid) |
| `autoflow_ntrip` | → client | NTRIP connection status updates |
| `autoflow_error` | → client | Error message |
| `ntrip_status` | → client | NTRIP client status (connected, bytes_sent, data_rate, etc.) |

---

## Testing & Verification

### Check Port Configuration
```bash
# Verify UART2 output messages (USB, non-intrusive):
python3 -c "
import serial, time
from pyubx2 import UBXReader, GET, RTCM3_PROTOCOL
with serial.Serial('/dev/ttyACM0', 9600, timeout=1) as s:
    ubr = UBXReader(s, msgmode=GET, protfilter=RTCM3_PROTOCOL)
    counts = {}
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            raw, _ = ubr.read()
            if raw and raw[0] == 0xD3:
                mt = ((raw[3] << 4) | (raw[4] >> 4))
                counts[mt] = counts.get(mt, 0) + 1
        except: pass
    for mt in sorted(counts):
        print(f'RTCM {mt}: {counts[mt]} frames')
"
```

Expected output: 1005, 1077, 1087, 1097, 1127, 1230 all present.

### Check API Status
```bash
curl http://localhost:8000/api/v1/status | jq .receiver
curl http://localhost:8000/api/v1/survey | jq .
curl http://localhost:8000/api/v1/autoflow/status | jq .
```

### Start/Stop AutoFlow
```bash
curl -X POST http://localhost:8000/api/v1/autoflow/enable
curl -X POST http://localhost:8000/api/v1/autoflow/disable
curl -X POST http://localhost:8000/api/v1/autoflow/start
curl -X POST http://localhost:8000/api/v1/autoflow/stop
```

---

## Issue Resolution (2026-03-30)

### Root Cause Found & Fixed
**Problem:** NAV-PVT, NAV-SVIN messages were NOT being output on UART2, only RTCM.

**Root Cause:** The `setup_uart2.py` script only enabled the UBX protocol on UART2, but did NOT enable the actual message rates:
- `CFG_MSGOUT_UBX_NAV_PVT_UART2` = 0 (disabled)
- `CFG_MSGOUT_UBX_NAV_SVIN_UART2` = 0 (disabled)
- `CFG_MSGOUT_UBX_NAV_SAT_UART2` = 0 (disabled)

**Solution:** Updated `uart2_config.py` to include these three CFG keys with rate=1 (1 Hz output).

### Verification Steps Taken
1. ✅ Direct UART2 → ttyAMA0 sniff: 30 seconds → 0 UBX frames (only RTCM 1230)
2. ✅ USB diagnostic: Confirmed NAV-PVT/SVIN/SAT were disabled in receiver config
3. ✅ Updated `setup_uart2.py` with message rate keys
4. ✅ Ran setup script → receiver ACK received
5. ✅ Verified NAV-PVT now flowing on ttyAMA0 (fix=3, 31 satellites)
6. ✅ Restarted application → position data populated
7. ✅ AutoFlow SURVEY → RTCM → NTRIP → STREAMING completed successfully

### Result
- **Position:** 13.0720436°N, 80.2619379°E, 10.6m (3D fix)
- **Satellites:** 31 (excellent)
- **Accuracy:** 0.663m (excellent)
- **PDOP:** 0.93 (excellent)
- **RTCM:** Streaming at 863 bps to caster.emlid.com

---

## Files Modified

| File | Changes |
|---|---|
| `app/config.py` | No changes (removed UART2_CONFIG vars) |
| `app/main.py` | Removed USB config call from lifespan |
| `app/gnss/uart2_config.py` | ✨ NEW — one-time UART2 setup via USB |
| `app/gnss/autoflow.py` | ✨ Added Phase 1b cleanup (RTCM disable + TMODE stop) + abort cleanup |
| `setup_uart2.py` | ✨ NEW — user-facing setup script (run once, then forget) |

---

## Service Management

```bash
# Start/stop/restart
sudo systemctl start gnss-backend.service
sudo systemctl stop gnss-backend.service
sudo systemctl restart gnss-backend.service

# View logs
journalctl -u gnss-backend.service -n 100 --no-pager
journalctl -u gnss-backend.service -f  # follow

# Enable auto-start on boot
sudo systemctl enable gnss-backend.service
```

---

## Summary

✅ **Complete & Verified:**
- UART2 one-time configuration via USB (setup_uart2.py) — includes message rates
- Application uses only ttyAMA0 (no USB dependency after setup)
- Full autoflow: SURVEY (60s) → RTCM enable → NTRIP connect → STREAMING
- RTCM messages verified: 1005, 1077, 1087, 1097, 1127, 1230 all flowing
- Position data: 13.072°N, 80.262°E, 31 sats, 3D fix, 0.663m accuracy
- Survey status: observation time, mean accuracy, active/valid flags all working
- NTRIP streaming to caster.emlid.com (MP23960 mountpoint)
- Clean startup/stop without stale state
- All configuration persisted in JSON (autoflow_config.json)

---

**Generated:** 2026-03-30 13:15 UTC
