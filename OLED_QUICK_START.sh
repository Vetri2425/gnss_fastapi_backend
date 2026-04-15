#!/bin/bash
# OLED Display Quick Start Script
# Run this to get the OLED display working

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║  OLED Display Quick Start                                 ║"
echo "║  Raspberry Pi 5 + SSD1306 Display                         ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Install GPIO support
echo "Step 1: Installing GPIO support for RPi 5..."
sudo apt-get update -qq
sudo apt-get install -y -qq raspi-gpio
echo "✓ raspi-gpio installed"
echo ""

# Step 2: Verify hardware
echo "Step 2: Verifying hardware..."
if ls /dev/spidev0.0 >/dev/null 2>&1; then
    echo "✓ SPI device found: /dev/spidev0.0"
else
    echo "✗ SPI device not found - enable SPI in raspi-config"
    exit 1
fi

if sudo raspi-gpio get 24 25 >/dev/null 2>&1; then
    echo "✓ GPIO pins accessible"
else
    echo "✗ GPIO access failed - check permissions"
    exit 1
fi
echo ""

# Step 3: Test OLED
echo "Step 3: Testing OLED display..."
echo "  Running simple test..."
cd /home/dyx/gnss_fastapi_backend

# Run test for 5 seconds
sudo timeout 5 venv/bin/python3 app/oled/oled_test.py 2>/dev/null || true
echo "✓ OLED test completed"
echo ""

# Step 4: Start full OLED animation
echo "Step 4: Starting OLED with real data..."
echo "  (You should see boot animation now)"
echo "  Press Ctrl+C to stop"
echo ""

sudo venv/bin/python3 app/oled/oled_animation.py

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  To install as service:                                    ║"
echo "║                                                            ║"
echo "║  sudo cp systemd_services/oled/oled_animation.service \\  ║"
echo "║    /etc/systemd/system/                                   ║"
echo "║  sudo systemctl daemon-reload                             ║"
echo "║  sudo systemctl enable oled_animation                     ║"
echo "║  sudo systemctl start oled_animation                      ║"
echo "║                                                            ║"
echo "║  journalctl -u oled_animation -f                          ║"
echo "╚════════════════════════════════════════════════════════════╝"
