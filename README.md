# GNSS FastAPI Backend

A FastAPI-based backend service for managing u-blox GNSS receivers (ZED-F9P, etc.) using the UBX protocol via pyubx2. Provides real-time position streaming, automated base-station workflow orchestration, NTRIP caster push, RTCM configuration, fixed base station setup, and OLED display support.

## Features

- **Real-time GNSS Data**: Stream NAV-PVT (position, velocity, time) data via WebSocket (Socket.IO and plain WS)
- **AutoFlow Orchestrator**: Automated state-machine that drives the full base-station lifecycle (IDLE → SURVEY → RTCM → NTRIP → STREAMING)
- **Survey-in Control**: Start/stop survey-in mode for base station setup
- **Fixed Base Station**: Configure the receiver with known LLH coordinates for immediate base operation
- **NTRIP Push Client**: Persistent TCP socket that pushes RTCM3 bytes to an NTRIP caster (v1 and v2)
- **RTCM Configuration**: Enable/disable RTCM3 MSM4/MSM7 message output on UART1, UART2, and USB
- **Base Station Mode**: Configure survey or fixed base station modes
- **Status Monitoring**: Monitor receiver status, errors, base reference, and statistics
- **Threaded Reader**: Non-blocking UBX message reading with pyubx2 (ubxpoller pattern)
- **OLED Display**: SSD1306 OLED display with boot animation, autoflow stage monitoring, and GNSS status
- **Socket.IO Integration**: Async WebSocket support with room-based subscriptions
- **Plain WebSocket**: `/ws/status` endpoint for frontend compatibility without socket.io-client
- **REST API**: Full HTTP API for command and control
- **UART2 Setup Tooling**: One-time scripts to configure UART2 and persist all settings to FLASH

## Project Structure

```
gnss_fastapi_backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + Socket.IO mount + plain WS /ws/status
│   ├── config.py            # Configuration (env vars, autoflow config loader)
│   ├── gnss/
│   │   ├── __init__.py
│   │   ├── reader.py        # Threaded UBX reader (ubxpoller pattern)
│   │   ├── commands.py      # CFG-VALSET commands (survey, RTCM, fixed, LLH, etc.)
│   │   ├── parser.py        # NAV-SVIN, NAV-PVT, NAV-SAT, ACK parsers
│   │   ├── state.py         # Thread-safe GNSS state management (dataclasses)
│   │   ├── autoflow.py      # AutoFlow orchestrator state-machine
│   │   ├── ntrip_push.py    # NTRIP push client (caster TCP socket)
│   │   ├── geodesy.py       # WGS84 ECEF ↔ LLH conversion utilities
│   │   └── uart2_config.py  # UART2 port configuration module
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py        # HTTP REST endpoints
│   │   └── schemas.py       # Pydantic models for requests/responses
│   ├── websocket/
│   │   ├── __init__.py
│   │   └── handlers.py      # Socket.IO event handlers + broadcast
│   ├── oled/
│   │   ├── __init__.py
│   │   ├── oled_animation.py        # Boot + stage monitoring animation
│   │   ├── oled_animation_rpicfg.py # Raspberry Pi config variant
│   │   ├── oled_test.py             # OLED test patterns
│   │   └── oled_test_mock.py        # Mock test (no hardware)
│   └── utils/
│       ├── __init__.py
│       └── serial_utils.py  # Serial port detection, test, auto-detect GNSS
├── data/
│   ├── autoflow_config.json         # Persisted AutoFlow configuration
│   └── autoflow_config.backup.json  # Backup config
├── systemd_services/
│   └── gnss-backend.service
├── requirements.txt
├── gnss-backend-fastapi.service     # systemd service file (production)
├── gnss-backend-fixed.service       # systemd service file (alternative)
├── setup_uart2.py                   # One-time UART2 setup script
├── save_all_config.py               # Comprehensive receiver config save script
├── manual_autoflow_acm0.py          # Manual AutoFlow runner (USB path)
├── fixed_base.md                    # Fixed base station API documentation
├── GNSS_SETUP_COMPLETE.md           # Full setup summary
├── OLED_SETUP.md / OLED_DEPLOYMENT_GUIDE.md / OLED_VERIFICATION.md
├── README.md
└── test_api.py / test_endpoints.py
```

