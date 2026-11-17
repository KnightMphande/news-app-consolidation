"""Django apps configuration and entry points."""

from django.apps import AppConfig


class NewsConfig(AppConfig):
    """Register the news application and its user-role signal handlers."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "news"

    def ready(self):
        """Register signal handlers when Django loads the application."""
        from . import signals  # noqa: F401
