"""
Geodesy helpers for WGS84 coordinate conversion.
"""

from math import atan2, cos, degrees, radians, sin, sqrt

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_B = WGS84_A * (1 - WGS84_F)
WGS84_E2 = 1 - (WGS84_B * WGS84_B) / (WGS84_A * WGS84_A)
WGS84_EP2 = (WGS84_A * WGS84_A - WGS84_B * WGS84_B) / (WGS84_B * WGS84_B)


def ecef_to_llh(x: float, y: float, z: float) -> tuple[float, float, float]:
    """
    Convert WGS84 ECEF coordinates to geodetic latitude, longitude, and ellipsoid height.

    Returns:
        Tuple of (latitude_deg, longitude_deg, height_m)
    """
    lon = atan2(y, x)
    p = sqrt((x * x) + (y * y))

    # Handle the polar case without dividing by zero.
    if p < 1e-9:
        lat = radians(90.0 if z >= 0 else -90.0)
        height = abs(z) - WGS84_B
        return degrees(lat), degrees(lon), height

    theta = atan2(z * WGS84_A, p * WGS84_B)
    sin_theta = sin(theta)
    cos_theta = cos(theta)

    lat = atan2(
        z + (WGS84_EP2 * WGS84_B * sin_theta * sin_theta * sin_theta),
        p - (WGS84_E2 * WGS84_A * cos_theta * cos_theta * cos_theta),
    )

    sin_lat = sin(lat)
    n = WGS84_A / sqrt(1 - (WGS84_E2 * sin_lat * sin_lat))
    height = (p / cos(lat)) - n

    return degrees(lat), degrees(lon), height


def ecef_distance(
    x1: float, y1: float, z1: float,
    x2: float, y2: float, z2: float
) -> float:
    """
    Calculate straight-line distance in metres between two ECEF points.
    Uses simple Euclidean distance — valid for short distances (<100km).
    """
    import math
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)
