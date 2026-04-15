Fixed Base Station API Documentation
Overview
This API allows you to configure the ZED-F9P GNSS receiver as a fixed base station using LLH (Latitude, Longitude, Height) coordinates. Once configured, the receiver broadcasts RTCM correction messages to NTRIP casters for rover RTK positioning.

Endpoint: Configure Fixed Base Station
POST /api/v1/base/fixed
Configure the receiver as a fixed base station with known LLH coordinates.

Request Body
Field	Type	Required	Default	Description
latitude	float	Yes	-	Latitude in decimal degrees (−90 to +90)
longitude	float	Yes	-	Longitude in decimal degrees (−180 to +180)
height	float	Yes	-	WGS84 ellipsoid height in meters (NOT MSL). See geoid_separation below.
geoid_separation	float	No	null	EGM96 geoid undulation N (meters). When provided, height is treated as MSL: ellipsoid_h = height + geoid_separation. Example: Chennai N ≈ −6.5 m.
fixed_pos_acc	float	No	0.10	Expected position accuracy in meters (0.001–10.0)
msm_type	string	No	"MSM4"	RTCM MSM type: "MSM4" or "MSM7"
enable_rtcm	boolean	No	true	Re-enable RTCM output after configuration
use_high_precision	boolean	No	false	Enable sub-centimeter HP fields
lat_hp	float	No	null	High-precision lat offset (±1×10⁻⁹ deg)
lon_hp	float	No	null	High-precision lon offset (±1×10⁻⁹ deg)
height_hp	float	No	null	High-precision height offset (meters)
save_to_flash	boolean	No	false	Persist to Flash (default: RAM+BBR only)
Example Request (Option A — you already have ellipsoid height)
{
  "latitude": 13.0720445,
  "longitude": 80.2619310,
  "height": 2.87,
  "fixed_pos_acc": 0.10,
  "msm_type": "MSM4",
  "enable_rtcm": true,
  "use_high_precision": false,
  "save_to_flash": false
}

Example Request (Option B — you have MSL height + geoid separation)
{
  "latitude": 13.0720445,
  "longitude": 80.2619310,
  "height": 9.37,
  "geoid_separation": -6.50,
  "fixed_pos_acc": 0.10,
  "msm_type": "MSM4",
  "enable_rtcm": true,
  "use_high_precision": false,
  "save_to_flash": false
}
Both examples configure the same ellipsoid height (2.87 m) on the receiver.
Geoid undulation for Chennai ≈ −6.5 m (EGM96). Look up your location at: https://geoid.bgi.obs-mip.fr/

Response
{
  "success": true,
  "message": "Fixed base station configured successfully",
  "effective_mode": "FIXED",
  "applied_llh": {
    "latitude": 13.0720445,
    "longitude": 80.261931,
    "height_ellipsoid": 2.87,
    "height_input": 9.37,
    "geoid_separation": -6.50
  },
  "applied_accuracy": 0.1,
  "layers_applied": "RAM+BBR",
  "rtcm_enabled": true
}
Response Fields
Field	Type	Description
success	boolean	Whether configuration succeeded
message	string	Human-readable status message
effective_mode	string	Applied mode (always "FIXED")
applied_llh	object	height_ellipsoid = value sent to receiver; height_input = original request value; geoid_separation = included when conversion was done
applied_accuracy	float	Applied accuracy in meters
layers_applied	string	Memory layers: "RAM", "RAM+BBR", or "RAM+BBR+FLASH"
rtcm_enabled	boolean	Whether RTCM output was re-enabled
HTTP Status Codes
Code	Meaning
200	Success
400	Invalid coordinates or missing required fields
500	Receiver rejected command (NAK) or timeout
503	GNSS reader not initialized
Endpoint: Configure RTCM Output
POST /api/v1/rtcm/configure
Enable or disable RTCM message output.

Request Body
Field	Type	Required	Default	Description
msm_type	string	No	"MSM4"	"MSM4" or "MSM7"
enable	boolean	No	true	Enable or disable RTCM
enable_beidou	boolean	No	null	If true, selects MSM7
Example Request
{
  "msm_type": "MSM4",
  "enable": true
}
Response
{
  "success": true,
  "message": "RTCM enabled (BeiDou: False)",
  "type": "rtcm_configure"
}
Endpoint: Get System Status
GET /api/v1/status
Get complete system status including position, survey, RTCM, NTRIP, and receiver status.

Response
{
  "position": {
    "latitude": 13.0720445,
    "longitude": 80.2619310,
    "altitude": 9.37,
    "accuracy": 0.082,
    "fix_type": 5,
    "fix_type_str": "rtk_float",
    "num_satellites": 31
  },
  "survey": {
    "active": false,
    "valid": false,
    "in_progress": false
  },
  "rtcm": {
    "enabled": true,
    "msm_type": "MSM4",
    "message_counts": {},
    "total_messages_sent": 0
  },
  "ntrip": {
    "enabled": true,
    "connected": true,
    "host": "caster.emlid.com",
    "port": 2101,
    "mountpoint": "MP23960",
    "bytes_sent": 284118,
    "uptime": 575.4
  },
  "receiver": {
    "connected": true,
    "serial_port": "/dev/ttyAMA0",
    "baudrate": 38400
  }
}
Endpoint: Get AutoFlow Status
GET /api/v1/autoflow/status
Get AutoFlow orchestrator status including NTRIP streaming state.

