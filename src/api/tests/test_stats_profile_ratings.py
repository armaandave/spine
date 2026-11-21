"""The profile's signature (docs/stats-contract.md STAT-313).

Its numbers are the Stats page's All Time Global ratings (STAT-204) for the same viewer,
and a profile the viewer can't see has none (STAT-002).
"""

from decimal import Decimal

from api.tests.test_stats_v2_activity import ActivityBase
from api.tests.test_stats_v2_shape import ALL_TIME
from app.models import MediaTypes

RATINGS_PATH = "/api/v1/stats/me/ratings/"


class ProfileRatingsTests(ActivityBase):
    """The signature's ratings and its gate."""

    def ratings(self, viewer=None):
        viewer = viewer or self.owner
        path = RATINGS_PATH if viewer == self.owner else self.user_path("ratings")
        response = self.get(viewer, {}, path=path)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_the_ratings_are_the_stats_pages_all_time_ratings_for_every_viewer(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.log(film, rating=Decimal("9.0"))
        # A rewatch is a rated log of its own, as on the Stats page.
        self.log(film, rating=Decimal("7.4"))
        self.log(self.item(MediaTypes.BOOK.value, "book"), rating=Decimal("6.0"))
        # A rated log only the owner sees (film and book logs are always public).
        self.log(self.item(MediaTypes.MANGA.value, "hidden"), rating=Decimal("3.0"), visibility="private")

        for viewer in (self.owner, self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                data = self.ratings(viewer)
                summary = self.summary(ALL_TIME, viewer=viewer)
                self.assertEqual(data["ratings"], summary["ratings"])
                self.assertEqual(data["range"], summary["range"])
                self.assertEqual(data["audience"], summary["audience"])

        self.assertEqual(self.ratings()["ratings"]["rated"], 4)
        self.assertEqual(self.ratings(self.stranger)["ratings"]["rated"], 3)

    def test_a_profile_the_viewer_cant_see_has_no_ratings(self):
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])
        self.assertEqual(self.get(self.stranger, {}, path=self.user_path("ratings")).status_code, 404)