## Requirements

- Python 3.10+
- u-blox GNSS receiver (ZED-F9P recommended, NEO-M8P compatible)
- Serial connection (USB `/dev/ttyACM0`, UART `/dev/ttyAMA0`, or SPI)
- Raspberry Pi 5 (for UART2 GPIO and OLED display support)

## Installation

### 1. Clone or copy the project

```bash
cd gnss_fastapi_backend
```

### 2. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate  # Linux
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env  # If available, otherwise defaults are used
# Edit .env with your settings
nano .env
```

Key configuration options:

| Variable | Default | Description |
|----------|---------|-------------|
| `SERIAL_PORT` | `/dev/ttyAMA0` | Serial port path |
| `SERIAL_BAUDRATE` | `38400` | Serial baud rate |
| `FASTAPI_PORT` | `8000` | HTTP API port |
| `FASTAPI_HOST` | `0.0.0.0` | HTTP bind address |
| `DEBUG` | `False` | Enable debug/reload mode |
| `SURVEY_ACCURACY_THRESHOLD` | `0.1` | Survey-in accuracy limit (meters) |
| `SURVEY_MIN_DURATION` | `10` | Survey-in minimum duration (seconds) |
| `RTCM_MSM_TYPE` | `MSM4` | RTCM MSM type (MSM4 or MSM7) |
| `NTRIP_ENABLED` | `False` | Enable NTRIP push on startup |
| `NTRIP_HOST` | `""` | NTRIP caster hostname |
| `NTRIP_PORT` | `2101` | NTRIP caster port |
| `NTRIP_MOUNTPOINT` | `""` | NTRIP mountpoint |
| `NTRIP_USERNAME` | `""` | NTRIP username |
| `NTRIP_PASSWORD` | `""` | NTRIP password |
| `LOG_LEVEL` | `INFO` | Logging level |
| `WS_CORS_ORIGINS` | `*` | WebSocket CORS allowed origins |

### 5. (One-time) Configure receiver UART2

If connecting via UART GPIO (`/dev/ttyAMA0`), run once with USB connected:

```bash
python3 setup_uart2.py
```

This configures UART2 on the ZED-F9P for UBX I/O at 38400 baud and saves settings to FLASH (survives power cycles). After this, the USB cable is no longer needed.

For a comprehensive configuration save (all ports, survey defaults, RTCM messages, constellations, etc.):

```bash
python3 save_all_config.py
```

## Usage

### Start the server

```bash
# Using the app's main entry point
python -m app.main

# Or using uvicorn directly
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Or with auto-reload for development
uvicorn app.main:app --reload
```

### API Documentation

Once running, access the interactive API documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

## REST API Endpoints

### Root & Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Root endpoint (API info) |
| GET | `/health` | Health check |
| GET | `/info` | Application + runtime info |

### Status Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/status` | Get full system status |
| GET | `/api/v1/status/position` | Get current position |
| GET | `/api/v1/status/survey` | Get survey-in status |
| GET | `/api/v1/status/base-reference` | Get surveyed/fixed base reference details |
| GET | `/api/v1/status/rtcm` | Get RTCM status |
| GET | `/api/v1/status/ntrip` | Get NTRIP status |
| GET | `/api/v1/status/receiver` | Get receiver status |
| GET | `/api/v1/reader/status` | Get reader thread status |
| GET | `/api/v1/autoflow/status` | Get AutoFlow orchestrator status |
| GET | `/api/v1/autoflow/config` | Get saved AutoFlow config |

### Command Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/command` | Execute any GNSS command |
| POST | `/api/v1/survey/start` | Start survey-in mode |
| POST | `/api/v1/survey/stop` | Stop survey-in mode |
| POST | `/api/v1/rtcm/configure` | Configure RTCM output |
| POST | `/api/v1/mode/base` | Configure base station mode |
| POST | `/api/v1/base/fixed` | Configure fixed base station with LLH coordinates |
| POST | `/api/v1/reader/reconnect` | Force reader reconnect |
| POST | `/api/v1/receiver/reset` | Reset GNSS receiver (UBX-CFG-RST) |

