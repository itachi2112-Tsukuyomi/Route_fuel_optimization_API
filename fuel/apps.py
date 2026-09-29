import sys
import threading

from django.apps import AppConfig


class FuelConfig(AppConfig):
    name = "fuel"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Warm the in-memory geocoder and station index when serving requests,
        # so the first API call doesn't pay the ~2 s start-up cost.
        serving = "runserver" in sys.argv or not sys.argv[0].endswith("manage.py")
        if serving and "test" not in sys.argv:
            threading.Thread(target=_warm_up, daemon=True).start()


def _warm_up():
    from django.db import OperationalError, ProgrammingError

    from .services.geocoding import _indexes
    from .services.stations import get_index

    _indexes()
    try:
        get_index()
    except (OperationalError, ProgrammingError):
        pass  # migrations not applied yet
