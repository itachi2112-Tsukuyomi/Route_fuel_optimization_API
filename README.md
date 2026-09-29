# Route Fuel Optimization API

A Django REST API that takes a start and finish location in the USA and returns:

- the driving route (GeoJSON) and an interactive map,
- the most cost-effective places to buy fuel along the way, given a **500-mile range**,
- the **total fuel cost** at **10 miles per gallon**, using the prices in `data/fuel-prices.csv`.

It makes **one** call to the free routing API per new route, and none for repeats (they're cached). Everything else, including geocoding of city, ZIP and coordinate input, runs offline and in memory.

```
GET /api/route/?start=New York, NY&finish=Los Angeles, CA
```

---

## Quick start

Requires **Python 3.12+** (Django 6.1).

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations      # imports + geocodes the fuel price CSV (~3 s, no network)
python manage.py runserver
```

Then open:

| URL | What |
|---|---|
| `http://127.0.0.1:8000/api/route/?start=Chicago, IL&finish=Denver, CO` | JSON plan (browsable API while `DEBUG`) |
| `http://127.0.0.1:8000/api/route/map/?start=Chicago, IL&finish=Denver, CO` | Interactive map |
| `http://127.0.0.1:8000/` | Map with an example route |

A Postman collection is in [`postman/route_fuel_api.postman_collection.json`](postman/route_fuel_api.postman_collection.json).

Run the tests:

```bash
python manage.py test fuel
```

---

## API

### `GET /api/route/` or `POST /api/route/`

| Parameter | Required | Description |
|---|---|---|
| `start` | yes | `"City, ST"` (also `"City, State"`), a 5-digit ZIP code, or `"lat,lng"` |
| `finish` | yes | same formats |
| `start_tank` | no | `full` (default): the trip starts with a full tank. `empty`: only enough fuel to reach the first station, so the total covers the whole trip's fuel |
| `stop_penalty` | no | dollars charged per stop when optimising (default `5`, allowed `0`–`1000`). `0` = strictly the lowest fuel bill, however many stops |

POST takes the same fields as a JSON body:

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "finish": "Houston, TX"}'
```

### Response (abbreviated)

Values below are from a live Chicago → San Francisco request against the public OSRM server; they can shift slightly when OSRM's road data changes.

```jsonc
{
  "start":  {"label": "Chicago, IL", "lat": 41.85933, "lng": -87.68213},
  "finish": {"label": "San Francisco, CA", "lat": 37.7648, "lng": -122.42665},
  "route": {
    "distance_miles": 2133.1,
    "duration_hours": 36.88,
    "geometry": {"type": "LineString", "coordinates": [[-87.68213, 41.85933], "..."]}
  },
  "vehicle": {"max_range_miles": 500.0, "miles_per_gallon": 10.0, "tank_size_gallons": 50.0,
              "start_tank": "full", "start_fuel_gallons": 50.0},
  "optimizer": {"stop_penalty_dollars": 5.0},
  "fuel_summary": {
    "total_fuel_cost": 501.23,            // money spent at the fuel stops
    "total_gallons_purchased": 163.31,
    "trip_fuel_used_gallons": 213.31,     // distance / mpg (50 gal came from the starting tank)
    "fuel_left_at_destination_gallons": 0.0,
    "average_price_paid": 3.069,
    "number_of_stops": 5,
    "stations_considered": 262            // stations found along the route
  },
  "fuel_stops": [
    // ...
    {
      "stop": 2, "opis_id": 68368, "name": "AKAL TRAVEL CENTER",
      "address": "I-80 EX 360", "city": "Waco", "state": "NE",
      "price_per_gallon": 2.799, "lat": 40.9198, "lng": -97.4534,
      "mile_marker": 556.5, "distance_from_route_miles": 6.8,
      "fuel_in_tank_on_arrival_gallons": 0.0,
      "gallons_purchased": 50.0, "cost": 139.95
    }
    // ...
  ],
  "meta": {
    "routing_api_calls": 1,               // 0 when the route came from the cache
    "geocoding_api_calls": 0,
    "route_cached": false,
    "timings_ms": {"geocode_ms": 0.1, "routing_ms": 1907.4, "optimize_ms": 27.8, "total_ms": 1935.3}
  },
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=Chicago%2C+IL&finish=San+Francisco%2C+CA&start_tank=full&stop_penalty=5.0"
}
```

Errors return JSON with an `error` (or `errors` for validation) field:

| Status | When |
|---|---|
| `400` | missing or invalid input, a location that can't be found, or one outside the USA |
| `422` | no drivable route, or a stretch longer than 500 miles with no fuel station (the response includes the `gap`) |
| `502` / `504` | the routing service failed or timed out |
| `503` | no fuel stations loaded yet (run `python manage.py load_stations`) |

### `GET /api/route/map/`

Same parameters. Returns an HTML page (Leaflet + OpenStreetMap tiles, no API key) with the route, numbered fuel stops and a cost summary. It reuses the cached route, so opening the map after the API call makes no extra routing request.

---

## How it works

```
start, finish ──► offline geocoder ──► OSRM route (1 HTTP call, cached)
                                             │
             in-memory station index ◄───────┘  KD-tree: stations within 10 mi of the route,
                                             │  each with its mile marker
                                             ▼
                              fuel optimiser (dynamic programming)
                                             ▼
                        stops + gallons + cost + GeoJSON + map URL
