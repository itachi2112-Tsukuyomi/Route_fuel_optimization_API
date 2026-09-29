from rest_framework import serializers

from .services.planner import START_TANK_CHOICES


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200, help_text="'City, ST', 5-digit ZIP, or 'lat,lng' in the USA."
    )
    finish = serializers.CharField(max_length=200)
    start_tank = serializers.ChoiceField(
        choices=START_TANK_CHOICES,
        default="full",
        help_text="'full' (default): trip starts with a full tank. "
        "'empty': only enough fuel to reach the first station.",
    )
    stop_penalty = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=1000,
        help_text="Dollar cost per fuel stop used by the optimiser (default from "
        "settings). 0 = strictly the cheapest fuel bill, however many stops.",
    )
