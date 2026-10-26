from fakeredis import FakeRedisConnection

from .settings import *  # noqa: F403

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
EXTERNAL_RATING_PERSON_PREPARATION_ENABLED = True
MUSICBRAINZ_EXTERNAL_RATINGS_ENABLED = True
LISTENBRAINZ_TOKEN = ""
APPLE_MUSIC_TEAM_ID = ""
APPLE_MUSIC_KEY_ID = ""
APPLE_MUSIC_PRIVATE_KEY = ""

# Steam API key for testing
STEAM_API_KEY = "test_steam_api_key"
STEAMGRIDDB_API_KEY = "test_steamgriddb_api_key"
