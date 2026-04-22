# GNSS FastAPI Backend - OLED & Network Implementation Log
**Date:** 2026-04-17  
**Session:** OLED Display Updates + Internet Sharing Configuration

---

## Issues Faced & Solutions

### Issue 1: GNSS Screen Cannot Show Survey vs Fixed Base Mode
**Problem:**
- OLED GNSS screen only displayed live position data (satellites, accuracy, altitude)
- No way to distinguish if receiver was in SURVEY mode or FIXED base mode
- Frontend needed to know the current base configuration state in real time

**Solution:**
- Added `/api/v1/status/base-reference` endpoint fetch to OLED animation fetcher
- Updated `draw_gnss()` to extract and display `fixed_reference.mode` field
- Mode now shows as "SURVEY" or "FIXED" on bottom-right of GNSS screen

**Files Modified:**
- `app/oled/oled_animation.py`
  - Line 34: Added `base_reference` to state dict
  - Lines 77-82: Added fetcher for `/api/v1/status/base-reference`
  - Lines 107: Added state update for base_reference
  - Lines 264-267: Extract base mode from fixed_reference
  - Lines 300-304: Display base mode on GNSS screen

**Test Result:**
```
curl http://localhost:8000/api/v1/status/base-reference
{
  "fixed_reference": {
    "mode": "FIXED",  ← Displayed on GNSS screen
    ...
  }
}
```

---

### Issue 2: GNSS Screen Showing Live Position Accuracy Instead of Base Station Accuracy
**Problem:**
- GNSS screen displayed `position.accuracy` (live rover accuracy)
- Should display `saved_position.accuracy` (base station surveyed accuracy)
- Base station accuracy is more important for RTK operations

**Solution:**
- Added `/api/v1/base/saved-position` endpoint fetch to OLED animation
- Extract `position.accuracy` from saved position data
- Changed display from "hAcc {live_accuracy}m" to "Base {base_accuracy}m"
- Removed altitude display (not needed for base reference)

**Files Modified:**
- `app/oled/oled_animation.py`
  - Line 35: Added `saved_position` to state dict
  - Lines 85-91: Added fetcher for `/api/v1/base/saved-position`
  - Line 118: Added state update for saved_position
  - Lines 278-280: Extract base accuracy from saved position
  - Lines 300-304: Display "Base {accuracy}m" instead of live accuracy

**Test Result:**
```
curl http://localhost:8000/api/v1/base/saved-position
{
  "position": {
    "accuracy": 1.2531  ← Displayed as "Base 1.253m"
  }
}
```

---

### Issue 3: 4G Screen Shows "OFFLINE" When Ethernet is Connected
**Problem (Critical):**
- OLED 4G screen only checked `ppp0` (cellular interface)
- When ethernet (eth0) was connected, screen still showed "OFFLINE"
- User could have internet via ethernet but display showed offline status

**Root Cause:**
- Code only detected cellular connections via `ip addr show ppp0`
- No detection for ethernet interfaces (eth0, eth1, eth2, wlan0)
- No prioritization logic for multiple network types

**Solution:**
- Added detection for ethernet interfaces (eth0, eth1, eth2, wlan0)
- Prioritize ethernet over 4G if both available
- Show correct connection type label: "ETHERNET" or "4G LTE"
- Show "LINK" badge for ethernet, signal bars (0-5) for 4G
- Correctly display "ONLINE" status for any active connection

**Files Modified:**
- `app/oled/oled_animation.py`
  - Lines 28-32: Added `eth_ip` and `conn_type` to state dict
  - Lines 47-76: 
    - Separated ppp0 and ethernet detection
    - Added `grep -E 'inet.*(eth0|eth1|eth2|wlan0)'` for ethernet detection
    - Added connection type prioritization logic
  - Lines 108-110: Updated state for eth_ip and conn_type
  - Lines 132-137: Signal bar logic - only show for 4G (set 5/5 for ethernet)
  - Lines 355-391: Complete redesign of `draw_4g()`:
    - Display "ETHERNET" or "4G LTE" based on `conn_type`
    - Show "LINK" or signal bars
    - Correct "ONLINE/OFFLINE" status
    - Display active IP address

