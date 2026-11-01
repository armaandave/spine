"""Following, follow requests, follower lists, user search, reports and pushes."""

import base64
import json
from datetime import UTC, datetime
from unittest import mock

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, MediaTypes, Sources
from lists.models import CustomList
from social import push
from social.models import (
    Block,
    Follow,
    FollowStatus,
    PushDevice,
    SocialAuditLog,
    UserReport,
)

KEY = ec.generate_private_key(ec.SECP256R1())
KEY_SETTING = base64.b64encode(
    KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ),
).decode()
PUSH_KEY = {"APNS_TEAM_ID": "TEAM123456", "APNS_KEY_ID": "KEY1234567", "APNS_PRIVATE_KEY": KEY_SETTING}
TOKEN = "ab" * 32


class SocialTestCase(TestCase):
    """Two users, signed in as the first."""

    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create_user(username="armaan", password="strong-password-123", display_name="Armaan")
        self.friend = User.objects.create_user(username="saiuri", password="strong-password-123", display_name="Sai")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def follow(self, follower, followed, follow_status=FollowStatus.ACCEPTED):
        return Follow.objects.create(from_user=follower, to_user=followed, status=follow_status)

    def make_private(self, user):
        user.profile_private = True
        user.save(update_fields=["profile_private"])


@mock.patch("api.services.social.push.notify")
class FollowTests(SocialTestCase):
    """Following, requests, removing followers and reports."""

    def test_following_a_public_account_follows_and_pushes_to_them(self, notify):
        response = self.client.post("/api/v1/users/saiuri/follow/")

        self.assertEqual(response.data, {"state": "following"})
        self.assertEqual(Follow.objects.get(from_user=self.me, to_user=self.friend).status, FollowStatus.ACCEPTED)
        notify.assert_called_once_with(
            self.friend,
            "Armaan started following you.",
            {"kind": "profile", "username": "armaan"},
        )

    def test_following_a_private_account_asks_and_pushes_a_request(self, notify):
        self.make_private(self.friend)

        response = self.client.post("/api/v1/users/saiuri/follow/")

        self.assertEqual(response.data, {"state": "requested"})
        self.assertEqual(Follow.objects.get(from_user=self.me, to_user=self.friend).status, FollowStatus.PENDING)
        notify.assert_called_once_with(self.friend, "Armaan wants to follow you.", {"kind": "follow_requests"})

    def test_following_again_keeps_an_accepted_follow_of_a_private_account(self, notify):
        self.make_private(self.friend)
        self.follow(self.me, self.friend)

        response = self.client.post("/api/v1/users/saiuri/follow/")

        self.assertEqual(response.data, {"state": "following"})
        self.assertEqual(Follow.objects.get(from_user=self.me, to_user=self.friend).status, FollowStatus.ACCEPTED)
        notify.assert_not_called()

    def test_asking_again_sends_no_second_request_push(self, notify):
        self.make_private(self.friend)
        self.follow(self.me, self.friend, FollowStatus.PENDING)

        self.client.post("/api/v1/users/saiuri/follow/")

        notify.assert_not_called()

    def test_a_request_to_an_account_that_went_public_becomes_a_follow(self, notify):
        self.follow(self.me, self.friend, FollowStatus.PENDING)

        response = self.client.post("/api/v1/users/saiuri/follow/")

        self.assertEqual(response.data, {"state": "following"})
        notify.assert_called_once()

    def test_you_cannot_follow_yourself_or_across_a_block(self, notify):
        self.assertEqual(self.client.post("/api/v1/users/armaan/follow/").status_code, status.HTTP_404_NOT_FOUND)
        Block.objects.create(blocker=self.friend, blocked=self.me)
        self.assertEqual(self.client.post("/api/v1/users/saiuri/follow/").status_code, status.HTTP_404_NOT_FOUND)
        Block.objects.all().delete()
        Block.objects.create(blocker=self.me, blocked=self.friend)
        self.assertEqual(self.client.post("/api/v1/users/saiuri/follow/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Follow.objects.exists())
        notify.assert_not_called()

    def test_unfollowing_takes_back_a_request(self, notify):
        self.follow(self.me, self.friend, FollowStatus.PENDING)

        response = self.client.delete("/api/v1/users/saiuri/follow/")

        self.assertEqual(response.data, {"state": "none"})
        self.assertFalse(Follow.objects.exists())

    def test_accepting_a_request_follows_and_pushes_to_the_requester(self, notify):
        self.make_private(self.me)
        request = self.follow(self.friend, self.me, FollowStatus.PENDING)

        listed = self.client.get("/api/v1/follow-requests/")
        response = self.client.post(f"/api/v1/follow-requests/{request.id}/accept/")

        self.assertEqual(listed.data["results"][0]["from_user"]["username"], "saiuri")
        self.assertIn("avatar_url", listed.data["results"][0]["from_user"])
        self.assertEqual(response.data, {"state": "following"})
        request.refresh_from_db()
        self.assertEqual(request.status, FollowStatus.ACCEPTED)
        notify.assert_called_once_with(
            self.friend,
            "Armaan accepted your follow request.",
            {"kind": "profile", "username": "armaan"},
        )
        self.assertTrue(SocialAuditLog.objects.filter(action="follow_request_accept", target_user=self.friend).exists())

    def test_declining_a_request_removes_it_quietly(self, notify):
        request = self.follow(self.friend, self.me, FollowStatus.PENDING)

        response = self.client.post(f"/api/v1/follow-requests/{request.id}/reject/")

        self.assertEqual(response.data, {"state": "rejected"})
        self.assertFalse(Follow.objects.exists())
        notify.assert_not_called()
        self.assertTrue(SocialAuditLog.objects.filter(action="follow_request_reject").exists())

    def test_someone_elses_request_or_an_unknown_answer_is_not_found(self, notify):
        other = get_user_model().objects.create_user(username="other", password="strong-password-123")
        theirs = self.follow(self.friend, other, FollowStatus.PENDING)
        mine = self.follow(self.friend, self.me, FollowStatus.PENDING)

        self.assertEqual(
            self.client.post(f"/api/v1/follow-requests/{theirs.id}/accept/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.post(f"/api/v1/follow-requests/{mine.id}/maybe/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_removing_a_follower_is_silent(self, notify):
        self.follow(self.friend, self.me)

        response = self.client.delete("/api/v1/me/followers/saiuri/")

        self.assertEqual(response.data, {"state": "removed"})
        self.assertFalse(Follow.objects.exists())
        notify.assert_not_called()
        self.assertTrue(SocialAuditLog.objects.filter(action="remove_follower", target_user=self.friend).exists())

    def test_reporting_pushes_to_the_owner_and_saves_the_report(self, notify):
        owner = get_user_model().objects.create_superuser(username="owner", password="strong-password-123")

        response = self.client.post("/api/v1/users/saiuri/report/")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(UserReport.objects.filter(reporter=self.me, reported=self.friend).exists())
        notify.assert_called_once_with(owner, "Armaan reported @saiuri.", {"kind": "profile", "username": "saiuri"})

    def test_you_cannot_report_yourself_or_someone_who_blocks_you(self, notify):
        self.assertEqual(self.client.post("/api/v1/users/armaan/report/").status_code, status.HTTP_404_NOT_FOUND)
        Block.objects.create(blocker=self.friend, blocked=self.me)
        self.assertEqual(self.client.post("/api/v1/users/saiuri/report/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(UserReport.objects.exists())


class FollowListTests(SocialTestCase):
    """A user's followers and the people they follow."""

    def test_followers_and_following_newest_first_with_your_connection(self):
        User = get_user_model()
        older = User.objects.create_user(username="older", password="strong-password-123")
        self.follow(older, self.friend)
        self.follow(self.me, self.friend)
        self.follow(self.friend, older)
        self.follow(self.me, older, FollowStatus.PENDING)
        self.follow(older, self.me)

        followers = self.client.get("/api/v1/users/saiuri/followers/")
        following = self.client.get("/api/v1/users/saiuri/following/")

        self.assertEqual([row["username"] for row in followers.data["results"]], ["armaan", "older"])
        self.assertEqual(followers.data["count"], 2)
        self.assertEqual(
            followers.data["results"][1]["viewer_relationship"],
            {"following": False, "requested": True, "followed_by": True},
        )
        self.assertEqual([row["username"] for row in following.data["results"]], ["older"])

    def test_pending_requests_are_not_followers(self):
        self.follow(self.friend, self.me, FollowStatus.PENDING)

        response = self.client.get("/api/v1/users/armaan/followers/")

        self.assertEqual(response.data["results"], [])

    def test_a_private_account_you_dont_follow_hides_its_lists(self):
        self.make_private(self.friend)

        self.assertEqual(self.client.get("/api/v1/users/saiuri/followers/").status_code, status.HTTP_404_NOT_FOUND)
        self.follow(self.me, self.friend)
        self.assertEqual(self.client.get("/api/v1/users/saiuri/followers/").status_code, status.HTTP_200_OK)

    def test_nobody_across_a_block_from_you_is_listed(self):
        User = get_user_model()
        blocks_me = User.objects.create_user(username="blocksme", password="strong-password-123")
        i_block = User.objects.create_user(username="iblock", password="strong-password-123")
        self.follow(blocks_me, self.friend)
        self.follow(i_block, self.friend)
        Block.objects.create(blocker=blocks_me, blocked=self.me)
        Block.objects.create(blocker=self.me, blocked=i_block)

        response = self.client.get("/api/v1/users/saiuri/followers/")

        self.assertEqual(response.data["results"], [])


class UserSearchTests(SocialTestCase):
    """Finding people by username or display name."""

    def search(self, query):
        response = self.client.get("/api/v1/users/search/", {"q": query})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [row["username"] for row in response.data["results"]]

    def test_matches_display_names_and_finds_private_accounts(self):
        self.make_private(self.friend)

        response = self.client.get("/api/v1/users/search/", {"q": "sai"})

        self.assertEqual(response.data["results"][0]["username"], "saiuri")
        self.assertTrue(response.data["results"][0]["is_private"])
        self.assertEqual(self.search("Sai"), ["saiuri"])

    def test_exact_username_first_then_prefixes_and_an_at_sign_is_ignored(self):
        User = get_user_model()
        User.objects.create_user(username="aasai", password="strong-password-123")
        User.objects.create_user(username="sai", password="strong-password-123")

        self.assertEqual(self.search("@sai"), ["sai", "saiuri", "aasai"])

    def test_leaves_out_you_people_who_block_you_and_empty_queries(self):
        Block.objects.create(blocker=self.friend, blocked=self.me)

        self.assertEqual(self.search("a"), [])
        self.assertEqual(self.search(""), [])


class ProfileVisibilityTests(SocialTestCase):
    """What someone else's profile shows."""

    def profile(self, username="saiuri"):
        return self.client.get(f"/api/v1/users/{username}/")

    def item(self, media_id):
        # Anime: movie, music, book and game logs are always public.
        return Item.objects.create(
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id=media_id,
            title=f"Anime {media_id}",
            image="https://example.com/poster.jpg",
        )

    def test_a_private_account_you_dont_follow_shows_only_its_header(self):
        self.make_private(self.friend)
        self.log("1", visibility="private", review="Secret")
        self.log("2", visibility="public")

        response = self.profile()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["restricted"])
        self.assertEqual(response.data["hof"], {})
        self.assertEqual(response.data["display_name"], "Sai")
        # Like a post count: how many logs, but nothing about them.
        self.assertEqual(response.data["counts"]["diary_entries"], 2)
        self.assertEqual(
            (response.data["counts"]["reviews"], response.data["counts"]["tags"], response.data["counts"]["lists"]),
            (0, 0, 0),
        )
        self.follow(self.me, self.friend)
        self.assertFalse(self.profile().data["restricted"])

    def test_someone_who_blocks_you_is_gone_and_someone_you_block_shows_their_header(self):
        Block.objects.create(blocker=self.me, blocked=self.friend)

        response = self.profile()

        self.assertTrue(response.data["restricted"])
        self.assertTrue(response.data["viewer_relationship"]["blocked"])
        self.client.force_authenticate(self.friend)
        self.assertEqual(self.profile("armaan").status_code, status.HTTP_404_NOT_FOUND)

    def log(self, media_id, **fields):
        return DiaryEntry.objects.create(
            user=self.friend,
            item=self.item(media_id),
            consumed_at=datetime(2026, 10, 1, tzinfo=UTC),
            **fields,
        )

    def test_someone_elses_counts_cover_only_what_you_may_open(self):
        self.log("1", visibility="public", review="Great")
        self.log("2", visibility="followers")
        self.log("3", visibility="private", review="Secret")
        CustomList.objects.create(name="Shared", owner=self.friend, visibility=CustomList.Visibility.PUBLIC)
        CustomList.objects.create(name="Mine", owner=self.friend, visibility=CustomList.Visibility.PRIVATE)

        stranger = self.profile().data["counts"]
        self.follow(self.me, self.friend)
        follower = self.profile().data["counts"]
        self.client.force_authenticate(self.friend)
        own = self.client.get("/api/v1/me/").data["counts"]

        self.assertEqual((stranger["diary_entries"], stranger["reviews"], stranger["lists"]), (1, 1, 1))
        self.assertEqual((follower["diary_entries"], follower["reviews"], follower["lists"]), (2, 1, 1))
        self.assertEqual((own["diary_entries"], own["reviews"], own["lists"]), (3, 2, 2))


class PushDeviceTests(SocialTestCase):
    """Phones registering for pushes."""

    def register(self, **fields):
        return self.client.post(
            "/api/v1/me/push-devices/",
            {"token": TOKEN, "bundle_id": "com.armaan.Spine", "environment": "production", **fields},
            format="json",
        )

    def test_registering_twice_keeps_one_device_and_signing_in_as_someone_else_moves_it(self):
        self.register()
        self.register(environment="sandbox")
        self.client.force_authenticate(self.friend)
        self.register()

        device = PushDevice.objects.get()
        self.assertEqual((device.user, device.environment), (self.friend, "production"))

    def test_a_bad_token_or_environment_is_refused(self):
        self.assertEqual(self.register(token="not-hex").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.register(environment="staging").status_code, status.HTTP_400_BAD_REQUEST)

    def test_signing_out_removes_only_your_own_device(self):
        PushDevice.objects.create(user=self.friend, token=TOKEN, bundle_id="com.armaan.Spine", environment="production")

        self.client.delete(f"/api/v1/me/push-devices/{TOKEN}/")
        self.assertTrue(PushDevice.objects.exists())
        self.client.force_authenticate(self.friend)
        self.client.delete(f"/api/v1/me/push-devices/{TOKEN}/")
        self.assertFalse(PushDevice.objects.exists())


@override_settings(**PUSH_KEY)
class PushSendTests(SocialTestCase):
    """Sending pushes through Apple."""

    def setUp(self):
        super().setUp()
        push._token = None
        self.requests = []
        self.answer = httpx.Response(200)

    def fake_apple(self):
        def handler(request):
            self.requests.append(request)
            return self.answer

        return mock.patch.object(push, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))

    def device(self, token=TOKEN, **fields):
        return PushDevice.objects.create(
            user=self.friend,
            token=token,
            **{"bundle_id": "com.armaan.Spine.dev", "environment": "sandbox", **fields},
        )

    def test_sends_to_each_phone_on_its_own_apple_host_and_app(self):
        self.device()
        self.device(token="cd" * 32, bundle_id="com.armaan.Spine", environment="production")

        with self.fake_apple():
            sent = push.send(self.friend.id, "Armaan started following you.", {"kind": "profile", "username": "armaan"})

        self.assertEqual(sent, 2)
        sandbox, production = sorted(self.requests, key=lambda request: request.url.host)[::-1]
        self.assertEqual(str(sandbox.url), f"https://api.sandbox.push.apple.com/3/device/{TOKEN}")
        self.assertEqual(sandbox.headers["apns-topic"], "com.armaan.Spine.dev")
        self.assertEqual(production.url.host, "api.push.apple.com")
        self.assertEqual(production.headers["apns-topic"], "com.armaan.Spine")
        body = json.loads(sandbox.content)
        self.assertEqual(body["aps"]["alert"]["body"], "Armaan started following you.")
        self.assertEqual(body["route"], {"kind": "profile", "username": "armaan"})
        claims = jwt.decode(
            sandbox.headers["authorization"].removeprefix("bearer "),
            KEY.public_key(),
            algorithms=["ES256"],
        )
        self.assertEqual(claims["iss"], "TEAM123456")
        self.assertEqual(jwt.get_unverified_header(sandbox.headers["authorization"][7:])["kid"], "KEY1234567")

    def test_a_dead_token_is_dropped_and_other_refusals_are_kept(self):
        self.device()
        self.answer = httpx.Response(410, json={"reason": "Unregistered"})
        with self.fake_apple():
            self.assertEqual(push.send(self.friend.id, "Hi", {}), 0)
        self.assertFalse(PushDevice.objects.exists())

        self.device()
        self.answer = httpx.Response(429, json={"reason": "TooManyRequests"})
        with self.fake_apple():
            push.send(self.friend.id, "Hi", {})
        self.assertTrue(PushDevice.objects.exists())

    def test_the_provider_token_is_reused_until_it_is_old(self):
        first = push.provider_token()
        self.assertEqual(push.provider_token(), first)
        with mock.patch.object(push.time, "time", return_value=push._token[2] + push.TOKEN_LIFETIME):
            self.assertNotEqual(push.provider_token(), first)

    def test_a_follow_sends_the_push_after_the_follow_is_saved(self):
        self.device()

        with self.fake_apple(), self.captureOnCommitCallbacks(execute=True):
            self.client.post("/api/v1/users/saiuri/follow/")

        self.assertEqual(len(self.requests), 1)

    @override_settings(APNS_PRIVATE_KEY="")
    def test_nothing_is_sent_without_a_key(self):
        self.device()

        with self.fake_apple(), self.captureOnCommitCallbacks(execute=True):
            self.client.post("/api/v1/users/saiuri/follow/")

        self.assertEqual(self.requests, [])

    @override_settings(APNS_PRIVATE_KEY="bm90IGEga2V5")
    def test_an_unreadable_key_sends_nothing(self):
        self.device()

        with self.fake_apple(), self.assertLogs("social.push", "ERROR"):
            self.assertEqual(push.send(self.friend.id, "Hi", {}), 0)
        self.assertEqual(self.requests, [])
