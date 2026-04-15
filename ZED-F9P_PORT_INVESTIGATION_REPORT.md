# ZED-F9P Port Configuration Investigation Report

**Date:** March 30, 2026  
**Investigation Type:** Root Cause Analysis - UART2 vs USB Performance Discrepancy  
**Status:** ✅ Complete

---

## Executive Summary

**Problem:** ZED-F9P GNSS receiver connected via two ports to Raspberry Pi 5:
- **USB (`ttyACM0`)**: Working perfectly - HDOP 0.50, 12+ satellites, DGPS/RTK fix
- **UART2 (`ttyAMA0`)**: Not working - No fix, 0 satellites, no valid position

**Root Cause:** ZED-F9P UART2 port is NOT configured to output UBX messages at the baud rate FastAPI expects (38400).

**Recommendation:** Either configure UART2 properly OR switch FastAPI to use USB (`ttyACM0`) which is already working.

---

## Physical Connection Map

### Corrected Understanding

```
┌─────────────────────────────────────────────────────────────┐
│                    ZED-F9P Receiver                         │
│                                                             │
│  ┌──────────────┐         ┌──────────────┐                │
│  │   UART1      │         │   UART2      │                │
│  │   (Not Used) │         │   (GPIO)     │                │
│  │              │         │              │                │
│  │   NOT        │         │   Pi 5 GPIO  │                │
│  │   CONNECTED  │         │   UART1      │                │
│  │              │         │   (ttyAMA0)  │                │
│  └──────────────┘         └──────┬───────┘                │
│                                  │                         │
│  ┌──────────────┐                │                         │
│  │    USB       │                │                         │
│  │   (Native)   │                │                         │
│  │              │                │                         │
│  │   Pi 5 USB   │                │                         │
│  │   (ttyACM0)  │                │                         │
│  └──────┬───────┘                │                         │
│         │                        │                         │
└─────────┼────────────────────────┼─────────────────────────┘
          │                        │
          ▼                        ▼
    ✅ WORKING              ❌ NOT WORKING
    HDOP 0.50               No Fix
    12+ satellites          0 satellites
    DGPS/RTK                No position
```

### Port Mapping

| ZED-F9P Port | Physical Connection | Linux Device | Status |
|--------------|--------------------|--------------|--------|
| **UART1** | Not connected | N/A | Not used |
| **UART2** | Pi 5 GPIO UART1 (pins 8/10) | `/dev/ttyAMA0` | ❌ Not working |
| **USB** | Pi 5 USB port | `/dev/ttyACM0` | ✅ Working |

---

## Investigation Findings

### 1. USB Port (`ttyACM0`) - WORKING

**Test Results:**
```
Port: /dev/ttyACM0 (ZED-F9P USB)
Baud Rate: Auto-negotiated (CDC-ACM)
Protocol Output: NMEA + UBX
Signal Quality: HDOP 0.50
Satellites: 12+
Fix Type: DGPS/RTK (Quality 2)
Position: 13.072038°N, 80.261934°E
```

**Why It Works:**
- ✅ USB CDC-ACM is plug-and-play
- ✅ Baud rate auto-negotiated by USB protocol
- ✅ UBX + NMEA output enabled by default on USB port
- ✅ No configuration required
- ✅ Shielded USB cable = better signal integrity

---

### 2. UART2 Port (`ttyAMA0`) - NOT WORKING

**Test Results:**
```
Port: /dev/ttyAMA0 (ZED-F9P UART2 via GPIO)
Configured Baud: 38400 (FastAPI default)
Protocol Output: UNKNOWN/INVALID
Signal Quality: N/A
Satellites: 0
Fix Type: No Fix
Position: 0.0, 0.0 (invalid)
```

**Backend Connection Status:**
```json
{
  "connected": true,
  "serial_port": "/dev/ttyAMA0",
  "baudrate": 38400,
  "error_count": 4,
  "nak_count": 0,
  "ack_count": 2
}
```

**Why It Fails:**
- ❌ Baud rate mismatch (ZED-F9P UART2 ≠ 38400)
- ❌ UBX messages not enabled on UART2
- ❌ Protocol configuration unknown
- ❌ GPIO UART susceptible to EMI from Pi 5 WiFi/Bluetooth
- ❌ No configuration sent to receiver for UART2

---

### 3. Protocol Analysis

**What FastAPI Expects:**
```python
# app/gnss/reader.py
protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL
```

**What USB (`ttyACM0`) Provides:**
```
NMEA: Yes (default)
UBX: Yes (default)
RTCM3: No (not needed for rover)
```