**Test Result:**
```
✅ With Ethernet Connected:
  - Display: "ETHERNET | LINK | IP 192.168.1.42"
  - Status: "ONLINE"

✅ With 4G Connected:
  - Display: "4G LTE | ▓▓▓▓░ | 4/5"
  - Status: "ONLINE"

✅ No Connection:
  - Display: "OFFLINE"
  - Status: "OFFLINE"
```

---

### Issue 4: Hotspot Shows "ONLINE" But No Internet Access
**Problem (Critical):**
- IP forwarding enabled and NAT rules configured
- DHCP clients could connect but had NO internet access
- DNS resolution failed (REFUSED errors)

**Root Causes:**
1. **dnsmasq not configured for upstream DNS** – No `server=` directives
2. **DHCP not advertising DNS server** – Missing `dhcp-option=6`
3. **dnsmasq only listening on wlan0** – Blocked recursion

**Solution:**

**Step 1: Enable IP Forwarding**
```bash
sudo sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
```

**Step 2: Configure NAT**
```bash
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
sudo sh -c 'iptables-save > /etc/iptables/rules.v4'
```

**Step 3: Update dnsmasq Configuration**
```bash
# File: /etc/dnsmasq.d/dyx-ap.conf
interface=wlan0
bind-interfaces
dhcp-range=192.168.4.2,192.168.4.50,255.255.255.0,24h
dhcp-option=3,192.168.4.1        # Gateway
dhcp-option=6,192.168.4.1        # DNS server ← ADDED
server=8.8.8.8                   # Upstream DNS ← ADDED
server=8.8.4.4                   # Upstream DNS ← ADDED
no-resolv                        # Don't read system resolv.conf
```

**Step 4: Restart dnsmasq**
```bash
sudo systemctl restart dnsmasq
```

**Files Modified:**
- `/etc/dnsmasq.d/dyx-ap.conf` – Added DNS forwarding configuration
- `/etc/sysctl.conf` – Enabled IP forwarding permanently
- `/etc/iptables/rules.v4` – Saved NAT rules persistently

**Test Results:**
```
✅ DNS Resolution:
  nslookup google.com 192.168.4.1
  → 142.251.222.174 (resolved)

✅ Gateway Ping:
  ping -c 1 192.168.4.1 → 0.039ms

✅ Internet Ping:
  ping -c 1 8.8.8.8 → 2.27ms

✅ Connected Client:
  DHCP lease: 192.168.4.22
  Gateway: 192.168.4.1
  Internet: ✅ Accessible
```

---

## Final Configuration Summary

### OLED Display Updates
| Screen | Data Source | Changes |
|---|---|---|
| **GNSS** | `/api/v1/status/position` | Added base mode (SURVEY/FIXED) |
| **GNSS** | `/api/v1/base/saved-position` | Changed to show base accuracy instead of live |
| **4G/Network** | `eth0, eth1, eth2, wlan0` | Added ethernet detection, prioritization |
| **4G/Network** | `ppp0` | Kept for 4G/cellular support |

### Network Configuration
| Component | Status |
|---|---|
| Hotspot SSID | DYX_BASE (wlan0) |
| Hotspot IP | 192.168.4.1 |
| DHCP Range | 192.168.4.2-50 |
| DNS Server | 192.168.4.1 |
| Upstream DNS | 8.8.8.8, 8.8.4.4 |
| IP Forwarding | Enabled (1) |
| NAT Rules | MASQUERADE on eth0 |
| Internet Sharing | ✅ ENABLED |

---

## API Endpoints Used

### Status Endpoints
```bash
GET /api/v1/status              # Full system status
GET /api/v1/status/position     # GNSS position data
GET /api/v1/status/base-reference  # Base mode and coordinates
GET /api/v1/base/saved-position # Saved base station position
GET /api/v1/autoflow/status     # AutoFlow state
```

