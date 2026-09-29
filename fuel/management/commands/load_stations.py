"""Load the fuel price CSV into the database, geocoding every station offline.

    python manage.py load_stations [--csv path/to/file.csv]

The CSV has no coordinates, only "I-44, EXIT 283 & US-69" style addresses plus
city/state. We place each station at its city's centroid using the bundled
ZIP code dataset, so this needs no network access and makes zero API calls.
Duplicate rows for the same OPIS id are collapsed, keeping the lowest price.
Stations outside the USA (the file includes some Canadian ones) are skipped.
"""

import csv
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuel.models import FuelStation
from fuel.services.geocoding import STATE_CODES, lookup_city


class Command(BaseCommand):
    help = "Import and geocode fuel stations from the fuel price CSV."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.FUEL_PLANNER["FUEL_PRICES_CSV"]))

    def handle(self, *args, **options):
        path = options["csv"]
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        except FileNotFoundError as exc:
            raise CommandError(f"CSV not found: {path}") from exc

        best = {}
        for row in rows:
            opis_id = int(row["OPIS Truckstop ID"])
            price = Decimal(row["Retail Price"].strip())
            if opis_id not in best or price < best[opis_id][0]:
                best[opis_id] = (price, row)

        stations, non_us, unmatched = [], 0, []
        for opis_id, (price, row) in best.items():
            city, state = row["City"].strip(), row["State"].strip().upper()
            if state not in STATE_CODES:
                non_us += 1
                continue
            point = lookup_city(city, state)
            if point is None:
                unmatched.append(f"{city}, {state}")
                continue
            stations.append(
                FuelStation(
                    opis_id=opis_id,
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=city,
                    state=state,
                    rack_id=int(row["Rack ID"]) if row["Rack ID"].strip() else None,
                    price=price,
                    latitude=point[0],
                    longitude=point[1],
                )
            )

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(stations)} stations from {len(rows)} rows "
                f"({len(rows) - len(best)} duplicate rows merged, "
                f"{non_us} outside the USA skipped, {len(unmatched)} not geocoded)."
            )
        )
        if unmatched:
            self.stdout.write("Not geocoded: " + "; ".join(sorted(set(unmatched))))
