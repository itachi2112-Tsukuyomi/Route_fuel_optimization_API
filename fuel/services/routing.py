"""Driving route from the free OSRM demo server: exactly one HTTP call per route."""

from dataclasses import dataclass

import requests
from django.conf import settings

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    """The routing service failed or found no drivable route."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


@dataclass
class Route:
    distance_miles: float
    duration_hours: float
    coordinates: list  # [[lng, lat], ...] along the road


def get_route(start, finish):
    """Fetch the fastest driving route between two `Location`s."""
    cfg = settings.FUEL_PLANNER
    url = (
        f"{cfg['OSRM_BASE_URL'].rstrip('/')}/route/v1/driving/"
        f"{start.lng:.6f},{start.lat:.6f};{finish.lng:.6f},{finish.lat:.6f}"
    )
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            headers={"User-Agent": cfg["HTTP_USER_AGENT"]},
            timeout=cfg["OSRM_TIMEOUT_SECONDS"],
        )
        data = resp.json()
    except requests.Timeout as exc:
        raise RoutingError("Routing service timed out.", status=504) from exc
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        message = data.get("message") or data.get("code") or f"HTTP {resp.status_code}"
        status = 422 if data.get("code") in ("NoRoute", "NoSegment") else 502
        raise RoutingError(f"No drivable route found: {message}", status=status)

    route = data["routes"][0]
    return Route(
        distance_miles=route["distance"] / METERS_PER_MILE,
        duration_hours=route["duration"] / 3600,
        coordinates=route["geometry"]["coordinates"],
    )