### Response Examples
```json
// Base Reference
{
  "fixed_reference": {
    "mode": "FIXED",
    "source": "autoflow_survey",
    "timestamp": "2026-04-17T06:50:46"
  }
}

// Saved Position
{
  "saved": true,
  "position": {
    "accuracy": 1.2531,
    "surveyed_at": "2026-04-17T06:50:45"
  }
}
```

---

## Service Management

### OLED Service
```bash
sudo systemctl restart oled_animation.service
sudo systemctl status oled_animation.service
```

### Network Services
```bash
sudo systemctl restart dnsmasq
sudo systemctl status hostapd
sudo systemctl status dnsmasq
```

---

## Verification Commands

### Test OLED Fetcher
```python
python3 << 'EOF'
import subprocess, json, urllib.request

# Test base reference fetch
r = urllib.request.urlopen('http://localhost:8000/api/v1/status/base-reference', timeout=2)
base_ref = json.loads(r.read())
mode = base_ref.get('fixed_reference', {}).get('mode', '')
print(f"Base mode: {mode}")

# Test saved position fetch
r = urllib.request.urlopen('http://localhost:8000/api/v1/base/saved-position', timeout=2)
saved_pos = json.loads(r.read())
accuracy = saved_pos.get('position', {}).get('accuracy', 0)
print(f"Base accuracy: {accuracy:.3f}m")
EOF
```

### Test Network Connectivity
```bash
# From DYX_BASE device
ping 8.8.8.8                    # External internet
curl -I http://google.com       # HTTP access
curl http://192.168.4.1:8000/api/v1/health  # Local API

# From client connected to hotspot
curl http://192.168.4.1:8000/api/v1/status  # API access
nslookup google.com 192.168.4.1             # DNS test
ping 8.8.8.8                                 # Internet access
```

---

## Files Changed Summary

### Core Application
- `app/oled/oled_animation.py` – OLED display logic (4 updates)
- `app/api/routes.py` – No changes (API already had endpoints)
- `app/api/schemas.py` – No changes

### System Configuration
- `/etc/dnsmasq.d/dyx-ap.conf` – Added DNS forwarding
- `/etc/sysctl.conf` – Enabled IP forwarding
- `/etc/iptables/rules.v4` – NAT rules persistence

---

## Performance Impact
- **API Calls:** 4 total per OLED update cycle (2s interval)
  - `/api/v1/status` (position, survey, rtcm, ntrip)
  - `/api/v1/autoflow/status` (state)
  - `/api/v1/status/base-reference` (mode)
  - `/api/v1/base/saved-position` (accuracy)
- **Network Overhead:** ~15KB per cycle
- **CPU:** Minimal (threaded background fetcher)
- **Display Update:** Non-blocking (runs every 2s)

---

## Known Limitations & Notes

1. **Ethernet Priority:** Ethernet (eth0/eth1/eth2) is prioritized over 4G if both available
2. **DNS Upstream:** Using Google Public DNS (8.8.8.8) – can be changed in dnsmasq config
3. **DHCP Range:** Clients get 192.168.4.2-50 (max 49 devices)
4. **Signal Bars:** Only shown for 4G/cellular, not for ethernet
5. **Base Accuracy Display:** Uses saved position accuracy, not survey mean accuracy

---

## Testing Checklist

- [x] GNSS screen shows base mode (SURVEY/FIXED)
- [x] GNSS screen shows saved base accuracy
- [x] 4G screen detects ethernet connection
- [x] 4G screen shows ONLINE for any active connection
- [x] Hotspot SSID DYX_BASE visible
- [x] Clients can connect and get DHCP
- [x] DNS resolution works on client
- [x] Internet access working on client
- [x] API access at 192.168.4.1:8000
- [x] OLED service running without errors

---

## Next Steps (Future)

1. Add WebSocket for real-time OLED updates instead of polling
2. Cache API responses to reduce network calls
3. Add error handling for network timeouts on OLED
4. Consider IPv6 support for hotspot
5. Add QoS rules for NTRIP traffic prioritization
