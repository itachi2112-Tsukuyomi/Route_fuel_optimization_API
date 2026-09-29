"""Turn user input ("Chicago, IL", "60601", "41.88,-87.63") into coordinates.

Resolution is offline first: the `zipcodes` package ships every US ZIP code
with its city, state and centroid, so cities and ZIPs resolve from memory
with no network call. Only free text that the offline index can't match
falls back to Nominatim (one HTTP call).
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import requests
import zipcodes
from django.conf import settings

# Rough bounding boxes: contiguous US, Alaska, Hawaii.
US_BOUNDS = (
    (24.0, 50.0, -125.5, -66.5),
    (51.0, 71.5, -180.0, -129.0),
    (18.5, 22.5, -161.0, -154.5),
)

STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI",
    "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}
STATE_CODES = set(STATE_NAMES.values())

# Applied word by word so "FT WORTH" and "FORT WORTH" land on the same key.
WORD_ALIASES = {"ST": "SAINT", "STE": "SAINTE", "MT": "MOUNT", "FT": "FORT", "PT": "POINT"}

LATLNG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
ZIP_RE = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")


class GeocodingError(ValueError):
    """Raised when a location can't be resolved to a point in the USA."""


@dataclass(frozen=True)
class Location:
    lat: float
    lng: float
    label: str
    source: str  # "coordinates" | "zip" | "city" | "nominatim"

    def as_dict(self):
        return {"label": self.label, "lat": round(self.lat, 5), "lng": round(self.lng, 5)}


def normalize_city(name):
    words = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("'", "")).split()
    return " ".join(WORD_ALIASES.get(w, w) for w in words)


def in_usa(lat, lng):
    return any(s <= lat <= n and w <= lng <= e for s, n, w, e in US_BOUNDS)


@lru_cache(maxsize=1)
def _indexes():
    """Build (city_index, zip_index) once per process (~0.3 s)."""
    standard = defaultdict(list)  # delivery ZIPs: best estimate of a town centre
    other = defaultdict(list)  # PO box / unique ZIPs: used only when nothing else
    zips = {}
    for z in zipcodes.list_all():
        if not z.get("lat") or not z.get("long"):
            continue
        lat, lng = float(z["lat"]), float(z["long"])
        zips[z["zip_code"]] = (lat, lng, f"{z['city']}, {z['state']} {z['zip_code']}")
        bucket = standard if z.get("zip_code_type") == "STANDARD" else other
        for city in [z["city"], *(z.get("acceptable_cities") or [])]:
            bucket[(normalize_city(city), z["state"])].append((lat, lng))
    cities = {}
    for key in other.keys() | standard.keys():
        lats, lngs = zip(*(standard.get(key) or other[key]))
        cities[key] = (sum(lats) / len(lats), sum(lngs) / len(lngs))
    return cities, zips


def lookup_city(city, state):
    """Offline centroid of a US city, or None. Tolerates 'De Forest' vs 'Deforest'."""
    cities, _ = _indexes()
    key = normalize_city(city)
    state = state.strip().upper()
    hit = cities.get((key, state)) or cities.get((key.replace(" ", ""), state))
    return hit


def _parse_city_state(text):
    """'Chicago, IL' / 'Chicago IL' / 'Chicago, Illinois' / '..., USA' -> (city, ST)."""
    text = re.sub(r",?\s*(USA|US|UNITED STATES)\.?\s*$", "", text.strip(), flags=re.I)
    if "," in text:
        city, _, state = text.rpartition(",")
    else:
        city, _, state = text.rpartition(" ")
    state = state.strip().upper()
    state = STATE_NAMES.get(state, state)
    city = city.strip()
    if not city or state not in STATE_CODES:
        return None
    return city, state


def _nominatim(query):
    cfg = settings.FUEL_PLANNER
    if not cfg["NOMINATIM_ENABLED"]:
        return None
    try:
        resp = requests.get(
            cfg["NOMINATIM_URL"],
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": cfg["HTTP_USER_AGENT"]},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not results:
        return None
    r = results[0]
    return Location(float(r["lat"]), float(r["lon"]), r.get("display_name", query), "nominatim")


def geocode(query):
    """Resolve a location string to a `Location` inside the USA."""
    if not query or not str(query).strip():
        raise GeocodingError("Location is required.")
    query = str(query).strip()

    location = None
    if m := LATLNG_RE.match(query):
        lat, lng = float(m.group(1)), float(m.group(2))
        location = Location(lat, lng, f"{lat:.5f},{lng:.5f}", "coordinates")
    elif m := ZIP_RE.match(query):
        hit = _indexes()[1].get(m.group(1))
        if hit:
            location = Location(hit[0], hit[1], hit[2], "zip")
    elif parsed := _parse_city_state(query):
        city, state = parsed
        hit = lookup_city(city, state)
        if hit:
            location = Location(hit[0], hit[1], f"{city.title()}, {state}", "city")

    if location is None:
        location = _nominatim(query)
    if location is None:
        raise GeocodingError(
            f"Could not find '{query}'. Use 'City, ST', a 5-digit ZIP code or 'lat,lng'."
        )
    if not in_usa(location.lat, location.lng):
        raise GeocodingError(f"'{query}' is outside the USA.")
    return location