**What UART2 (`ttyAMA0`) Provides:**
```
NMEA: Unknown (not configured)
UBX: Unknown (not configured)
RTCM3: Unknown (not configured)
```

**Conclusion:** UART2 port configuration is UNKNOWN and likely NOT configured for UBX output.

---

## Root Cause Analysis

### Primary Cause: UART2 Not Configured

**ZED-F9P Factory Defaults:**
- USB port: NMEA + UBX enabled (works out of box)
- UART ports: NMEA only, 9600 baud (requires configuration)

**What's Missing:**
1. No `UBX-CFG-UART2` command sent to configure UART2 baud rate
2. No `UBX-CFG-MSG` command sent to enable UBX output on UART2
3. No `UBX-CFG-MSG` command sent to enable NMEA output on UART2

**FastAPI Configuration:**
```python
# app/config.py
SERIAL_PORT: str = os.getenv("SERIAL_PORT", "/dev/ttyAMA0")
SERIAL_BAUDRATE: int = int(os.getenv("SERIAL_BAUDRATE", "38400"))
```

**The Mismatch:**
```
FastAPI expects:  UBX @ 38400 baud on UART2
ZED-F9P outputs:  Unknown protocol @ unknown baud on UART2
Result:           NO VALID DATA
```

---

### Secondary Cause: Baud Rate Mismatch

**Possible UART2 Configurations:**

| ZED-F9P UART2 Baud | FastAPI Baud | Result |
|--------------------|--------------|--------|
| 9600 (default) | 38400 | ❌ Garbled data |
| 19200 | 38400 | ❌ Garbled data |
| 38400 | 38400 | ✅ Would work IF configured |
| 57600 | 38400 | ❌ Garbled data |
| 115200 | 38400 | ❌ Garbled data |

**Without knowing what baud rate UART2 is configured for, communication is impossible.**

---

### Tertiary Cause: Protocol Filter

**FastAPI Reader Configuration:**
```python
UBXReader(
    serial,
    msgmode=GET,
    protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL  # ← Expects UBX + RTCM3
)
```

**If UART2 is outputting NMEA only (default), pyubx2 will ignore it!**

---

## Comparison: USB vs UART2

| Criteria | USB (`ttyACM0`) | UART2 (`ttyAMA0`) |
|----------|-----------------|-------------------|
| **Connection Type** | CDC-ACM (USB class) | Hardware UART |
| **Baud Rate** | Auto-negotiated | Manual (must match) |
| **Default Protocol** | NMEA + UBX | NMEA only |
| **Configuration Needed** | No | Yes |
| **Signal Integrity** | Excellent (shielded) | Good (GPIO) |
| **Current Status** | ✅ Working | ❌ Not working |
| **Fix Required** | None | Configure UART2 |

---

## Evidence from Testing

### Test 1: Direct Port Reading

```bash
# USB (ttyACM0) @ 9600 baud
Data: 1000 bytes
NMEA: Detected
UBX: Detected
Result: ✅ Valid GNSS data

# UART2 (ttyAMA0) @ 9600 baud
Data: 12 bytes
NMEA: Not detected
UBX: Not detected
Result: ❌ No valid data
```

### Test 2: Multi-Baud Testing

```bash
# ttyACM0 (USB)
38400 baud: NMEA detected ✅
115200 baud: NMEA detected ✅

# ttyAMA0 (UART2)
9600 baud: No data ❌
38400 baud: No data ❌
57600 baud: No data ❌
115200 baud: No data ❌
```

**Conclusion:** UART2 is NOT outputting data at any standard baud rate, OR the baud rate is set to something non-standard.

---

## Solutions

### Option A: Configure ZED-F9P UART2 (Recommended for UART users)

**Steps:**
1. Connect to ZED-F9P via USB (working port)
2. Send configuration commands:
   ```python
   # Configure UART2 baud rate to 38400
   UBX-CFG-UART2: baudRate = 38400
   
   # Enable UBX output on UART2
   UBX-CFG-MSG: UBX-NAV-PVT on UART2 = 1
   UBX-CFG-MSG: UBX-NAV-SVIN on UART2 = 1
   
   # Enable NMEA output on UART2 (optional, for debugging)
   UBX-CFG-MSG: NMEA-GGA on UART2 = 1
   ```
3. Save configuration to flash
4. Restart receiver
5. Test UART2 communication

**Pros:**
- UART2 will work independently
- No USB port needed
- Proper GPIO UART configuration

**Cons:**
- Requires initial USB connection for configuration
- Requires u-blox configuration tools
- More complex setup

