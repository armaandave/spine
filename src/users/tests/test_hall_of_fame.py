from importlib import import_module

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from app.models import Item, MediaTypes, Sources
from users import hall_of_fame

crown_order = import_module("users.migrations.0063_fill_hall_of_fame_picks").crown_order
ALL = {"movie", "tv", "anime", "manga", "game", "book", "comic", "music"}


class CrownOrderTests(SimpleTestCase):
    def test_music_sits_in_the_middle_of_the_visible_picks(self):
        # n = 4 visible picks: music at index 2.
        self.assertEqual(
            crown_order({"movie", "tv", "book", "music"}, ALL),
            ["movie", "tv", "music", "book"],
        )
        # n = 3: index 1.
        self.assertEqual(crown_order({"movie", "tv", "music"}, ALL), ["movie", "music", "tv"])
        self.assertEqual(crown_order({"music"}, ALL), ["music"])

    def test_without_music_the_order_is_rank_order(self):
        self.assertEqual(crown_order({"book", "movie", "game"}, ALL), ["movie", "game", "book"])

    def test_turned_off_types_follow_the_visible_ones(self):
        self.assertEqual(
            crown_order({"movie", "tv", "book", "music"}, ALL - {"tv", "music"}),
            ["movie", "book", "tv", "music"],
        )


class ServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="crown")

    def item(self, media_type, media_id):
        return Item.objects.create(
            source=Sources.TMDB.value,
            media_type=media_type,
            media_id=media_id,
            title=media_id,
        )

    def test_set_picks_syncs_the_first_pick_of_each_type_to_the_old_fields(self):
        first = self.item(MediaTypes.MOVIE.value, "a")
        show = self.item(MediaTypes.TV.value, "b")
        second = self.item(MediaTypes.MOVIE.value, "c")

        hall_of_fame.set_picks(self.user, [first, show, second])
        self.user.refresh_from_db()
        self.assertEqual((self.user.hof_movie, self.user.hof_tv, self.user.hof_book), (first, show, None))

        hall_of_fame.set_picks(self.user, [second])
        self.user.refresh_from_db()
        self.assertEqual((self.user.hof_movie, self.user.hof_tv), (second, None))
