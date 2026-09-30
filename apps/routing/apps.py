from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class RoutingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.routing"
    verbose_name = "Route & Fuel Optimization"

    def ready(self) -> None:
        from django.conf import settings

        if not getattr(settings, "ORS_API_KEY", "").strip():
            raise ImproperlyConfigured(
                "ORS_API_KEY is missing or empty. A valid OpenRouteService API key is required."
            )
