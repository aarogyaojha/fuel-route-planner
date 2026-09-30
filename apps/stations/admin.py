from django.contrib import admin

from apps.stations.models import Station


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "retail_price", "opis_id", "rack_id")
    search_fields = ("name", "city", "state", "opis_id")
    list_filter = ("state",)