### AutoFlow Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/autoflow/config` | Save AutoFlow configuration |
| POST | `/api/v1/autoflow/start` | Manually trigger AutoFlow run |
| POST | `/api/v1/autoflow/stop` | Abort current AutoFlow run |
| POST | `/api/v1/autoflow/enable` | Enable AutoFlow and trigger run |
| POST | `/api/v1/autoflow/disable` | Disable AutoFlow and abort run |

### NTRIP Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/ntrip/start` | Start NTRIP streaming to caster |
| POST | `/api/v1/ntrip/stop` | Stop NTRIP streaming |

### Convenience Aliases

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/survey` | Alias for `/status/survey` |
| GET | `/api/v1/rtcm` | Alias for `/status/rtcm` |
| GET | `/api/v1/ntrip` | Alias for `/status/ntrip` |

### Example: Start Survey-in

```bash
curl -X POST http://localhost:8000/api/v1/survey/start \
  -H "Content-Type: application/json" \
  -d '{"min_duration": 300, "accuracy_limit": 0.10}'
```

### Example: Configure Fixed Base Station

```bash
curl -X POST http://localhost:8000/api/v1/base/fixed \
  -H "Content-Type: application/json" \
  -d '{
    "latitude": 13.0720445,
    "longitude": 80.2619310,
    "height": 2.87,
    "fixed_pos_acc": 0.10,
    "msm_type": "MSM4",
    "enable_rtcm": true
  }'
```

### Example: Enable RTCM Output

```bash
curl -X POST http://localhost:8000/api/v1/rtcm/configure \
  -H "Content-Type: application/json" \
  -d '{"msm_type": "MSM4", "enable": true}'
```

### Example: Save AutoFlow Config

```bash
curl -X POST http://localhost:8000/api/v1/autoflow/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "min_duration_sec": 120,
    "accuracy_limit_m": 0.10,
    "msm_type": "MSM4",
    "ntrip_host": "ntrip.example.com",
    "ntrip_port": 2101,
    "ntrip_mountpoint": "MY_MOUNTPOINT",
    "ntrip_password": "secret"
  }'
```

## WebSocket API

### Socket.IO (Primary)

Connect to `ws://localhost:8000/ws` for real-time data streaming with room-based subscriptions.

#### Python Client Example

```python
import socketio

sio = socketio.Client()

@sio.event
def connect():
    print("Connected to GNSS backend")
    sio.emit("subscribe", {"stream": "position", "room": "default"})

@sio.on("initial_state")
def on_initial_state(state):
    print(f"Initial state: {state}")

@sio.on("gnss_data")
def on_gnss_data(data):
    print(f"GNSS data: {data}")

@sio.on("autoflow_state")
def on_autoflow_state(data):
    print(f"AutoFlow: {data}")

sio.connect("ws://localhost:8000/ws")
sio.wait()
```

#### JavaScript Client Example

```javascript
const io = require("socket.io-client");
const socket = io("ws://localhost:8000/ws");

socket.on("connect", () => {
  console.log("Connected");
  socket.emit("subscribe", { stream: "position", room: "default" });
});

socket.on("initial_state", (state) => console.log("Initial state:", state));
socket.on("gnss_data", (data) => console.log("GNSS data:", data));
socket.on("autoflow_state", (data) => console.log("AutoFlow:", data));
```

#### Socket.IO Events

**Client → Server:**

| Event | Data | Description |
|-------|------|-------------|
| `subscribe` | `{stream, room}` | Subscribe to data stream |
| `unsubscribe` | `{stream, room}` | Unsubscribe from stream |
| `join_room` | `room` | Join a room |
| `leave_room` | `room` | Leave a room |
| `command` | `{type, params}` | Execute command |
| `get_status` | - | Request full status |

**Server → Client:**

| Event | Data | Description |
|-------|------|-------------|
| `initial_state` | `state` | Initial state on connect |
| `gnss_data` | `data` | GNSS data update |
| `status` | `status` | Full status update |
| `autoflow_state` | `state` | AutoFlow orchestrator state |
| `serial_connected` | `{port, baudrate}` | Serial port connected |
| `serial_disconnected` | `{reason}` | Serial port disconnected |
| `command_response` | `{success, message}` | Command response |
| `subscribed` | `{stream, room}` | Subscription confirmed |