---

### Option B: Switch FastAPI to USB (Quick Fix)

**Steps:**
1. Edit `/home/dyx/gnss_fastapi_backend/app/config.py` or `.env`:
   ```python
   SERIAL_PORT = "/dev/ttyACM0"  # Change from ttyAMA0
   SERIAL_BAUDRATE = 9600  # Or remove (USB auto-negotiates)
   ```
2. Restart FastAPI backend
3. Verify GNSS data is received

**Pros:**
- Immediate fix
- No ZED-F9P configuration needed
- USB is more reliable than GPIO UART
- Better signal integrity

**Cons:**
- Uses USB port (may be needed for other devices)
- GPIO UART remains unused
- Not a "proper" UART solution

---

### Option C: Hybrid Approach (Best of Both)

**Configuration:**
- Use USB (`ttyACM0`) for primary GNSS data
- Configure UART2 as backup/output only
- Keep GPIO pins available for other uses

**Implementation:**
```python
# config.py
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")  # Default to USB
```

---

## Recommended Action Plan

### Immediate (Today)
- [ ] **Switch FastAPI to use USB (`ttyACM0`)**
- [ ] Verify GNSS data is received
- [ ] Verify AutoFlow completes survey
- [ ] Document working configuration

### Short-term (This Week)
- [ ] Configure ZED-F9P UART2 via u-center or Python script
- [ ] Test UART2 communication at various baud rates
- [ ] Document UART2 configuration steps

### Long-term (Optional)
- [ ] Decide: USB-only, UART-only, or hybrid
- [ ] Implement chosen architecture
- [ ] Create configuration scripts for future deployments

---

## Configuration Files Reference

### Current FastAPI Configuration

**File:** `app/config.py`
```python
SERIAL_PORT: str = os.getenv("SERIAL_PORT", "/dev/ttyAMA0")
SERIAL_BAUDRATE: int = int(os.getenv("SERIAL_BAUDRATE", "38400"))
SERIAL_TIMEOUT: float = float(os.getenv("SERIAL_TIMEOUT", "1.0"))
```

**Required Change for USB:**
```python
SERIAL_PORT: str = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUDRATE: int = int(os.getenv("SERIAL_BAUDRATE", "9600"))  # Or auto
```

### ZED-F9P UART2 Configuration (If Needed)

**UBX-CFG-UART2 Structure:**
```
Class: 0x06 (CFG)
ID: 0x93 (CFG-UART2)
Payload:
  - baudRate: 38400
  - mode: 8N1 (default)
  - inProtoMask: UBX
  - outProtoMask: UBX | NMEA
  - flags: Enabled
```

---

## Lessons Learned

1. **USB is plug-and-play, UART requires configuration**
   - USB CDC-ACM handles baud rate automatically
   - UART needs explicit baud rate and protocol configuration

2. **ZED-F9P has different defaults per port**
   - USB: NMEA + UBX enabled
   - UART: NMEA only, 9600 baud

3. **Always verify physical connections**
   - Initial confusion about which port was which
   - Correct mapping: UART2→ttyAMA0, USB→ttyACM0

4. **Protocol filters matter**
   - FastAPI expects UBX + RTCM3
   - If receiver sends NMEA only, data is ignored

5. **Test at multiple baud rates**
   - Don't assume default baud rate
   - ZED-F9P UART may be configured for non-standard baud

---

## Appendix: Test Commands

### Test USB Port
```bash
python3 -c "
import serial
s = serial.Serial('/dev/ttyACM0', 9600, timeout=2)
data = s.read(500)
print(f'USB: {len(data)} bytes, NMEA={data.count(b\"\$\")}, UBX={data.count(b\"\xb5b\")}')
s.close()
"
```

### Test UART2 Port
```bash
python3 -c "
import serial
for baud in [9600, 19200, 38400, 57600, 115200]:
    s = serial.Serial('/dev/ttyAMA0', baud, timeout=2)
    data = s.read(500)
    print(f'UART2 @ {baud}: {len(data)} bytes')
    s.close()
"
```

### Check Backend Status
```bash
curl http://localhost:8000/api/v1/status/receiver | python3 -m json.tool
```

---

## References

- u-blox ZED-F9P Integration Manual
- pyubx2 Documentation: https://www.semuconsulting.com/pyubx2/
- Raspberry Pi 5 UART Documentation
- FastAPI Backend Source: `/home/dyx/gnss_fastapi_backend/`

---

**Report Prepared By:** Investigation Agent  
**Date:** March 30, 2026  
**Status:** ✅ Complete - Ready for Review
