from unittest import mock

import numpy as np
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from fuel.models import FuelStation
from fuel.services import routing, stations
from fuel.services.geo import cumulative_miles
from fuel.services.routing import Route, RoutingError

# A straight east-west "road" along latitude 40 from Nebraska to Illinois.
ROUTE_COORDS = [[lng, 40.0] for lng in np.linspace(-100.0, -88.0, 200)]
ROUTE_MILES = float(cumulative_miles(ROUTE_COORDS)[-1])  # ~632 miles
FAKE_ROUTE = Route(ROUTE_MILES, ROUTE_MILES / 60, ROUTE_COORDS)


def make_station(opis_id, lng, price, lat=40.0, **extra):
    return FuelStation.objects.create(
        opis_id=opis_id, name=f"Stop {opis_id}", address="I-80, EXIT 1",
        city="Town", state="NE", price=price, latitude=lat, longitude=lng, **extra,
    )


class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        stations.reset_index()
        make_station(1, -99.0, "3.90")   # ~53 mi
        make_station(2, -97.0, "2.95")   # ~159 mi (cheap)
        make_station(3, -94.0, "3.60")   # ~319 mi
        make_station(4, -91.0, "3.10")   # ~479 mi
        make_station(5, -95.0, "1.00", lat=41.0)  # cheapest, but 69 mi off route
        self.addCleanup(stations.reset_index)

    def get(self, **params):
        return self.client.get("/api/route/", {"start": "40,-100", "finish": "40,-88", **params})

    @mock.patch.object(routing, "get_route", return_value=FAKE_ROUTE)
    def test_plan_with_full_tank(self, get_route):
        resp = self.get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertAlmostEqual(data["route"]["distance_miles"], ROUTE_MILES, delta=0.1)
        # 632 miles with 500 in the tank: buy the ~132 missing miles at the cheapest
        # reachable station ($2.95), not the off-route $1.00 one.
        stops = data["fuel_stops"]
        self.assertEqual([s["opis_id"] for s in stops], [2])
        self.assertAlmostEqual(stops[0]["gallons_purchased"], (ROUTE_MILES - 500) / 10, delta=0.2)
        summary = data["fuel_summary"]
        self.assertAlmostEqual(summary["total_fuel_cost"], stops[0]["cost"], delta=0.01)
        self.assertEqual(summary["stations_considered"], 4)
        self.assertEqual(data["meta"]["routing_api_calls"], 1)
        self.assertIn("/api/route/map/?", data["map_url"])
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")

    @mock.patch.object(routing, "get_route", return_value=FAKE_ROUTE)
    def test_empty_tank_pays_for_whole_trip(self, get_route):
        data = self.get(start_tank="empty", stop_penalty=0).json()
        summary = data["fuel_summary"]
        used_after_first_station = (ROUTE_MILES - data["fuel_stops"][0]["mile_marker"]) / 10
        self.assertAlmostEqual(
            summary["total_gallons_purchased"] - summary["fuel_left_at_destination_gallons"],
            used_after_first_station, delta=0.3,
        )
        # Just enough at $3.90 to reach the $2.95 station, then everything else there.
        self.assertEqual([s["opis_id"] for s in data["fuel_stops"]], [1, 2])
        self.assertAlmostEqual(data["fuel_stops"][0]["gallons_purchased"], 10.6, delta=0.2)

    @mock.patch.object(routing, "get_route", return_value=FAKE_ROUTE)
    def test_route_is_cached_between_requests(self, get_route):
        self.get()
        second = self.get(start_tank="empty").json()
        self.client.get("/api/route/map/", {"start": "40,-100", "finish": "40,-88"})
        self.assertEqual(get_route.call_count, 1)
        self.assertTrue(second["meta"]["route_cached"])
        self.assertEqual(second["meta"]["routing_api_calls"], 0)

    @mock.patch.object(routing, "get_route", return_value=FAKE_ROUTE)
    def test_post_json(self, get_route):
        resp = self.client.post(
            "/api/route/", {"start": "40,-100", "finish": "40,-88"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("fuel_stops", resp.json())

    @mock.patch.object(routing, "get_route", return_value=FAKE_ROUTE)
    def test_gap_longer_than_range_returns_422(self, get_route):
        FuelStation.objects.exclude(opis_id__in=[1]).delete()
        stations.reset_index()
        resp = self.get()
        self.assertEqual(resp.status_code, 422)
        self.assertIn("gap", resp.json())

    def test_validation_errors(self):
        self.assertEqual(self.client.get("/api/route/", {"start": "40,-100"}).status_code, 400)
        self.assertEqual(self.get(start_tank="half").status_code, 400)
        self.assertEqual(self.get(stop_penalty=-1).status_code, 400)
        resp = self.client.get("/api/route/", {"start": "48.85,2.35", "finish": "40,-88"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("outside the USA", resp.json()["error"])

    @mock.patch.object(routing, "get_route", side_effect=RoutingError("down", status=502))
    def test_routing_failure_returns_502(self, get_route):
        self.assertEqual(self.get().status_code, 502)

    @mock.patch.object(routing, "get_route", return_value=FAKE_ROUTE)
    def test_map_page(self, get_route):
        resp = self.client.get("/api/route/map/", {"start": "40,-100", "finish": "40,-88"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "leaflet")
        self.assertContains(resp, 'id="plan-data"')


class OsrmClientTests(TestCase):
    def test_parses_osrm_response(self):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {
            "code": "Ok",
            "routes": [{"distance": 160934.4, "duration": 7200,
                        "geometry": {"coordinates": [[-100, 40], [-98, 40]]}}],
        }
        start = mock.Mock(lat=40.0, lng=-100.0)
        finish = mock.Mock(lat=40.0, lng=-98.0)
        with mock.patch.object(routing.requests, "get", return_value=fake) as get:
            route = routing.get_route(start, finish)
        get.assert_called_once()
        self.assertIn("-100.000000,40.000000;-98.000000,40.000000", get.call_args.args[0])
        self.assertAlmostEqual(route.distance_miles, 100.0)
        self.assertAlmostEqual(route.duration_hours, 2.0)

    def test_no_route(self):
        fake = mock.Mock(status_code=400)
        fake.json.return_value = {"code": "NoRoute", "message": "Impossible route"}
        with mock.patch.object(routing.requests, "get", return_value=fake):
            with self.assertRaises(RoutingError) as ctx:
                routing.get_route(mock.Mock(lat=1, lng=1), mock.Mock(lat=2, lng=2))
        self.assertEqual(ctx.exception.status, 422)


class StationsAlongRouteTests(TestCase):
    def test_corridor_and_mile_markers(self):
        make_station(10, -99.0, "3.00")                 # on route
        make_station(11, -95.0, "3.00", lat=40.1)       # ~7 mi off
        make_station(12, -95.0, "3.00", lat=40.5)       # ~35 mi off
        make_station(13, -80.0, "3.00")                 # beyond the end
        idx, miles, offsets = stations.stations_along_route(
            ROUTE_COORDS, corridor_miles=10, index=stations._build_index()
        )
        self.assertEqual(len(idx), 2)
        self.assertTrue(np.all(np.diff(miles) >= 0))
        self.assertAlmostEqual(miles[0], 53, delta=1)
        self.assertAlmostEqual(offsets[1], 6.9, delta=0.3)


class LoadStationsCommandTests(TestCase):
    def test_loads_bundled_csv(self):
        call_command("load_stations", stdout=mock.Mock())
        self.assertGreater(FuelStation.objects.count(), 6000)
        # Duplicate OPIS ids are merged, and nothing outside the US is kept.
        self.assertFalse(FuelStation.objects.filter(state__in=["ON", "BC", "AB"]).exists())
        pilot = FuelStation.objects.get(opis_id=20)
        self.assertAlmostEqual(pilot.latitude, 32.95, delta=0.3)  # Gila Bend, AZ
