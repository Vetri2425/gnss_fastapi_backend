Full Session Summary
What We Built
Rebuilt the GNSS FastAPI backend from scratch (replacing a deleted Node.js/gpsd app) with direct pyubx2 serial communication, Socket.IO WebSocket, and AutoFlow orchestration.

Phase 1 — Missing Endpoints (implemented)
Endpoint	Description
GET /api/v1/autoflow/config	Read saved config (password masked)
POST /api/v1/autoflow/enable	Enable autoflow + trigger run
POST /api/v1/autoflow/disable	Disable autoflow + abort run
POST /api/v1/receiver/reset	UBX-CFG-RST hotstart
WS serial_connected/disconnected	Fired from reader thread via run_coroutine_threadsafe
WS ntrip_status	Fired on NTRIP connect/disconnect
Phase 2 — Robustness (implemented by Qwen)
ntrip_push.py — exponential backoff, max retries, gave_up flag, data_rate_bps, stale property
autoflow.py — survey stuck detection pending
Critical Bugs Fixed
Bug	Root Cause	Fix
systemctl restart hangs forever	Custom signal.signal(SIGTERM) override swallowed uvicorn shutdown	Removed signal handlers — uvicorn manages them
AttributeError: sio.close()	AsyncServer has no .close() method	Changed to await sio.disconnect()
messages_read: 0 always	Receiver UART2 had CFG_UART2OUTPROT_UBX=0 from factory	Sent CFG-VALSET via USB: enabled UBX + NAV-PVT + NAV-SVIN on UART2
parse_errors on every message	msg.msg_id returns bytes in pyubx2 — .startswith("INF-") crashes	Use msg.identity (always str); add protfilter=UBX_PROTOCOL|RTCM3_PROTOCOL
lat: 1.3e-06 instead of 13.07°	pyubx2 pre-scales lat/lon/headMot/pDOP — parser divided again	Removed /1e7, /1e5, /100 for pre-scaled fields
/command unknown_type → 500	HTTPException(400) caught by except Exception and re-wrapped as 500	Added except HTTPException: raise before generic catch
CORS blocking frontend	Default only allowed localhost:3000	Changed default to "*" with proper handling for Socket.IO (string) vs FastAPI (list)
Hardware Discovery
USB /dev/ttyACM0 → u-blox receiver (CFG commands)
UART /dev/ttyAMA0 @ 38400 baud → receiver's UART2 → Pi UART1
Pi also has AP on wlan0 → 192.168.4.1 (field mode)
Both 192.168.4.1:8000 and 192.168.1.42:8000 respond
pyubx2 1.2.x Rules (verified from source)

# CORRECT
msg.identity          # always str "NAV-PVT" — use for all dispatch
msg.lat               # already degrees (pre-scaled 1e-7)
msg.lon               # already degrees
msg.headMot           # already degrees (pre-scaled 1e-5)
msg.pDOP              # already scaled (0.01 applied)
msg.hMSL / 1000       # mm → meters (no pre-scale)
msg.hAcc / 1000       # mm → meters
msg.velN / 1000       # mm/s → m/s

UBXReader(serial, msgmode=GET, protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL)

# WRONG
msg.msg_id            # returns bytes b'\x07', NOT string
Test Results

27 PASS  0 FAIL  0 WARN  12 SKIP (hardware)
avg latency: 10ms
Current Status
Service: active (running) on port 8000
GNSS: 13.07°N 80.26°E, RTK float, 32 satellites, parse_errors: 0
Frontend connection: needs investigation — DYX_BASE frontend uses plain WebSocket on /ws/status, backend uses Socket.IO on /ws → mismatch to resolve next