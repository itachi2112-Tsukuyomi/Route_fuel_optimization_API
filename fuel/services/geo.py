"""Small, dependency-light geometry helpers (all distances in miles)."""

import numpy as np

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance; works on scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def to_unit_vectors(lats, lons):
    """Lat/lng (degrees) -> points on the unit sphere, for KD-tree lookups.

    Euclidean (chord) distance between unit vectors is monotonic with and,
    for short distances, practically equal to great-circle distance / R.
    """
    lat = np.radians(np.asarray(lats, dtype=float))
    lon = np.radians(np.asarray(lons, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def cumulative_miles(coords):
    """Cumulative distance along a polyline of (lng, lat) pairs."""
    coords = np.asarray(coords, dtype=float)
    seg = haversine_miles(coords[:-1, 1], coords[:-1, 0], coords[1:, 1], coords[1:, 0])
    return np.concatenate(([0.0], np.cumsum(seg)))


def densify(coords, step_miles):
    """Resample a (lng, lat) polyline so points are at most `step_miles` apart.

    Returns (points, mile_markers). Linear interpolation in lat/lng is fine at
    this scale (segments from the router are already short).
    """
    coords = np.asarray(coords, dtype=float)
    cum = cumulative_miles(coords)
    total = cum[-1]
    n = max(int(np.ceil(total / step_miles)) + 1, 2)
    markers = np.linspace(0.0, total, n)
    lngs = np.interp(markers, cum, coords[:, 0])
    lats = np.interp(markers, cum, coords[:, 1])
    return np.column_stack((lngs, lats)), markers


def thin(coords, min_spacing_miles):
    """Drop vertices closer than `min_spacing_miles` to the last kept one.

    Used only to shrink the geometry we send back to clients.
    """
    coords = np.asarray(coords, dtype=float)
    if len(coords) <= 2:
        return coords.tolist()
    cum = cumulative_miles(coords)
    kept = [0]
    last = 0.0
    for i in range(1, len(coords) - 1):
        if cum[i] - last >= min_spacing_miles:
            kept.append(i)
            last = cum[i]
    kept.append(len(coords) - 1)
    return np.round(coords[kept], 5).tolist()
