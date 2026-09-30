from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.stations.services import geocode_all_stations


class Command(BaseCommand):
    help = "Fill station latitude/longitude coordinates offline using GeoNames."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--data-path",
            type=str,
            default=None,
            help="Path to cached GeoNames US.zip (defaults to data/US.zip)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for database bulk update",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        data_path = Path(options["data_path"]) if options["data_path"] else None
        if data_path and not data_path.is_absolute():
            from django.conf import settings

            data_path = settings.BASE_DIR / data_path

        self.stdout.write("Geocoding stations using GeoNames dataset...")
        stats = geocode_all_stations(zip_path=data_path, batch_size=options["batch_size"])

        self.stdout.write(self.style.SUCCESS(f"Matched stations: {stats.matched}"))
        self.stdout.write(f"Unmatched stations: {stats.unmatched}")

        if stats.unmatched_pairs:
            self.stdout.write("First 20 unmatched city/state pairs:")
            for city, state in stats.unmatched_pairs[:20]:
                self.stdout.write(f"  - {city}, {state}")
