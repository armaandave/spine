"""Another user's diary, tags, likes, lists and tracking, read-only.

/users/<username>/diary/, diary/tags/, liked-media/, lists/ and tracking/: each answers
in the shape of the signed-in user's own endpoint and holds only what the viewer may
see: followers-only logs need an accepted follow, private logs and lists stay the
owner's, and a private account or a block either way is a 404.
"""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.tests.test_tv_tracking_api import TVAPITestCase
from app.models import (
    Anime,
    DiaryEntry,
    Item,
    MediaLike,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from app.services import update_diary_entry_tags
from app.tests.tv_fixtures import make_user
from lists.models import CustomList
from social.models import Block, Follow, FollowStatus

ENDPOINTS = (
    ("diary/", {}),
    ("diary/tags/", {"all": "true"}),
    ("liked-media/", {}),
    ("lists/", {"list_type": "all"}),
    ("tracking/", {"media_type": MediaTypes.ANIME.value}),
)


class UserContentTestCase(TestCase):
    """An owner, saiuri, and a viewer, armaan, who isn't following her yet."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="saiuri", password="strong-password-123")
        self.viewer = User.objects.create_user(username="armaan", password="strong-password-123")
        self.client = APIClient()

    def item(self, media_type=MediaTypes.ANIME.value, media_id="one"):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-{media_id}",
            title=f"{media_type} {media_id}",
            image="https://example.com/poster.jpg",
        )

    def log(self, item, day=1, user=None, tags=(), **fields):
        entry = DiaryEntry.objects.create(
            user=user or self.owner,
            item=item,
            consumed_at=datetime(2026, 10, day, 12, tzinfo=UTC),
            **fields,
        )
        if tags:
            update_diary_entry_tags(entry, list(tags))
        return entry

    def follow(self, follow_status=FollowStatus.ACCEPTED):
        Follow.objects.update_or_create(
            from_user=self.viewer,
            to_user=self.owner,
            defaults={"status": follow_status},
        )

    def make_private(self):
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])

    def get(self, path, viewer=None, **params):
        self.client.force_authenticate(viewer or self.viewer)
        return self.client.get(f"/api/v1/{path}", params)

    def theirs(self, path, viewer=None, **params):
        return self.get(f"users/saiuri/{path}", viewer, **params)

    def ok(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK, getattr(response, "data", None))
        return response.data

    def ids(self, data):
        return [row["id"] for row in data["results"]]

    def assert_same_page(self, theirs, mine):
        """Assert the same payload, whose page links point at each endpoint's path."""
        links = {key: theirs[key] and theirs[key].replace("/users/saiuri/", "/") for key in ("next", "previous")}
        self.assertEqual({**theirs, **links}, dict(mine))


class ProfileAccessTests(UserContentTestCase):
    """Every endpoint is a 404 where the profile is, and only reads."""

    def statuses(self):
        return {path: self.theirs(path, **params).status_code for path, params in ENDPOINTS}

    def assert_every_endpoint(self, expected):
        self.assertEqual(self.statuses(), {path: expected for path, _ in ENDPOINTS})

    def test_a_public_profile_is_readable(self):
        self.assert_every_endpoint(status.HTTP_200_OK)

    def test_a_private_account_is_404_until_the_follow_is_accepted(self):
        self.make_private()
        self.assert_every_endpoint(status.HTTP_404_NOT_FOUND)
        self.follow(FollowStatus.PENDING)
        self.assert_every_endpoint(status.HTTP_404_NOT_FOUND)
        self.follow()
        self.assert_every_endpoint(status.HTTP_200_OK)

    def test_a_block_either_way_is_404_even_for_a_follower(self):
        self.follow()
        Block.objects.create(blocker=self.owner, blocked=self.viewer)
        self.assert_every_endpoint(status.HTTP_404_NOT_FOUND)
        Block.objects.all().delete()
        Block.objects.create(blocker=self.viewer, blocked=self.owner)
        self.assert_every_endpoint(status.HTTP_404_NOT_FOUND)

    def test_an_unknown_user_is_404(self):
        for path, params in ENDPOINTS:
            with self.subTest(path=path):
                response = self.get(f"users/nobody/{path}", **params)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_they_are_read_only(self):
        self.client.force_authenticate(self.viewer)
        for path, _params in ENDPOINTS:
            with self.subTest(path=path):
                response = self.client.post(f"/api/v1/users/saiuri/{path}", {}, format="json")
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_signing_in_is_required(self):
        self.client.force_authenticate(None)
        for path, params in ENDPOINTS:
            with self.subTest(path=path):
                response = self.client.get(f"/api/v1/users/saiuri/{path}", params)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class UserDiaryTests(UserContentTestCase):
    """/users/<username>/diary/ is /diary/ over the logs the viewer may see."""

    def test_the_owner_gets_exactly_their_own_diary(self):
        anime = self.item()
        self.log(anime, day=1, visibility="private", review="Mine only", tags=["secret"])
        self.log(anime, day=2, visibility="followers", liked=True)
        self.log(self.item(MediaTypes.MOVIE.value), day=3, rating=8, tags=["film"])

        for params in ({}, {"has_review": "true"}, {"tag": "film"}, {"page_size": "1", "page": "2"}):
            with self.subTest(params=params):
                self.assert_same_page(
                    self.ok(self.theirs("diary/", self.owner, **params)),
                    self.ok(self.get("diary/", self.owner, **params)),
                )

    def test_followers_only_logs_need_an_accepted_follow_and_private_ones_stay_hidden(self):
        anime = self.item()
        public = self.log(anime, day=1)
        followers_only = self.log(anime, day=2, visibility="followers")
        self.log(anime, day=3, visibility="private")
        # Half-star media logs are always public.
        movie = self.log(self.item(MediaTypes.MOVIE.value), day=4, visibility="followers")

        self.assertEqual(self.ids(self.ok(self.theirs("diary/"))), [movie.id, public.id])
        self.follow(FollowStatus.PENDING)
        self.assertEqual(self.ids(self.ok(self.theirs("diary/"))), [movie.id, public.id])
        self.follow()
        self.assertEqual(self.ids(self.ok(self.theirs("diary/"))), [movie.id, followers_only.id, public.id])
        self.assertEqual(self.ok(self.theirs("diary/"))["count"], 3)

    def test_rows_are_in_the_diary_shape_with_the_viewers_like(self):
        entry = self.log(self.item(), review="Great", tags=["cozy"])
        self.log(self.item(media_id="other"), user=self.viewer)
        mine = self.ok(self.get("diary/", self.viewer))
        self.client.force_authenticate(self.viewer)
        self.client.post(f"/api/v1/diary/{entry.id}/like/")

        data = self.ok(self.theirs("diary/"))

        self.assertEqual(data.keys(), mine.keys())
        row = data["results"][0]
        self.assertEqual(row.keys(), mine["results"][0].keys())
        self.assertEqual(row["user"]["username"], "saiuri")
        self.assertEqual((row["review"], row["tags"]), ("Great", ["cozy"]))
        self.assertEqual((row["like_count"], row["viewer_has_liked"]), (1, True))

    def test_reviews_tags_and_pages_filter_only_what_the_viewer_may_see(self):
        anime = self.item()
        reviewed = self.log(anime, day=1, review="Loved it", tags=["cozy"])
        titled = self.log(anime, day=2, review_title="A title", tags=["cozy"])
        self.log(anime, day=3, tags=["cozy"])
        self.log(anime, day=4, visibility="private", review="Hidden", tags=["cozy", "secret"])

        self.assertEqual(self.ids(self.ok(self.theirs("diary/", has_review="true"))), [titled.id, reviewed.id])
        self.assertEqual(self.ok(self.theirs("diary/", tag="cozy"))["count"], 3)
        self.assertEqual(self.ok(self.theirs("diary/", tag="secret"))["count"], 0)
        paged = self.ok(self.theirs("diary/", tag="cozy", page_size="2", page="2"))
        self.assertEqual((paged["count"], self.ids(paged)), (3, [reviewed.id]))
        self.assertIsNotNone(paged["previous"])

    def test_stats_drill_downs_count_as_the_viewers_stats_do(self):
        anime = self.item()
        self.log(anime, day=1, visibility="private")
        second = self.log(anime, day=2)

        # To the owner the second log is a repeat; others can't see the first.
        self.assertEqual(self.ids(self.ok(self.theirs("diary/", self.owner, repeat="true"))), [second.id])
        self.assertEqual(self.ids(self.ok(self.theirs("diary/", repeat="true"))), [])


class UserDiaryTagsTests(UserContentTestCase):
    """/users/<username>/diary/tags/ is /diary/tags/?mine=true over visible logs."""

    def test_tags_count_only_the_logs_the_viewer_may_see(self):
        anime = self.item()
        self.log(anime, day=1, tags=["shared", "open"])
        self.log(anime, day=2, visibility="followers", tags=["shared", "friends"])
        self.log(anime, day=3, visibility="private", tags=["secret"])
        self.log(self.item(media_id="viewer"), user=self.viewer, tags=["viewers own"])

        self.assertEqual(
            self.ok(self.theirs("diary/tags/", all="true"))["results"],
            [{"name": "open", "usage_count": 1}, {"name": "shared", "usage_count": 1}],
        )
        self.follow()
        self.assertEqual(
            self.ok(self.theirs("diary/tags/", all="true"))["results"],
            [
                {"name": "shared", "usage_count": 2},
                {"name": "friends", "usage_count": 1},
                {"name": "open", "usage_count": 1},
            ],
        )

    def test_the_owner_gets_their_own_tags_as_diary_tags_mine(self):
        anime = self.item()
        self.log(anime, day=1, tags=["shared", "open"])
        self.log(anime, day=2, visibility="private", tags=["shared", "secret"])

        theirs = self.ok(self.theirs("diary/tags/", self.owner, all="true"))

        self.assertEqual(theirs, self.ok(self.get("diary/tags/", self.owner, all="true", mine="true")))
        self.assertEqual(theirs["results"][0], {"name": "shared", "usage_count": 2})


class UserLikedMediaTests(UserContentTestCase):
    """/users/<username>/liked-media/ is /me/liked-media/ for the owner's likes."""

    def test_lists_the_owners_likes_in_the_me_shape(self):
        movie = self.item(MediaTypes.MOVIE.value)
        anime = self.item()
        MediaLike.objects.create(user=self.owner, item=movie)
        MediaLike.objects.create(user=self.owner, item=anime)
        MediaLike.objects.create(user=self.viewer, item=self.item(MediaTypes.MOVIE.value, "viewer"))

        data = self.ok(self.theirs("liked-media/"))
        mine = self.ok(self.get("me/liked-media/", self.owner))

        self.assertEqual([row["title"] for row in data["results"]], [anime.title, movie.title])
        self.assertEqual(self.ok(self.theirs("liked-media/", self.owner)), mine)
        self.assertEqual(data.keys(), mine.keys())
        self.assertEqual(data["results"][0].keys(), mine["results"][0].keys())
        # media.user_state is the viewer's own, who hasn't liked these.
        self.assertTrue(mine["results"][0]["user_state"]["has_liked"])
        self.assertFalse(data["results"][0]["user_state"]["has_liked"])
        movies = self.ok(self.theirs("liked-media/", media_type=MediaTypes.MOVIE.value))
        self.assertEqual([row["title"] for row in movies["results"]], [movie.title])


class UserListsTests(UserContentTestCase):
    """/users/<username>/lists/ is /lists/ for the owner's lists the viewer may see."""

    def make_list(self, name, visibility, **fields):
        return CustomList.objects.create(owner=self.owner, name=name, visibility=visibility, **fields)

    def test_others_see_public_lists_and_the_private_ones_they_help_edit(self):
        public = self.make_list("A public", CustomList.Visibility.PUBLIC)
        self.make_list("B unlisted", CustomList.Visibility.UNLISTED)
        self.make_list("C private", CustomList.Visibility.PRIVATE)
        shared = self.make_list("D shared", CustomList.Visibility.PRIVATE)
        shared.collaborators.add(self.viewer)
        CustomList.objects.create(owner=self.viewer, name="Viewer's", visibility=CustomList.Visibility.PUBLIC)

        data = self.ok(self.theirs("lists/", list_type="all"))

        self.assertEqual(self.ids(data), [public.id, shared.id])
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["results"][0]["owner"]["username"], "saiuri")

    def test_the_owner_gets_their_lists_in_the_lists_shape(self):
        self.make_list("A public", CustomList.Visibility.PUBLIC)
        self.make_list("B private", CustomList.Visibility.PRIVATE)
        self.make_list("C people", CustomList.Visibility.PUBLIC, list_type=CustomList.ListType.PEOPLE)

        for params in ({"list_type": "all"}, {}, {"list_type": "people"}, {"list_type": "all", "q": "private"}):
            with self.subTest(params=params):
                self.assertEqual(
                    self.ok(self.theirs("lists/", self.owner, **params)),
                    self.ok(self.get("lists/", self.owner, **params)),
                )


