# OLED Display Deployment & Troubleshooting Guide

**Date:** 2026-03-30
**Status:** ✅ **Files ready - RPi5 GPIO compatibility fix required**

---

## 📋 Current Issue

OLED initialization fails with:
```
RuntimeError: Cannot determine SOC peripheral base address
```

**Root Cause:** RPi.GPIO doesn't support Raspberry Pi 5 GPIO without additional setup.

**Solution:** Install `raspi-gpio` system tool or use pigpio library.

---

## 📁 Files Ready for Deployment

```
/home/dyx/gnss_fastapi_backend/app/oled/
├── oled_animation.py              ← Main driver (RPi.GPIO)
├── oled_animation_rpicfg.py       ← RPi5 optimized version
├── oled_test.py                   ← Simple test
└── oled_test_mock.py              ← Mock test (no hardware)

/home/dyx/gnss_fastapi_backend/systemd_services/oled/
└── oled_animation.service         ← Systemd service file
```

---

## 🔧 Quick Fix (Choose One)

### Option A: Install raspi-gpio (Easiest)

```bash
sudo apt-get update
sudo apt-get install -y raspi-gpio
```

This tool provides direct GPIO control compatible with RPi 5.

### Option B: Install pigpio

```bash
pip install pigpio
sudo apt-get install python3-pigpio pigpiod
sudo systemctl start pigpiod
```

### Option C: Manual GPIO Export (Complex)

```bash
echo 24 | sudo tee /sys/class/gpio/export
echo 25 | sudo tee /sys/class/gpio/export
echo out | sudo tee /sys/class/gpio/gpio24/direction
echo out | sudo tee /sys/class/gpio/gpio25/direction
```

---

## 🚀 Testing OLED (After Fix)

### Test 1: Simple Display Test

```bash
sudo python3 app/oled/oled_test.py
```

**Expected:** "Hello World! Rover OS Ready" on display

### Test 2: Full Animation Test

```bash
sudo python3 app/oled/oled_animation.py
```

**Expected:**
1. Boot splash animation (border, "DYX_BASE ONLINE")
2. Loading bar (0-100%)
3. Status loop showing real-time GNSS/NTRIP data

### Test 3: With API Running

Make sure FastAPI backend is running:

```bash
systemctl status gnss-backend
# or start it:
systemctl start gnss-backend
```

---

## 📋 Hardware Checklist

| Item | Pin | Status |
|------|-----|--------|
| **DC (Data/Command)** | GPIO 24 (Pin 18) | ? |
| **RST (Reset)** | GPIO 25 (Pin 22) | ? |
| **MOSI** | GPIO 10 (Pin 19) | ? |
| **SCLK** | GPIO 11 (Pin 23) | ? |
| **CE0** | GPIO 8 (Pin 24) | ? |
| **GND** | Any GND pin | ? |
| **3.3V** | Pin 1 or 17 | ? |

---

## ✅ Verification Commands

```bash
# Check SPI is enabled
raspi-config nonint get_spi

# Check SPI device exists
ls -l /dev/spidev0.0

# Check FastAPI is running
curl http://localhost:8000/api/v1/autoflow/status | jq .state

# Check GPIO access
raspi-gpio get 24 25

# Test OLED directly
python3 app/oled/oled_test.py
```

---

## 📊 Expected Display Flow

During normal operation (autoflow state = "STREAMING"):

```
TIME: 0-2s    → AUTOFLOW STAGE screen
              → Shows: STATE, accuracy, observation count

TIME: 2-6s    → NTRIP CASTER screen
              → Shows: host, mountpoint, bytes sent, data rate

TIME: 6-10s   → GNSS screen
              → Shows: satellites, accuracy, altitude, fix type

TIME: 10-20s  → 4G LTE screen (once per 10s cycle)
              → Shows: signal bars, IP, status

TIME: 20s+    → Loop repeats
```

---

## 🆘 Troubleshooting

### "Cannot determine SOC peripheral base address"

**Fix:** Install raspi-gpio or pigpio (see Quick Fix above)

### "No module named 'luma'"

**Fix:**
```bash
source venv/bin/activate
pip install pillow luma.core luma.oled
```

### Display shows nothing (black screen)

**Check:**
1. 3.3V and GND pins connected?
2. DC and RST pins connected to GPIO 24 and 25?
3. SPI pins (MOSI/SCLK/CE0) connected?
4. Try `sudo python3 oled_test.py`

### Display flickers or shows garbage

**Fix:**
1. Check wiring (loose connections?)
2. Try slower SPI speed (modify spi() call in script)
3. Verify display is SSD1306 model

### API connection timeout

**Check:**
1. Is FastAPI running? `curl http://localhost:8000/api/v1/status`
2. Is autoflow enabled? `curl http://localhost:8000/api/v1/autoflow/status | jq .enabled`
3. Check service: `systemctl status gnss-backend`

---

## 📦 Installing as Systemd Service

Once OLED works manually:

```bash
# Copy service file
sudo cp systemd_services/oled/oled_animation.service /etc/systemd/system/

# Update service file to use correct path
sudo sed -i 's|/home/dyx/Documents|/home/dyx/gnss_fastapi_backend/app/oled|g' \
  /etc/systemd/system/oled_animation.service

# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable oled_animation

# Start service
sudo systemctl start oled_animation

# Check status
sudo systemctl status oled_animation

# View logs
journalctl -u oled_animation -f
```

---

## 🔗 Data API Endpoints

OLED polls these every 2 seconds:

```
GET /api/v1/status
    ├─ position.fix_type_str      → "3d_fix", "no_fix", etc.
    ├─ position.num_satellites    → 0-32
    ├─ position.accuracy          → meters
    ├─ position.altitude          → meters
    ├─ survey.observation_time    → seconds
    ├─ survey.mean_accuracy       → meters
    ├─ ntrip.connected            → true/false
    ├─ ntrip.host                 → "caster.emlid.com"
    ├─ ntrip.data_rate_bps        → bits per second
    └─ ntrip.bytes_sent           → total bytes

GET /api/v1/autoflow/status
    ├─ state                      → "SURVEY","LOCKING","STREAMING","ERROR","IDLE"
    ├─ enabled                    → true/false
    ├─ last_error                 → error message or null
    └─ config                     → configuration object
```

---

## 📝 Service File Config

**Location:** `/home/dyx/gnss_fastapi_backend/systemd_services/oled/oled_animation.service`

```ini
[Unit]
Description=OLED Display - DYX_BASE
After=network.target gnss-backend.service
Wants=gnss-backend.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/dyx/gnss_fastapi_backend
ExecStart=/home/dyx/gnss_fastapi_backend/venv/bin/python3 \
  /home/dyx/gnss_fastapi_backend/app/oled/oled_animation.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=oled-display

[Install]
WantedBy=multi-user.target
```

---

## ✅ Checklist Before Deployment

- [ ] Hardware checked (all 7 pins connected)
- [ ] raspi-gpio OR pigpio installed
- [ ] SPI enabled in raspi-config
- [ ] FastAPI backend running (`systemctl status gnss-backend`)
- [ ] `python3 app/oled/oled_test.py` works
- [ ] `sudo python3 app/oled/oled_animation.py` shows boot animation
- [ ] Display shows real GNSS/NTRIP data after 2 seconds

---

**Last Updated:** 2026-03-30 18:35 UTC
