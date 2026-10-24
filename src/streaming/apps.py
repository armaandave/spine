from django.apps import AppConfig


class StreamingConfig(AppConfig):
    """Streaming availability (where to watch) for movies and TV."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "streaming"
