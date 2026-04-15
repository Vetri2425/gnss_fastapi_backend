# OLED Display Setup - DYX_BASE

**Date:** 2026-03-30  
**Status:** ✅ Files integrated into gnss_fastapi_backend

---

## 📁 File Structure

```
gnss_fastapi_backend/
├── app/
│   ├── oled/
│   │   ├── __init__.py                  # Module init
│   │   ├── oled_animation.py            # Main display driver (14 KB)
│   │   └── oled_test.py                 # Simple test script (1.4 KB)
│   └── [other modules...]
├── systemd_services/
│   ├── gnss-backend.service             # FastAPI backend service
│   └── oled/
│       └── oled_animation.service       # OLED display service
└── [other files...]
```

---

## 🖥️ Hardware Requirements

- **Display:** SSD1306 OLED (128×64 pixels)
- **Interface:** SPI (hardware SPI0 on Raspberry Pi)
- **GPIO Pins:**
  - DC (Data/Command): GPIO 24 (Pin 18)
  - RST (Reset): GPIO 25 (Pin 22)
  - Standard SPI pins: MOSI, MISO, CLK

---

## 📦 Dependencies

Install required Python packages:

```bash
pip install pillow luma.core luma.oled
```

Or add to requirements.txt:

```
Pillow>=9.0.0
luma.core>=1.17.0
luma.oled>=3.4.0
```

Optional for 4G signal detection:

```bash
sudo apt-get install modemmanager
```

---

## 🚀 Running the OLED Display

### Option 1: Standalone (Manual Testing)

```bash
python3 app/oled/oled_test.py      # Simple Hello World test
python3 app/oled/oled_animation.py # Full display loop
```

### Option 2: Systemd Service (Production)

Copy service file to systemd:

```bash
sudo cp systemd_services/oled/oled_animation.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Start the service:

```bash
sudo systemctl start oled_animation
sudo systemctl status oled_animation
```

Enable auto-start on boot:

```bash
sudo systemctl enable oled_animation
```

View logs:

```bash
journalctl -u oled_animation -f
```

---

## 📺 Display Screens

### Boot Sequence
1. **Splash Animation** — Border reveal + "DYX_BASE ONLINE" text (3s)
2. **Loading Bar** — Animated progress bar 0-100% (3s)

### Status Loop

**During Autoflow (until COMPLETE):**
- Shows: STAGE, accuracy (m), observation count (every 2s)
- Stages: SURVEYING → LOCKING → STREAMING → COMPLETE

**After COMPLETE:**
- **NTRIP Caster** (4s) — Host, mountpoint, bytes sent, rate, uptime
- **GNSS Status** (4s) — Satellite icon, count, accuracy, altitude
- **4G LTE** (10s) — Signal bars, IP status, connection state

---

## 🔧 Configuration

### API Endpoints Used

The OLED display fetches data from the FastAPI backend:

| Endpoint | Purpose |
|----------|---------|
| `/api/v1/status` | Position, fix type, satellites |
| `/api/v1/autoflow/status` | Autoflow stage & errors |
| `/api/v1/survey` | Survey observations & accuracy |
| `/api/v1/ntrip` | NTRIP connection status |

**Note:** Make sure FastAPI backend is running on `localhost:8000`.

### 4G Signal Detection

The script checks 4G status via:
1. `ppp0` interface IP detection (`ip` command)
2. `mmcli` for signal strength (ModemManager)

If neither works, defaults to 4/5 bars when IP is detected.

---

## 🐛 Troubleshooting

### Display Not Lighting Up

```bash
# Check SPI is enabled
raspi-config nonint get_spi

# Enable SPI if needed
sudo raspi-config nonint do_spi 0

# Test GPIO pins
python3 -c "import RPi.GPIO as GPIO; GPIO.setmode(GPIO.BCM); \
  GPIO.setup(24, GPIO.OUT); GPIO.setup(25, GPIO.OUT); \
  GPIO.output(24, GPIO.HIGH); print('GPIO test passed')"
```

### "No module named 'luma'"

```bash
pip install pillow luma.core luma.oled
# Or system-wide:
sudo apt-get install python3-pil python3-luma-core python3-luma-oled
```

### API Connection Errors

Verify FastAPI backend is running:

```bash
curl http://localhost:8000/api/v1/status | jq .position
```

### Font Not Found

The script requires Ubuntu Mono font:

```bash
sudo apt-get install fonts-liberation fonts-liberation-mono
# Or install Ubuntu fonts:
sudo apt-get install fonts-ubuntu
```

---

## 📊 Data Flow

```
┌──────────────────┐
│ FastAPI Backend  │ (localhost:8000/api/v1/*)
└────────┬─────────┘
         │
         │ HTTP polls (2s interval)
         ▼
┌─────────────────────────────┐
│ OLED Animation Background   │
│ Fetcher Thread (daemon)     │
└────────┬────────────────────┘
         │
         │ Thread-safe state dict
         │ (locks protect access)
         ▼
┌──────────────────────┐
│ Display Render Loop  │
│ (25ms frame updates) │
└────────┬─────────────┘
         │
         ▼
┌──────────────────────┐
│ SSD1306 Display      │
│ (SPI bus, 128×64)    │
└──────────────────────┘
```

---

## ✅ Verification

After installing, verify everything works:

```bash
# 1. Check service status
sudo systemctl status oled_animation gnss-backend

# 2. Check API endpoints
curl http://localhost:8000/api/v1/autoflow/status | jq .stage

# 3. Monitor logs
journalctl -u oled_animation -u gnss-backend -f
```

Expected output: OLED shows boot splash, then status loop.

---

## Notes

- Service runs as `root` to access GPIO pins
- Display updates every 250ms (0.25s frame time)
- Background API fetcher runs every 2s (non-blocking)
- Thread-safe state management prevents display freezing
- Graceful shutdown via Ctrl+C clears display

---

**Last Updated:** 2026-03-30 18:15 UTC
