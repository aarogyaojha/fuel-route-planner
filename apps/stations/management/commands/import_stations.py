from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from apps.stations.services import import_stations_from_csv


class Command(BaseCommand):
    help = "Import fuel stations from an OPIS CSV file."

    def add_arguments(self, parser: CommandParser) -> None:
        default_csv = settings.BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"
        parser.add_argument(
            "csv_path",
            type=str,
            nargs="?",
            default=str(default_csv),
            help=f"Path to fuel stations CSV file (default: {default_csv})",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of records to insert per batch (default: 1000)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        csv_path = Path(options["csv_path"])
        if not csv_path.is_absolute():
            csv_path = settings.BASE_DIR / csv_path
        if not csv_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {csv_path}"))
            return

        batch_size = options["batch_size"]
        self.stdout.write(f"Importing stations from {csv_path}...")
        stats = import_stations_from_csv(csv_path, batch_size=batch_size)

        self.stdout.write(f"Rows read: {stats.rows_read}")
        self.stdout.write(f"Non-US dropped: {stats.non_us_dropped}")
        self.stdout.write(f"Duplicates dropped: {stats.duplicates_dropped}")
        self.stdout.write(self.style.SUCCESS(f"Rows saved: {stats.rows_saved}"))
