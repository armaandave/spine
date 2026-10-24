from django.test import SimpleTestCase

from config.cache_settings import cache_settings

DUMMY = "django.core.cache.backends.dummy.DummyCache"
REDIS = "django_redis.cache.RedisCache"


def build(*, debug, dev_cache=""):
    return cache_settings(
        debug=debug,
        dev_cache=dev_cache,
        redis_url="redis://localhost:6379/5",
        key_prefix="local-test:",
        timeout=86400,
    )["default"]


class CacheSettingsTests(SimpleTestCase):
    """DEV_CACHE=redis turns the Redis cache on for debug servers."""

    def test_debug_alone_uses_no_cache(self):
        self.assertEqual(build(debug=True), {"BACKEND": DUMMY})

    def test_debug_with_dev_cache_redis_uses_redis(self):
        cache = build(debug=True, dev_cache="redis")

        self.assertEqual(cache["BACKEND"], REDIS)
        self.assertEqual(cache["LOCATION"], "redis://localhost:6379/5")
        self.assertEqual(cache["KEY_PREFIX"], "local-test:")
        self.assertEqual(cache["TIMEOUT"], 86400)

    def test_without_debug_always_uses_redis(self):
        for dev_cache in ("", "redis", "none"):
            with self.subTest(dev_cache=dev_cache):
                self.assertEqual(build(debug=False, dev_cache=dev_cache)["BACKEND"], REDIS)
