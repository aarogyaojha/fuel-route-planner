from django.db import models


class Station(models.Model):
    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField()
    retail_price = models.DecimalField(max_digits=8, decimal_places=5)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["latitude", "longitude"], name="station_lat_lon_idx"),
            models.Index(fields=["state"], name="station_state_idx"),
        ]
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.name} - {self.city}, {self.state} (${self.retail_price})"
