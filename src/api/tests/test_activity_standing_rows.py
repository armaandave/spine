"""The viewer-independent view: the rows a post stands on, whoever looks (plan 5.8)."""

from django.test import override_settings

from api.services import activity_rows
from api.tests.test_activity_calc_books import BookCalcBase, d
from app import tv_tracking
from app.models import Item, MediaTypes
from app.tests.tv_fixtures import make_manual_show

TV = MediaTypes.TV.value


@override_settings(TIME_ZONE="America/New_York")
class StandingRowsTests(BookCalcBase):
    """A log only some viewers see moves rows for them, never for the standing view."""

    def setUp(self):
        super().setUp()
        self.show = make_manual_show(seasons={1: 2})
        Item.objects.filter(media_id=self.show.media_id, media_type=MediaTypes.EPISODE.value).update(runtime_minutes=45)
        self.season = Item.objects.get(media_id=self.show.media_id, media_type=MediaTypes.SEASON.value)
        with self.on(d(2026, 3, 1)):
            tv_tracking.tick(self.owner, self.show, 1, 1)

    def days(self, rows):
        return sorted(row.day for row in rows if row.kind == activity_rows.PROGRESS and row.bucket == TV)

    def log_before_the_tick(self, visibility):
        with self.on(d(2026, 3, 2)):
            tv_tracking.create_log(self.owner, self.season, consumed_at=d(2025, 12, 20), visibility=visibility)

    def test_a_followers_only_backdated_log_moves_the_rows_of_those_who_see_it(self):
        self.log_before_the_tick("followers")
        for viewer in (self.owner, self.follower):
            with self.subTest(viewer=viewer.username):
                rows = activity_rows.rows_for([self.owner], viewer, types=[TV]).rows
                self.assertEqual(self.days(rows), [])
        stranger = activity_rows.rows_for([self.owner], self.stranger, types=[TV]).rows
        self.assertEqual(self.days(stranger), [d(2026, 3, 1)])

    def assert_standing_keeps_the_tick(self, visibility):
        self.log_before_the_tick(visibility)
        rows = activity_rows.standing_rows([self.owner.pk])
        self.assertEqual(self.days(rows), [d(2026, 3, 1)])
        self.assertEqual({row.kind for row in rows}, {activity_rows.PROGRESS})

    def test_the_standing_view_keeps_the_tick_under_a_private_log(self):
        self.assert_standing_keeps_the_tick("private")

    def test_the_standing_view_keeps_the_tick_under_a_followers_log(self):
        self.assert_standing_keeps_the_tick("followers")

    def test_the_standing_view_keeps_the_tick_under_a_public_log(self):
        self.assert_standing_keeps_the_tick("public")

    def test_it_is_the_progress_of_every_person_asked_for(self):
        rows = activity_rows.standing_rows([self.owner.pk, self.follower.pk])
        self.assertEqual({row.actor_id for row in rows}, {self.owner.pk})
        self.assertEqual(activity_rows.standing_rows([self.follower.pk]), [])
