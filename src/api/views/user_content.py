"""Another user's diary, tags, likes, lists and tracking, read-only.

Each answers in the shape of the signed-in user's own endpoint, so the app reuses its
decoders, and holds only what the viewer may see. A profile the viewer can't see
(``can_view_user_profile``: unknown, blocked either way, or private and not followed)
is a 404.
"""

from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.serializers.common import SERIES_TYPES, media_summary_from_item
from api.services import activity as activity_service
from api.services import diary as diary_service
from api.services import stats as stats_service
from api.views.diary import DiaryListView
from api.views.lists import _list_type_query, _lists_visible_to, list_payload
from api.views.stats import visible_profile_owner
from api.views.tracking import TrackingListView
from app import exposure
from app.curation import prime_curated_artwork
from app.models import DiaryEntry, MediaLike, MediaTypes

# What a manga's or comic's tracking row says about its logs.
SERIES_LOG_FACTS = (
    "series",
    "liked",
    "diary_count",
    "rating_source_diary_entry_id",
    "like_source_diary_entry_id",
)


class UserDiaryListView(DiaryListView):
    """GET /users/<username>/diary/: their logs the viewer may see, as /diary/."""

    http_method_names = ["get", "head", "options"]

    def get(self, request, username):
        owner = visible_profile_owner(request, username)
        entries = stats_service.visible_diary_entries(user=owner, viewer=request.user)
        return self.list_response(request, owner, entries)


class UserDiaryTagsView(APIView):
    """GET /users/<username>/diary/tags/: their tags on the logs the viewer may see."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        owner = visible_profile_owner(request, username)
        limit = None if request.query_params.get("all") == "true" else 10
        results = diary_service.tag_results(
            request.query_params.get("q", ""),
            user=owner,
            limit=limit,
            entries=stats_service.visible_diary_entries(user=owner, viewer=request.user),
        )
        return Response({"results": results})


class UserLikedMediaView(APIView):
    """GET /users/<username>/liked-media/: their liked media, as /me/liked-media/."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        owner = visible_profile_owner(request, username)
        likes = MediaLike.objects.filter(
            user=owner,
            item__media_type__in=exposure.media_types(),
        ).select_related("item").order_by("-created_at", "-id")
        media_type = request.query_params.get("media_type")
        if media_type:
            exposure.require_media_type(media_type)
            likes = likes.filter(item__media_type=media_type)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(likes, request, view=self)
        prime_curated_artwork(like.item for like in page)
        # media.user_state is the viewer's own, as on every other shared collection.
        return paginator.get_paginated_response(
            [media_summary_from_item(like.item, request=request, user=request.user) for like in page],
        )


class UserListsView(APIView):
    """GET /users/<username>/lists/: their lists the viewer may browse, as /lists/."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        owner = visible_profile_owner(request, username)
        lists = (
            _lists_visible_to(request.user, owner)
            .select_related("owner")
            .prefetch_related("collaborators")
        )
        list_type = _list_type_query(request)
        if list_type != "all":
            lists = lists.filter(list_type=list_type)
        query = request.query_params.get("q", "")
        if query:
            lists = lists.filter(Q(name__icontains=query) | Q(description__icontains=query))
        return Response(
            {
                "count": lists.count(),
                "next": None,
                "previous": None,
                "results": [
                    list_payload(custom_list, request=request, include_preview_items=True)
                    for custom_list in lists[:100]
                ],
            },
        )


class UserTrackingListView(TrackingListView):
    """GET /users/<username>/tracking/: their tracked media, as /tracking/."""

    http_method_names = ["get", "head", "options"]

    def get(self, request, username):
        owner = visible_profile_owner(request, username)
        response = self.list_response(request, owner)
        if owner != request.user:
            # Tracking notes are the owner's own jottings, which no profile page shows.
            for row in response.data.get("results", ()):
                row["tracking"]["notes"] = ""
            _hide_series_logs(response.data.get("results", ()), request.user, owner)
        return response


def _hide_series_logs(rows, viewer, owner):
    """Tell another person nothing of logs they can't see (AC-5): manga, comics, anime.

    Those logs can set the rating and the end date, and an anime's its start and last
    watched day too. The attempts and log counts say the rest, so another person gets
    where the title stands, as AC-6 has it.
    """
    series = [row for row in rows if row["media"]["ref"]["media_type"] in (*SERIES_TYPES, MediaTypes.ANIME.value)]
    if not series:
        return
    visible, _ = activity_service.profile_sources(viewer, owner)
    hidden = set(
        DiaryEntry.objects.filter(user=owner, item_id__in=[row["media"]["ref"]["item_id"] for row in series])
        .exclude(pk__in=visible.values("pk"))
        .values_list("item_id", flat=True),
    )
    for row in series:
        tracking = row["tracking"]
        for key in SERIES_LOG_FACTS:
            tracking.pop(key, None)
        if row["media"]["ref"]["item_id"] in hidden:
            tracking["rating"] = None
            tracking["end_date"] = None
            if "anime" in tracking:
                tracking["start_date"] = None
                tracking["anime"]["last_watched_on"] = None
