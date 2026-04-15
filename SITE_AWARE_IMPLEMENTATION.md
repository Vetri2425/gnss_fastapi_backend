# Site-Aware Base Position Management - Implementation Summary

**Date Implemented:** April 15, 2026  
**Status:** ✅ COMPLETE

## Overview

This implementation adds intelligent site detection to the GNSS FastAPI RTK Base Station. After power cuts or relocations, the system now:

1. **Saves surveyed ECEF coordinates** to `data/base_position.json` after each survey
2. **Detects location changes** by comparing current 3D fix against saved position
3. **Prompts user confirmation** if location has changed (>100m threshold)
4. **Auto-applies saved position** if same site is detected
5. **Provides 15-minute grace period** for operator decision before auto-resurvey timeout

## Implementation Details

### 1. CHANGE 1 — Geodesy Utilities (`app/gnss/geodesy.py`)

Added `ecef_distance()` function for calculating straight-line distance between two ECEF points.

```python
def ecef_distance(x1, y1, z1, x2, y2, z2) -> float
```

### 2. CHANGE 2 — AutoFlow Orchestrator (`app/gnss/autoflow.py`)

#### New States

```python
class AutoflowState(str, Enum):
    CHECKING_POSITION = "CHECKING_POSITION"   # Verify if same site
    AWAITING_CONFIRM  = "AWAITING_CONFIRM"    # Wait for user decision
    APPLY_FIXED_BASE  = "APPLY_FIXED_BASE"    # Apply saved coordinates
```

#### New Constants

```python
_BASE_POSITION_FILE: Path = Config.DATA_DIR / "base_position.json"
_LOCATION_CHANGE_THRESHOLD_M: float = 100.0      # metres
_AWAITING_CONFIRM_TIMEOUT_S: float = 900.0       # 15 minutes
```

#### New Instance Variables

- `self._confirm_resurvey: threading.Event` — User confirms resurvey
- `self._skip_resurvey: threading.Event` — User confirms same site

#### New Public Methods

```python
def confirm_resurvey() -> None       # User triggered resurvey
def skip_resurvey() -> None          # User confirmed same site
```

#### New Helper Methods

```python
def _load_base_position() -> dict | None
def _save_base_position(ecef_x, ecef_y, ecef_z, accuracy) -> None
```

#### Phase 1c: Location Checking Logic

New phase inserted before survey-in:

1. **Load saved position** from `base_position.json`
2. **Wait for 3D fix** (max 60 seconds)
3. **Calculate distance** between current LLH and saved ECEF
4. **Decision logic:**
   - Distance **< 100m** → Apply saved position directly (skip survey)
   - Distance **> 100m** → Notify frontend, wait for user decision
   - **15-minute timeout** → Auto-resurvey if no user input

#### Survey Completion

After successful survey, coordinates are saved via:
```python
self._save_base_position(
    ecef_x=survey.ecef_x,
    ecef_y=survey.ecef_y,
    ecef_z=survey.ecef_z,
    accuracy=survey.mean_accuracy,
)
```

### 3. CHANGE 3 — REST API Endpoints (`app/api/routes.py`)

#### New Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/base/saved-position` | Return saved base position |
| DELETE | `/api/v1/base/saved-position` | Delete saved position (forces resurvey) |
| POST | `/api/v1/base/confirm-resurvey` | User confirms location changed |
| POST | `/api/v1/base/skip-resurvey` | User confirms same site |

## Data Structure

### `data/base_position.json`

```json
{
  "ecef_x": 4500123.456,
  "ecef_y": 1200567.890,
  "ecef_z": 4301234.567,
  "accuracy": 0.087,
  "surveyed_at": "2026-04-15T10:23:45.123456+00:00"
}
```

## WebSocket Events (from orchestrator)

### `location_changed` Event

Emitted when location difference exceeds threshold:

```javascript
{
  "distance_metres": 245.3,
  "saved_position": {
    "ecef_x": 4500123.456,
    "ecef_y": 1200567.890,
    "ecef_z": 4301234.567,
    "accuracy": 0.087,
    "surveyed_at": "2026-04-14T08:15:30.000000+00:00"
  },
  "current_ecef": {
    "x": 4500250.123,
    "y": 1200600.456,
    "z": 4301300.789
  },
  "auto_resurvey_in_seconds": 900
}
```