class UserTrackingTests(UserContentTestCase):
    """/users/<username>/tracking/ is /tracking/ with the owner's rows."""

    def track(self, model, item, user=None, **fields):
        return model.objects.bulk_create([model(user=user or self.owner, item=item, **fields)])[0]

    def test_rows_are_the_owners_tracking_not_the_viewers(self):
        anime = self.item()
        owner_row = self.track(Anime, anime, status=Status.IN_PROGRESS.value, progress=5, score=8, notes="Mine")
        self.track(Anime, anime, self.viewer, status=Status.PLANNING.value, progress=0)
        self.track(Anime, self.item(media_id="done"), status=Status.COMPLETED.value, progress=12)

        data = self.ok(self.theirs("tracking/", media_type="anime", status="In progress"))

        self.assertEqual(data["count"], 1)
        row = data["results"][0]
        self.assertEqual(row["tracking"]["tracking_id"], owner_row.id)
        self.assertEqual(
            (row["tracking"]["status"], row["tracking"]["progress"]["value"], row["tracking"]["rating"]),
            (Status.IN_PROGRESS.value, 5, "8.0"),
        )
        # user_state would be the owner's too, hidden logs and private lists included.
        self.assertIsNone(row["media"]["user_state"])
        # Notes stay the owner's.
        self.assertEqual(row["tracking"]["notes"], "")
        own = self.ok(self.theirs("tracking/", self.owner, media_type="anime", status="In progress"))
        self.assertEqual(own["results"][0]["tracking"]["notes"], "Mine")

    def test_half_star_rows_carry_the_owners_heart_and_log_count(self):
        liked_by_owner = self.item(MediaTypes.MOVIE.value, "owner-liked")
        liked_by_viewer = self.item(MediaTypes.MOVIE.value, "viewer-liked")
        for item in (liked_by_owner, liked_by_viewer):
            self.track(Movie, item, status=Status.COMPLETED.value)
        MediaLike.objects.create(user=self.owner, item=liked_by_owner)
        MediaLike.objects.create(user=self.viewer, item=liked_by_viewer)
        self.log(liked_by_owner, user=self.viewer)

        data = self.ok(self.theirs("tracking/", media_type="movie"))

        rows = {row["media"]["title"]: row["tracking"] for row in data["results"]}
        self.assertEqual(
            {title: (tracking["liked"], tracking["diary_count"]) for title, tracking in rows.items()},
            {liked_by_owner.title: (True, 0), liked_by_viewer.title: (False, 0)},
        )

    def test_the_owner_gets_exactly_their_own_tracking(self):
        self.track(Anime, self.item(), status=Status.IN_PROGRESS.value, progress=5)
        self.track(Anime, self.item(media_id="two"), status=Status.COMPLETED.value, progress=12)
        self.track(Movie, self.item(MediaTypes.MOVIE.value), status=Status.COMPLETED.value)

        for params in (
            {"media_type": "anime"},
            {"media_type": "anime", "status": "In progress"},
            {"media_type": "anime", "page_size": "1", "page": "2"},
            {"media_type": "movie"},
        ):
            with self.subTest(params=params):
                self.assert_same_page(
                    self.ok(self.theirs("tracking/", self.owner, **params)),
                    self.ok(self.get("tracking/", self.owner, **params)),
                )

    def test_media_type_is_required_as_on_tracking(self):
        response = self.theirs("tracking/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UserContinueWatchingTests(TVAPITestCase):
    """The In Progress rail's shows: another user's Continue Watching (TV-701)."""

    def test_another_users_continue_watching_lists_their_shows(self):
        show = self.make_show(title="Theirs")
        self.tick(show, 1, 1)
        viewer = make_user("tv-viewer")
        params = {"media_type": "tv", "continue_watching": "true"}
        path = f"/api/v1/users/{self.user.username}/tracking/"

        rows = self.client_for(viewer).get(path, params).data["results"]

        self.assertEqual(self.titles(rows), ["Theirs"])
        self.assertEqual(rows[0]["tracking"]["tv"]["up_next"]["episode_number"], 2)
        self.assertEqual(self.client_for(viewer).get("/api/v1/tracking/", params).data["results"], [])
        self.assertEqual(
            self.client_for(self.user).get(path, params).data,
            self.client_for(self.user).get("/api/v1/tracking/", params).data,
        )

    def titles(self, results):
        return [row["media"]["title"] for row in results]


class DiaryDetailVisibilityTests(UserContentTestCase):
    """GET /diary/<id>/ follows the diary list's visibility rule."""

    def detail(self, entry, viewer=None):
        return self.get(f"diary/{entry.id}/", viewer).status_code

    def test_a_followers_only_log_needs_an_accepted_follow(self):
        entry = self.log(self.item(), visibility="followers")

        self.assertEqual(self.detail(entry), status.HTTP_404_NOT_FOUND)
        self.follow(FollowStatus.PENDING)
        self.assertEqual(self.detail(entry), status.HTTP_404_NOT_FOUND)
        self.follow()
        self.assertEqual(self.detail(entry), status.HTTP_200_OK)
        self.assertEqual(self.detail(entry, self.owner), status.HTTP_200_OK)

    def test_private_logs_stay_the_owners_and_half_star_logs_are_public(self):
        self.follow()
        private = self.log(self.item(), visibility="private")
        movie = self.log(self.item(MediaTypes.MOVIE.value), visibility="private")

        self.assertEqual(self.detail(private), status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.detail(private, self.owner), status.HTTP_200_OK)
        self.assertEqual(self.detail(movie), status.HTTP_200_OK)
        Follow.objects.all().delete()
        self.assertEqual(self.detail(movie), status.HTTP_200_OK)

    def test_a_private_account_or_a_block_hides_even_public_logs(self):
        entry = self.log(self.item())
        self.make_private()
        self.assertEqual(self.detail(entry), status.HTTP_404_NOT_FOUND)
        self.follow()
        self.assertEqual(self.detail(entry), status.HTTP_200_OK)
        Block.objects.create(blocker=self.owner, blocked=self.viewer)
        self.assertEqual(self.detail(entry), status.HTTP_404_NOT_FOUND)