```

### 1. Station coordinates, with no API

The CSV has no coordinates, only addresses like `I-44, EXIT 283 & US-69` plus a city and state. Geocoding 6,700 addresses through a free API would be slow and rate-limited. Instead, `load_stations` places each station at the centre of its city, using the ZIP code dataset bundled with the [`zipcodes`](https://pypi.org/project/zipcodes/) package:

- 8,151 rows → 6,738 unique stations (duplicate OPIS ids are merged, keeping the lowest price).
- 6,626 US stations are geocoded; **100%** of US cities match. 112 Canadian stations are skipped because the route is US-only.

### 2. Start and finish, also offline

The same index resolves `"City, ST"` and ZIP codes from memory. Only input it can't resolve falls back to Nominatim (OpenStreetMap), which is one extra call and is reported in `meta.geocoding_api_calls`.

### 3. One routing call

[OSRM](https://project-osrm.org/)'s free public server (`router.project-osrm.org`, no key) returns the full road geometry and distance in a single request. Results are cached per start/finish pair, so repeat requests and the map page make no routing calls.

### 4. Stations along the route

The route is resampled every 0.5 miles and put in a `scipy` KD-tree (on 3-D unit-sphere points, so distances are correct at every latitude). One vectorised query finds every station within `CORRIDOR_MILES` (10) of the road and its **mile marker**, which is the distance along the route to its nearest point. This takes a few milliseconds even for coast-to-coast trips.

### 5. Choosing the stops

With stations sorted by mile marker, this is the classic *gas station problem*. The optimiser is a **dynamic program over (station, fuel in tank)** that minimises:

```
fuel cost  +  stop_penalty × number_of_stops
```

- Fuel is tracked in 1-mile (0.1-gallon) steps.
- At each station, buying up to level `g` costs `g·p + penalty + min_{f<g}(cost[f] − f·p)`, which is a running prefix minimum. So each station is one O(tank-levels) numpy pass: about 2 ms per 100 stations.
- A backwards pass recovers where to stop. A forward replay with the exact distances then computes the gallons at each stop, so the tank never overflows and always reaches the next stop.

**Why a stop penalty?** The strictly cheapest plan often pulls off the highway to buy 1 gallon and save 3 cents. On the live Chicago → San Francisco route (2,133 miles, mostly I-80):

| `stop_penalty` | fuel cost | stops |
|---|---|---|
| 0 | $498.39 | 10 |
| **5 (default)** | **$501.23** | **5** |
| 10 | $509.20 | 4 |

`stop_penalty=0` gives the exact minimum fuel bill. The code also includes the textbook greedy algorithm for that case (`plan_fuel_stops_greedy`: *if a cheaper station is within range, buy just enough to reach it; otherwise fill up*). The tests check the DP against it on 200 random routes.

### Performance

- **Stations:** loaded into numpy arrays once per process, and warmed up in a background thread when the server starts. Requests never query the database.
- **New route:** response time ≈ OSRM latency + ~15–50 ms of our own work (measured on a coast-to-coast route). The public OSRM server usually answers a cross-country route in 1–3 s.
- **Repeated route:** ~15–30 ms, zero external calls.

---

## Assumptions and limitations

- **Vehicle:** 500-mile range (50-gallon tank), 10 mpg. Both are configurable in `settings.FUEL_PLANNER`.
- **Starting fuel:** by default the vehicle starts with a full tank, and `total_fuel_cost` is what's spent at stops along the way. Use `start_tank=empty` for a bill covering the whole trip's fuel.
- **Station positions:** stations are placed at their city's centre, not the exact exit. That's why the corridor is 10 miles, and why `distance_from_route_miles` is reported. The detour to a station isn't added to the route distance.
- **Prices:** taken from the CSV; the lowest price is used for duplicate station rows.
- **Routing server:** the public OSRM server is a free demo with fair-use limits. Point `OSRM_BASE_URL` at your own OSRM instance for production.
- **Cache:** `LocMemCache` is per process. Use Redis or Memcached with multiple workers.

## Configuration

`config/settings.py` → `FUEL_PLANNER`:

| Key | Default | |
|---|---|---|
| `MAX_RANGE_MILES` | `500` | vehicle range |
| `MILES_PER_GALLON` | `10` | fuel economy |
| `STOP_PENALTY_DOLLARS` | `5` | default cost per stop |
| `CORRIDOR_MILES` | `10` | max distance from the route for a station to count |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | env `OSRM_BASE_URL` |
| `OSRM_TIMEOUT_SECONDS` | `20` | routing request timeout (`504` after this) |
| `NOMINATIM_ENABLED` | `True` | env `NOMINATIM_ENABLED=0` to disable the fallback |
| `ROUTE_CACHE_SECONDS` | `86400` | route cache lifetime |

## Project layout

```
config/                      Django project (settings, urls)
data/fuel-prices.csv         provided fuel price list
fuel/
  models.py                  FuelStation
  management/commands/
    load_stations.py         CSV -> DB, offline geocoding, de-duplication
  services/
    geocoding.py             "City, ST" / ZIP / lat,lng -> coordinates (offline, Nominatim fallback)
    routing.py               OSRM client (one call per route)
    stations.py              in-memory station index + KD-tree route matching
    optimizer.py             DP optimiser (+ exact greedy reference)
    planner.py               orchestration, caching, response shape
    geo.py                   haversine, resampling, polyline thinning
  views.py / urls.py         REST endpoint + HTML map
  templates/fuel/map.html    Leaflet map
  tests/                     optimiser, geocoder, API, station matching, loader
postman/                     Postman collection
```
