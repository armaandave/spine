from fakeredis import FakeRedisConnection

from .settings import *  # noqa: F403
from .test_network import block_outside_connections

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,  # noqa: F405
        "TIMEOUT": 18000,  # 5 hours
        "OPTIONS": {
            "CONNECTION_POOL_KWARGS": {"connection_class": FakeRedisConnection},
        },
    },
}

CELERY_TASK_ALWAYS_EAGER = True

TESTING = True
SERVER_TIMING = False
MUSIC_ENABLED = True
POSTER_PALETTE_COUNTING = False
EXTERNAL_RATING_PERSON_PREPARATION_ENABLED = True
MUSICBRAINZ_EXTERNAL_RATINGS_ENABLED = True
LISTENBRAINZ_TOKEN = ""
APPLE_MUSIC_TEAM_ID = ""
APPLE_MUSIC_KEY_ID = ""
APPLE_MUSIC_PRIVATE_KEY = ""
# Tests follow the code's default catalog, never .env.
MUSIC_CATALOG_SOURCE = MUSIC_CATALOG_DEFAULT  # noqa: F405

# Steam API key for testing
STEAM_API_KEY = "test_steam_api_key"
STEAMGRIDDB_API_KEY = "test_steamgriddb_api_key"

# Tests never reach the internet: an unmocked provider call fails at once, naming the host.
block_outside_connections()
