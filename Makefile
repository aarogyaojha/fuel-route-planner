PYTHON ?= python
PIP ?= pip

.PHONY: help install migrate load-data run test lint

help:
	@echo "Usage:"
	@echo "  make install    Install Python dependencies"
	@echo "  make migrate    Run database migrations"
	@echo "  make load-data  Import stations CSV and run offline geocoding"
	@echo "  make run        Start Django development server"
	@echo "  make test       Run pytest test suite"
	@echo "  make lint       Run ruff linter and format checker"

install:
	$(PIP) install -r requirements.txt

migrate:
	$(PYTHON) manage.py migrate

load-data:
	$(PYTHON) manage.py import_stations
	$(PYTHON) manage.py geocode_stations

run:
	$(PYTHON) manage.py runserver

test:
	pytest

lint:
	ruff check .
	ruff format --check .
