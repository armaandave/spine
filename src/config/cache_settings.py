def cache_settings(*, debug, dev_cache, redis_url, key_prefix, timeout):
    """Return CACHES: Redis, or DummyCache for DEBUG unless DEV_CACHE=redis."""
    if debug and dev_cache != "redis":
        return {
            "default": {
                "BACKEND": "django.core.cache.backends.dummy.DummyCache",
            },
        }
    return {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": redis_url,
            "TIMEOUT": timeout,
            "VERSION": 16,
            "KEY_PREFIX": key_prefix,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        },
    }
