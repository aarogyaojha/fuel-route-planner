#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -o errexit

# 1. Install dependencies
pip install -r requirements.txt

# 2. Collect static files
python manage.py collectstatic --noinput

# 3. Apply database migrations
python manage.py migrate --noinput

# 4. Import fuel stations (idempotent, skips duplicates via unique opis_id)
python manage.py import_stations

# 5. Geocode stations with offline coordinates (idempotent bulk update)
python manage.py geocode_stations
