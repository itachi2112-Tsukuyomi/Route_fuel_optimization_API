from django.db import models


class FuelStation(models.Model):
    """A truck stop from the fuel price list, geocoded to its city centre."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=4)
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