## Workflow

### Boot Sequence (with saved position)

```
WAITING_SERIAL
    ↓
CHECKING_POSITION
    ├─ Load saved position
    ├─ Get 3D fix
    ├─ Calculate distance
    │
    ├─ Same site (<100m)
    │  └─ APPLY_FIXED_BASE
    │      └─ Transition to ENABLING_RTCM
    │
    ├─ Different location (>100m)
    │  └─ AWAITING_CONFIRM (15min timeout)
    │      ├─ /confirm-resurvey → SURVEY
    │      ├─ /skip-resurvey → APPLY_FIXED_BASE
    │      └─ Timeout → SURVEY (auto-resurvey)
    │
    └─ No 3D fix (60s timeout or no saved pos)
       └─ SURVEY (normal flow)
```

## Verification Steps

### 1. Check service logs

```bash
sudo systemctl restart gnss-backend
sudo journalctl -u gnss-backend -f --no-pager | grep -i "checking\|saved\|distance\|fixed\|confirm"
```

### 2. Verify file creation

After first survey:
```bash
cat data/base_position.json
```

### 3. Test location change detection

**Simulate relocation:**
```bash
# Delete saved position to force resurvey
curl -X DELETE http://localhost:8000/api/v1/base/saved-position

# Or manually delete
rm data/base_position.json

# Restart service
sudo systemctl restart gnss-backend
```

### 4. Check new endpoints

```bash
# Get saved position
curl http://localhost:8000/api/v1/base/saved-position | jq

# Confirm resurvey (during AWAITING_CONFIRM)
curl -X POST http://localhost:8000/api/v1/base/confirm-resurvey

# Skip resurvey (during AWAITING_CONFIRM)
curl -X POST http://localhost:8000/api/v1/base/skip-resurvey

# Delete saved position
curl -X DELETE http://localhost:8000/api/v1/base/saved-position
```

## Suggested Frontend Implementation

### Location Change Dialog

1. Listen for `location_changed` WebSocket event
2. Show modal with:
   - Current distance from saved site
   - Distance threshold (100m)
   - Time remaining before auto-resurvey (15m countdown)
   - Two buttons:
     - "Same Site" → POST `/base/skip-resurvey`
     - "New Location" → POST `/base/confirm-resurvey`

### GNSS Status Display

Add to status view:
- **Current Mode:** "Applying saved base" or "Waiting confirmation"
- **Location Match:** "Same site (25.3m away)" or "New location (245m away)"
- **RTCM Status:** Already sending from saved coordinates

## Additional Improvements Noticed

1. **Optional: Dynamic threshold** — Make 100m configurable via environment variable
2. **Optional: Accuracy re-verification** — If saved accuracy > 0.5m, trigger fresh survey
3. **Optional: Site naming** — Store site name/description with ECEF in base_position.json
4. **Optional: Multi-site history** — Maintain `/data/base_position_history.json` with last 5 sites
5. **Optional: LLH fallback** — Store LLH for faster human readability in JSON

## Testing Checklist

- [ ] First boot: surveys and saves position → `base_position.json` created
- [ ] Second boot (same location): skips survey, applies saved base → RTCM streaming with same ARP
- [ ] Location change (>100m): triggers `location_changed` event → waits for user input
- [ ] User confirms resurvey: completes new survey → saves new coordinates
- [ ] User skips resurvey: applies saved base → continues with old ARP (may cause RTK issues if rover moved)
- [ ] 15-minute timeout: auto-triggers survey if no user action
- [ ] Manual delete: next boot triggers survey
- [ ] GET `/base/saved-position`: returns current saved position or `{"saved": false}`
- [ ] DELETE `/base/saved-position`: removes file and forces next boot survey

## Notes

- All ECEF/LLH conversions use WGS84 ellipsoid model (pyubx2 compatible)
- 100m threshold is conservative for site relocation detection
- 15-minute timeout allows operator response during power restoration
- Saved position file survives systemd service restarts
- Thread-safety ensured via `threading.Event` for confirm/skip signals
- Backward compatible — no changes to existing survey/RTCM/NTRIP flows
