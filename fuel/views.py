from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.geocoding import GeocodingError
from .services.optimizer import NoFeasiblePlan
from .services.planner import plan_trip
from .services.routing import RoutingError


def _plan_or_error(params):
    """Validate input and plan the trip. Returns (data, status_code)."""
    if not hasattr(params, "items"):
        return {"error": "Expected a JSON object."}, status.HTTP_400_BAD_REQUEST
    # Blank form fields mean "use the default".
    params = {k: v for k, v in params.items() if v not in ("", None)}
    serializer = RouteRequestSerializer(data=params)
    if not serializer.is_valid():
        return {"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST
    try:
        return plan_trip(**serializer.validated_data), status.HTTP_200_OK
    except GeocodingError as exc:
        return {"error": str(exc)}, status.HTTP_400_BAD_REQUEST
    except NoFeasiblePlan as exc:
        return {
            "error": str(exc),
            "gap": {"from_mile": round(exc.from_mile, 1), "to_mile": round(exc.to_mile, 1)},
        }, status.HTTP_422_UNPROCESSABLE_ENTITY
    except RoutingError as exc:
        return {"error": str(exc)}, exc.status


class RouteFuelPlanView(APIView):
    """
    Plan a road trip with the cheapest fuel stops.

    GET  /api/route/?start=Chicago, IL&finish=Denver, CO[&start_tank=full|empty]
    POST /api/route/  {"start": "Chicago, IL", "finish": "Denver, CO"}
    """

    def get(self, request):
        return self._respond(request, request.query_params)

    def post(self, request):
        return self._respond(request, request.data)

    def _respond(self, request, params):
        data, code = _plan_or_error(params)
        if code == status.HTTP_200_OK:
            query = urlencode({
                "start": params.get("start"),
                "finish": params.get("finish"),
                "start_tank": data["vehicle"]["start_tank"],
                "stop_penalty": data["optimizer"]["stop_penalty_dollars"],
            })
            data["map_url"] = request.build_absolute_uri(f"{reverse('route-map')}?{query}")
        return Response(data, status=code)


def route_map(request):
    """Interactive Leaflet map of the route and fuel stops (reuses the cached route)."""
    data, code = _plan_or_error(request.GET)
    return render(request, "fuel/map.html", {"plan": data, "ok": code == 200}, status=code)
