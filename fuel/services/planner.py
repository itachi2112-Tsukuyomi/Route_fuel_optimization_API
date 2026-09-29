"""Orchestrates geocode -> route (1 API call) -> stations on route -> optimal stops."""

import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache

from . import routing
from .geo import thin
from .geocoding import geocode
from .optimizer import plan_fuel_stops
from .stations import get_index, stations_along_route

logger = logging.getLogger(__name__)

START_TANK_CHOICES = ("full", "empty")


def _route_cache_key(start, finish):
    raw = f"{start.lat:.4f},{start.lng:.4f};{finish.lat:.4f},{finish.lng:.4f}"
    return "route:" + hashlib.sha1(raw.encode()).hexdigest()


def get_route_cached(start, finish):
    """Routing API is called at most once per origin/destination pair."""
    key = _route_cache_key(start, finish)
    route = cache.get(key)
    if route is None:
        route = routing.get_route(start, finish)
        cache.set(key, route, settings.FUEL_PLANNER["ROUTE_CACHE_SECONDS"])
        return route, False
    return route, True


def plan_trip(start, finish, start_tank="full", stop_penalty=None):
    """Build the full API response for a trip from two location strings."""
    cfg = settings.FUEL_PLANNER
    max_range, mpg = cfg["MAX_RANGE_MILES"], cfg["MILES_PER_GALLON"]
    if stop_penalty is None:
        stop_penalty = cfg["STOP_PENALTY_DOLLARS"]
    timings = {}
    t0 = time.perf_counter()

    index = get_index()
    if len(index) == 0:
        raise routing.RoutingError(
            "No fuel stations loaded. Run `python manage.py load_stations`.", status=503
        )

    start = geocode(start)
    finish = geocode(finish)
    timings["geocode_ms"] = (time.perf_counter() - t0) * 1000

    t = time.perf_counter()
    route, cached = get_route_cached(start, finish)
    timings["routing_ms"] = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    idx, miles, offsets = stations_along_route(route.coordinates, cfg["CORRIDOR_MILES"], index)
    prices = index.prices[idx]

    if start_tank == "empty":
        # Tank holds just enough to reach the first station on the route, so
        # the total cost covers (almost) all of the fuel the trip burns.
        start_fuel = float(miles[0]) if len(miles) else 0.0
    else:
        start_fuel = max_range

    plan = plan_fuel_stops(
        miles.tolist(), prices.tolist(), route.distance_miles, max_range, mpg, start_fuel,
        stop_penalty=stop_penalty,
    )
    timings["optimize_ms"] = (time.perf_counter() - t) * 1000

    stops = []
    for n, p in enumerate(plan.purchases, start=1):
        station_i = int(idx[p.position])
        stops.append(
            {
                "stop": n,
                **index.station_dict(station_i),
                "mile_marker": round(p.mile, 1),
                "distance_from_route_miles": round(float(offsets[p.position]), 1),
                "fuel_in_tank_on_arrival_gallons": round(p.fuel_before_gallons, 2),
                "gallons_purchased": round(p.gallons, 2),
                "cost": round(p.cost, 2),
            }
        )

    timings["total_ms"] = (time.perf_counter() - t0) * 1000
    logger.info(
        "Planned %s -> %s: %.0f mi, %d stops, $%.2f (route cached=%s, %.0f ms)",
        start.label, finish.label, route.distance_miles, len(stops),
        plan.total_cost, cached, timings["total_ms"],
    )

    return {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
            "geometry": {"type": "LineString", "coordinates": thin(route.coordinates, 1.0)},
        },
        "vehicle": {
            "max_range_miles": max_range,
            "miles_per_gallon": mpg,
            "tank_size_gallons": max_range / mpg,
            "start_tank": start_tank,
            "start_fuel_gallons": round(start_fuel / mpg, 2),
        },
        "optimizer": {"stop_penalty_dollars": stop_penalty},
        "fuel_summary": {
            "total_fuel_cost": round(plan.total_cost, 2),
            "total_gallons_purchased": round(plan.total_gallons, 2),
            "trip_fuel_used_gallons": round(route.distance_miles / mpg, 2),
            "fuel_left_at_destination_gallons": round(plan.fuel_left_gallons, 2),
            "average_price_paid": (
                round(plan.total_cost / plan.total_gallons, 3) if plan.total_gallons else None
            ),
            "number_of_stops": len(stops),
            "stations_considered": int(len(idx)),
        },
        "fuel_stops": stops,
        "meta": {
            "routing_api_calls": 0 if cached else 1,
            "geocoding_api_calls": sum(loc.source == "nominatim" for loc in (start, finish)),
            "route_cached": cached,
            "timings_ms": {k: round(v, 1) for k, v in timings.items()},
        },
    }