#### Data Streams

| Stream | Description |
|--------|-------------|
| `all` | All data types |
| `position` | NAV-PVT position data |
| `survey` | NAV-SVIN survey data |
| `rtcm` | RTCM status |

### Plain WebSocket (Frontend Compatibility)

Connect to `ws://localhost:8000/ws/status` for a simple text-based status feed. Sends a `status_update` JSON message every 1.5 seconds. No socket.io-client required.

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/status");

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log("Status:", data);
};
```

## AutoFlow Orchestrator

The AutoFlow orchestrator is a state-machine that automates the complete base-station workflow:

```
IDLE
  └─► WAITING_SERIAL  (waiting for serial connection)
        └─► SURVEY        (survey-in running, polls NAV-SVIN every 5s)
              └─► ENABLING_RTCM   (sends CFG-VALSET RTCM enable)
                    └─► NTRIP_CONNECT  (if NTRIP host configured)
                          └─► STREAMING  (pushing RTCM to caster)
                    └─► STREAMING  (if no NTRIP — RTCM active on serial)
FAILED  (any unrecoverable error)
```

When `enabled: true` in the AutoFlow config, the orchestrator automatically starts this flow on server boot. It can also be triggered manually via the API.

State is broadcast to WebSocket clients as `autoflow_state` events and is queryable via `/api/v1/autoflow/status`.

## CFG-VALSET Commands

The backend uses UBX CFG-VALSET for all configuration changes:

### Survey-in Start

```python
from app.gnss.commands import GNSSCommands

cmd = GNSSCommands.create_survey_start_command(
    min_duration=300,      # 5 minutes
    accuracy_limit=0.10,   # 10 cm
)
```

### Survey-in Stop

```python
cmd = GNSSCommands.create_survey_stop_command()
```

### Fixed Mode (ECEF)

```python
cmd = GNSSCommands.create_fixed_mode_command(
    ecef_x=4500000.0,
    ecef_y=1200000.0,
    ecef_z=4300000.0,
)
```

### Fixed Mode (LLH)

```python
cmd = GNSSCommands.create_fixed_llh_command(
    latitude=13.0720445,
    longitude=80.2619310,
    height=2.87,
    fixed_pos_acc=0.10,
)
```

### RTCM Enable (MSM4)

```python
cmd = GNSSCommands.create_rtcm_enable_command(msm_type="MSM4")
# Enables: 1074, 1084, 1094, 1124, 1005 (ARP), 1230 (GLONASS CPB)
# on UART1, UART2, and USB ports
```

### RTCM Enable (MSM7)

```python
cmd = GNSSCommands.create_rtcm_enable_command(msm_type="MSM7")
# Enables: 1077, 1087, 1097, 1127, 1005 (ARP), 1230 (GLONASS CPB)
```

### Base Station Mode

```python
cmd = GNSSCommands.create_base_mode_command(
    msm_type="MSM4",
    survey_mode=True,
    min_duration=300,
    accuracy_limit=0.10,
)
```

## OLED Display

The project includes support for SSD1306 OLED displays connected via I2C on Raspberry Pi. The display shows:

- Boot splash and loading animation
- AutoFlow stage monitoring (SURVEY/LOCK/STREAM)
- GNSS satellite status
- 4G LTE signal strength (if available)
- NTRIP caster connection status

See `OLED_SETUP.md` for hardware wiring and installation instructions.

## Systemd Service

For production deployment on Linux:

### 1. Copy service file

```bash
sudo cp gnss-backend-fastapi.service /etc/systemd/system/
```

### 2. Edit service file

```bash
sudo nano /etc/systemd/system/gnss-backend-fastapi.service
```

Update paths, user, and group as needed.

### 3. Enable and start service

```bash
sudo systemctl daemon-reload
sudo systemctl enable gnss-backend-fastapi
sudo systemctl start gnss-backend-fastapi
sudo systemctl status gnss-backend-fastapi
```

### 4. View logs

```bash
sudo journalctl -u gnss-backend-fastapi -f
```

## Architecture

### Threaded Reader Pattern

The GNSS reader uses a threaded pattern based on pyubx2's ubxpoller example:

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Serial Port    │────▶│  UBXReader       │────▶│  Reader Thread  │
│  (ttyAMA0)      │     │  (pyubx2)        │     │  (background)   │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                           ┌──────────────────────────────┼──────────────┐
                           │                              │              │
                           ▼                              ▼              ▼
                    ┌──────────────┐              ┌──────────────┐  ┌──────────────┐
                    │ Inbound Queue│              │  GNSS State  │  │Outbound Queue│
                    │  (messages)  │              │  (thread-safe)│  │  (commands)  │
                    └──────────────┘              └──────────────┘  └──────────────┘
                           │                              │              │
                           ▼                              ▼              ▼
                    ┌──────────────┐              ┌──────────────┐  ┌──────────────┐
                    │  WebSocket   │              │  REST API    │  │  Serial Port │
                    │  Broadcast   │              │  Endpoints   │  │  (commands)  │
                    └──────────────┘              └──────────────┘  └──────────────┘
                                                          │
                                                          ▼
                                                   ┌──────────────┐
                                                   │ AutoFlow     │
                                                   │ Orchestrator │
                                                   │ (state-machine)│
                                                   └──────┬───────┘
                                                          │
                                                          ▼
                                                   ┌──────────────┐
                                                   │ NTRIP Push   │
                                                   │ Client       │
                                                   └──────────────┘
```

