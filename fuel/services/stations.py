"""In-memory station index and "which stations are along this route" lookup.

All ~6.6k stations are loaded once into numpy arrays, so a request never
touches the database. Matching a route against them is a KD-tree query that
takes a few milliseconds even for a coast-to-coast trip.
"""

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from fuel.models import FuelStation

from .geo import EARTH_RADIUS_MILES, densify, to_unit_vectors

# Route is resampled at this spacing before matching; the error it adds to a
# station's mile marker is at most half of it.
ROUTE_SAMPLE_MILES = 0.5


@dataclass
class StationIndex:
    ids: np.ndarray
    names: np.ndarray
    addresses: np.ndarray
    cities: np.ndarray
    states: np.ndarray
    prices: np.ndarray
    lats: np.ndarray
    lngs: np.ndarray
    vectors: np.ndarray

    def __len__(self):
        return len(self.ids)

    def station_dict(self, i):
        return {
            "opis_id": int(self.ids[i]),
            "name": str(self.names[i]),
            "address": str(self.addresses[i]),
            "city": str(self.cities[i]),
            "state": str(self.states[i]),
            "price_per_gallon": round(float(self.prices[i]), 3),
            "lat": round(float(self.lats[i]), 5),
            "lng": round(float(self.lngs[i]), 5),
        }


_index = None
_lock = threading.Lock()


def get_index():
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = _build_index()
    return _index


def reset_index():
    global _index
    _index = None


def _build_index():
    rows = list(
        FuelStation.objects.values_list(
            "opis_id", "name", "address", "city", "state", "price", "latitude", "longitude"
        )
    )
    cols = list(zip(*rows)) if rows else [()] * 8
    lats = np.array(cols[6], dtype=float)
    lngs = np.array(cols[7], dtype=float)
    return StationIndex(
        ids=np.array(cols[0], dtype=int),
        names=np.array(cols[1], dtype=object),
        addresses=np.array(cols[2], dtype=object),
        cities=np.array(cols[3], dtype=object),
        states=np.array(cols[4], dtype=object),
        prices=np.array(cols[5], dtype=float),
        lats=lats,
        lngs=lngs,
        vectors=to_unit_vectors(lats, lngs) if rows else np.empty((0, 3)),
    )


def stations_along_route(coordinates, corridor_miles, index=None):
    """Stations within `corridor_miles` of the route.

    Returns (station_indices, mile_markers, offsets_miles), sorted by mile marker.
    """
    index = index or get_index()
    empty = (np.empty(0, dtype=int), np.empty(0), np.empty(0))
    if len(index) == 0 or len(coordinates) < 2:
        return empty

    points, markers = densify(coordinates, ROUTE_SAMPLE_MILES)

    # Cheap bounding-box prefilter (corridor padded generously in degrees).
    pad = corridor_miles / 50.0 + 0.1
    lng_min, lat_min = points.min(axis=0) - pad
    lng_max, lat_max = points.max(axis=0) + pad
    candidates = np.flatnonzero(
        (index.lats >= lat_min) & (index.lats <= lat_max)
        & (index.lngs >= lng_min) & (index.lngs <= lng_max)
    )
    if candidates.size == 0:
        return empty

    tree = cKDTree(to_unit_vectors(points[:, 1], points[:, 0]))
    chord, nearest = tree.query(
        index.vectors[candidates], distance_upper_bound=corridor_miles / EARTH_RADIUS_MILES
    )
    hit = np.isfinite(chord)
    candidates, chord, nearest = candidates[hit], chord[hit], nearest[hit]

    miles = markers[nearest]
    order = np.lexsort((index.prices[candidates], miles))
    return candidates[order], miles[order], (chord * EARTH_RADIUS_MILES)[order]