Response
{
  "state": "STREAMING",
  "enabled": true,
  "last_error": null,
  "config": {
    "ntrip_host": "caster.emlid.com",
    "ntrip_port": 2101,
    "ntrip_mountpoint": "MP23960",
    "ntrip_username": "u98264",
    "msm_type": "MSM4"
  },
  "ntrip": {
    "connected": true,
    "bytes_sent": 284118,
    "uptime": 575.4,
    "host": "caster.emlid.com",
    "port": 2101,
    "mountpoint": "MP23960"
  }
}
Complete Workflow
1. Get Current Position (for survey-based setup)
GET /api/v1/status/position
Wait for survey to complete, then use the ECEF coordinates from:

GET /api/v1/status/survey
2. Configure Fixed Base Station
POST /api/v1/base/fixed
Content-Type: application/json

{
  "latitude": 13.0720445,
  "longitude": 80.2619310,
  "height": 9.37,
  "geoid_separation": -6.50,
  "fixed_pos_acc": 0.10,
  "msm_type": "MSM4",
  "enable_rtcm": true
}
3. Verify Configuration
GET /api/v1/status
Check:

rtcm.enabled = true
rtcm.msm_type = "MSM4" or "MSM7"
ntrip.connected = true
4. Start NTRIP Streaming (if not auto-started)
POST /api/v1/ntrip/start
Content-Type: application/json

{
  "host": "caster.emlid.com",
  "port": 2101,
  "mountpoint": "MP23960",
  "password": "your-password",
  "username": "your-username",
  "ntrip_version": 1
}
MSM4 vs MSM7
Feature	MSM4	MSM7
Precision	Standard	High
Message Size	Smaller	Larger
Constellations	GPS, GLONASS, Galileo, BeiDou	GPS, GLONASS, Galileo, BeiDou
Use Case	General RTK base	High-precision applications
Messages	1074, 1084, 1094, 1124	1077, 1087, 1097, 1127
Recommendation: Use MSM4 for most applications. Use MSM7 only when sub-centimeter rover accuracy is required.

Error Handling
Common Errors
Error	Cause	Solution
NAK from receiver	Invalid coordinates or receiver busy	Verify coordinates are valid; wait for receiver to be idle
Timeout waiting for ACK	Serial communication issue	Check serial connection; restart receiver
400 Bad Request	Invalid latitude/longitude range	Ensure lat: −90 to +90, lon: −180 to +180
503 Service Unavailable	GNSS reader not initialized	Restart the backend service
Frontend Integration Example (TypeScript)
interface FixedBaseRequest {
  latitude: number;
  longitude: number;
  /** WGS84 ellipsoid height OR MSL height (if geoid_separation is also provided) */
  height: number;
  /** EGM96 geoid undulation N for your location. When supplied, height is MSL and
   *  the API converts: ellipsoid_h = height + geoid_separation */
  geoid_separation?: number;
  fixed_pos_acc?: number;
  msm_type?: 'MSM4' | 'MSM7';
  enable_rtcm?: boolean;
  use_high_precision?: boolean;
  lat_hp?: number;
  lon_hp?: number;
  height_hp?: number;
  save_to_flash?: boolean;
}

interface FixedBaseResponse {
  success: boolean;
  message: string;
  effective_mode: string;
  applied_llh: {
    latitude: number;
    longitude: number;
    height_ellipsoid: number;   // value sent to receiver (WGS84 HAE)
    height_input: number;       // original request value
    geoid_separation?: number;  // present only when conversion was done
  };
  applied_accuracy: number;
  layers_applied: string;
  rtcm_enabled: boolean;
}

async function configureFixedBase(config: FixedBaseRequest): Promise<FixedBaseResponse> {
  const response = await fetch('/api/v1/base/fixed', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Configuration failed');
  }
  
  return response.json();
}

// Usage — provide MSL height + geoid separation for automatic conversion
try {
  const result = await configureFixedBase({
    latitude: 13.0720445,
    longitude: 80.2619310,
    height: 9.37,           // MSL height from survey/map
    geoid_separation: -6.50, // EGM96 undulation for Chennai
    fixed_pos_acc: 0.10,
    msm_type: 'MSM4',
    enable_rtcm: true,
  });
  // result.applied_llh.height_ellipsoid === 2.87 (sent to receiver)
  console.log('Fixed base configured:', result);
} catch (error) {
  console.error('Configuration failed:', error);
}
Notes
Memory Layers:

RAM only: Lost on power cycle
RAM+BBR (default): Persists with battery backup
RAM+BBR+FLASH: Permanent storage (use sparingly)
High Precision Fields:

Optional sub-centimeter accuracy
HP fields: ±99 units (lat/lon: 1×10⁻⁹ deg, height: 0.1mm)
Most applications don’t need HP fields
Command Sequence:
The API automatically:

Disables RTCM (prevent stale data broadcast)
Disables existing TMODE
Applies fixed LLH configuration
Re-enables RTCM (if requested)
AMA0 Path:
This endpoint uses the /dev/ttyAMA0 application path.

ACM0 is debug-only.