### State Management

All GNSS state is stored in thread-safe dataclasses:

- **PositionFix**: Lat/lon/alt, accuracy, fix type, satellites, DOP, velocity
- **SurveyStatus**: Survey-in active/valid, progress, accuracy, ECEF coordinates
- **BaseReference**: Last applied base reference (survey-derived or fixed LLH)
- **RTCMStatus**: Enabled, MSM type, per-message-type counts, data rate
- **NTRIPStatus**: Connection state, bytes transferred, uptime
- **ReceiverStatus**: Connection, firmware version, errors, ACK/NAK counts

### RTCM3 Message Handling

When the reader detects an RTCM3 frame (sync byte `0xD3`), it:
1. Parses the 12-bit message type from the frame header
2. Increments the per-message-type counter in state
3. Forwards raw bytes to the NTRIP push client (if active)
4. Skips further UBX parsing for that frame

This allows RTCM3 frames received from the receiver to be pushed directly to the NTRIP caster in real time.

## Error Handling

- **Serial Reconnect**: Automatic reconnection with configurable delay and max attempts
- **ACK-NAK Detection**: Tracks command acknowledgments with timeout
- **Permission Errors**: Handles serial port permission issues gracefully
- **Graceful Shutdown**: Clean shutdown on SIGINT/SIGTERM via uvicorn lifecycle
- **Empty NAV-SVIN Preservation**: Prevents loss of survey results when receivers send empty snapshots after survey-in completes

## Logging

Configure logging level in `.env`:

```bash
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

Logs include:
- Serial connection/disconnection events
- Command execution and ACK/NAK results
- Message parsing and RTCM3 frame detection
- AutoFlow state transitions
- NTRIP push connection status
- Error conditions

## Troubleshooting

### Serial Port Not Found

```bash
# List available serial ports
python -c "from app.utils.serial_utils import detect_serial_ports; print(detect_serial_ports())"

# Check permissions
ls -la /dev/ttyAMA0
sudo usermod -a -G dialout $USER  # Add user to dialout group
```

### No GNSS Data

1. Verify receiver is connected: `ls /dev/ttyACM* /dev/ttyAMA*`
2. Check baud rate matches receiver settings (default: 38400)
3. Ensure UART2 was configured: run `python3 setup_uart2.py`
4. Check logs for parse errors

### Survey-in Not Starting

1. Verify TMODE is not already active
2. Check receiver supports survey-in (ZED-F9P recommended)
3. Ensure clear sky view for good signal
4. Monitor NAV-SVIN responses via `/api/v1/status/survey`

### NTRIP Push Not Working

1. Verify NTRIP caster credentials in AutoFlow config
2. Check network connectivity to caster host
3. Monitor NTRIP status via `/api/v1/autoflow/status` (includes live push client state)
4. Ensure RTCM output is enabled before NTRIP start

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

For issues and feature requests, please open an issue on the repository.
