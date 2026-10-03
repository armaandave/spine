from django.conf import settings
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.http import urlencode
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.exceptions import (
    AllMediaSearchUnavailable,
    ListTypeMismatch,
    PeopleSearchUnavailable,
)
from api.pagination import StandardResultsSetPagination
from api.serializers.common import PersonCompletionResponseSerializer
from api.services import completion as completion_service
from api.services import diary as diary_service
from api.services import filters as filter_service
from api.services import media as media_service
from api.throttling import SearchRateThrottle
from api.views.mixins import MediaExposureMixin
from app import config, exposure, single_weight
from app.forms import ManualItemForm
from app.models import BasicMedia, DiaryEntry, MediaTypes, Status
from app.providers import anilist
from app.providers import services as provider_services
from lists.models import CustomList, CustomListItem
from social.models import ContentLike


class MediaSearchView(MediaExposureMixin, APIView):
    """Provider-backed media search."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SearchRateThrottle]

    def get(self, request):
        scope = request.query_params.get("scope")
        media_type = request.query_params.get("media_type")
        query = request.query_params.get("q", "").strip()
        if scope == "all":
            return self._search_all(request, query)
        if scope:
            return Response({"scope": ["Use all."]}, status=status.HTTP_400_BAD_REQUEST)
        if not media_type or not query:
            return Response(
                {"media_type": ["This field is required."], "q": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        page = int(request.query_params.get("page", 1))
        results = media_service.search_media(
            media_type=media_type,
            query=query,
            page=page,
            source=request.query_params.get("source"),
            request=request,
            user=request.user,
        )
        return Response({"count": len(results), "next": None, "previous": None, "results": results})

    @staticmethod
    def _search_all(request, query):
        if not query:
            return Response({"q": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if request.query_params.get("page", "1") != "1":
            return Response(
                {"page": ["All-media search only supports the first page."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        enabled = set(request.user.get_enabled_media_types())
        media_types = [value for value in exposure.primary_media_types() if value in enabled]
        if not media_types:
            return Response(
                {"media_types": ["Enable at least one media type in Settings."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = media_service.search_all_media(
            media_types=media_types,
            query=query,
            request=request,
            user=request.user,
        )
        if not payload["completed_media_types"]:
            raise AllMediaSearchUnavailable
        return Response(
            {
                "count": len(payload["results"]),
                "next": None,
                "previous": None,
                "results": payload["results"],
                "unavailable_media_types": payload["unavailable_media_types"],
            },
        )


class MediaSourcesView(APIView):
    """Source map by media type."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(exposure.source_map())


class PeopleSearchView(APIView):
    """Search all supported provider-backed people."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SearchRateThrottle]

    def get(self, request):
        query = " ".join(request.query_params.get("q", "").split())
        if not query:
            return Response(
                {"q": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(query) > 100:
            return Response(
                {"q": ["Ensure this field has no more than 100 characters."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.query_params.get("page", "1") != "1":
            return Response(
                {"page": ["People search only supports the first page."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = media_service.search_people(query=query)
        if not payload["completed_sources"]:
            raise PeopleSearchUnavailable
        return Response({
            "count": len(payload["results"]),
            "next": None,
            "previous": None,
            "results": payload["results"],
            "unavailable_sources": payload["unavailable_sources"],
        })


class FilterOptionsView(APIView):
    """Return available reusable filter values for a collection scope."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get("scope")
        if scope == "tracking":
            media_type = request.query_params.get("media_type")
            if not media_type:
                return Response({"media_type": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
            status_filter = request.query_params.get("status", "All")
            manager_status_filter = "All" if str(status_filter).lower() == "tracked" else status_filter
            queryset = BasicMedia.objects.get_media_list(
                request.user,
                media_type,
                manager_status_filter,
                None,
            )
            if str(status_filter).lower() == "tracked":
                queryset = queryset.exclude(status=Status.PLANNING.value)
            filter_service.ensure_filter_metadata(queryset, request.query_params)
            return Response(filter_service.filter_options_for_items(queryset, request.query_params))

        if scope == "diary":
            queryset = DiaryEntry.objects.filter(
                user=request.user,
                item__media_type__in=exposure.media_types(),
            ).select_related("item")
            filter_service.ensure_filter_metadata(queryset, request.query_params)
            return Response(filter_service.filter_options_for_items(queryset, request.query_params))

        if scope == "list":
            list_id = request.query_params.get("list_id")
            custom_list = get_object_or_404(CustomList, id=list_id)
            if custom_list.visibility == CustomList.Visibility.PRIVATE and not custom_list.user_can_view(request.user):
                return Response(status=status.HTTP_404_NOT_FOUND)
            if custom_list.list_type != CustomList.ListType.MEDIA:
                raise ListTypeMismatch
            queryset = CustomListItem.objects.filter(
                custom_list=custom_list,
                item__media_type__in=exposure.media_types(),
            ).select_related("item")
            filter_service.ensure_filter_metadata(queryset, request.query_params)
            return Response(filter_service.filter_options_for_items(queryset, request.query_params))

        return Response({"scope": ["Use tracking, diary, or list."]}, status=status.HTTP_400_BAD_REQUEST)


class MediaDiscoverView(MediaExposureMixin, APIView):
    """Provider-backed media discovery."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SearchRateThrottle]

    def get(self, request):
        errors = _discover_errors(request.query_params)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        paginator = StandardResultsSetPagination()
        page_size = paginator.get_page_size(request)
        page = int(request.query_params.get("page", 1))
        try:
            payload = media_service.discover_media(
                media_type=request.query_params["media_type"],
                source=request.query_params.get("source"),
                page=page,
                page_size=page_size,
                genre=request.query_params.get("genre"),
                year=request.query_params.get("year"),
                platform=request.query_params.get("platform"),
                sort=request.query_params.get("sort", "vote_count"),
                request=request,
                user=request.user,
            )
        except NotImplementedError as error:
            return Response({"detail": str(error)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        count = payload["count"]
        provider_page_size = payload["page_size"]
        return Response(
            {
                "count": count,
                "next": _discover_page_url(request, page + 1) if page * provider_page_size < count else None,
                "previous": _discover_page_url(request, page - 1) if page > 1 else None,
                "results": payload["results"],
            },
        )


def _discover_errors(params):
    errors = {}
    media_type = params.get("media_type")
    if not media_type:
        errors["media_type"] = ["This field is required."]
    elif media_type not in config.MEDIA_TYPE_CONFIG:
        errors["media_type"] = ["Unsupported media type."]

    if not any(params.get(name) for name in ["genre", "year", "platform"]):
        errors["non_field_errors"] = ["At least one of genre, year, or platform is required."]

    year = params.get("year")
    if year and not (year.isdigit() and len(year) == 4):
        errors["year"] = ["Enter a 4-digit year."]

    sort = params.get("sort")
    if sort and sort not in ["vote_count", "-vote_count"]:
        errors["sort"] = ["Unsupported sort. Use vote_count."]

    for field in ["page", "page_size"]:
        value = params.get(field)
        if value and (not value.isdigit() or int(value) < 1):
            errors[field] = ["Enter a positive integer."]

    return errors


def _discover_page_url(request, page):
    params = request.query_params.copy()
    params["page"] = page
    return request.build_absolute_uri(f"{request.path}?{urlencode(params, doseq=True)}")


class ManualMediaView(MediaExposureMixin, APIView):
    """Create a manual media item."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {
            "media_type": request.data.get("media_type"),
            "title": request.data.get("title"),
            "image": request.data.get("image_url") or settings.IMG_NONE,
            "season_number": request.data.get("season_number"),
            "episode_number": request.data.get("episode_number"),
        }
        form = ManualItemForm(data, user=request.user)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        item = form.save()
        from api.serializers.common import media_summary_from_item

        return Response(media_summary_from_item(item, request=request, user=request.user), status=status.HTTP_201_CREATED)


class MediaDetailView(MediaExposureMixin, APIView):
    """Provider-backed media detail."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.media_detail(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    season_number=request.query_params.get("season_number"),
                    episode_number=request.query_params.get("episode_number"),
                    request=request,
                    user=request.user if request.user.is_authenticated else None,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MediaExternalRatingsView(MediaExposureMixin, APIView):
    """Cached external-rating state for a materialized media identity."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.media_external_rating_payload(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    season_number=request.query_params.get("season_number"),
                    episode_number=request.query_params.get("episode_number"),
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MusicRecordingDetailView(APIView):
    """Read-only MusicBrainz recording detail within an album context."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def initial(self, request, *args, **kwargs):
        exposure.require_media_type(MediaTypes.MUSIC.value)
        return super().initial(request, *args, **kwargs)

    def get(self, request, release_group_mbid, recording_mbid):
        try:
            return Response(
                media_service.music_recording_detail(
                    release_group_mbid=release_group_mbid,
                    recording_mbid=recording_mbid,
                    request=request,
                    user=request.user if request.user.is_authenticated else None,
                ),
            )
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                raise Http404 from error
            raise


class MusicDetailStageView(APIView):
    """Return one stage of MusicBrainz album detail."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]
    include_music_enrichment = False

    def initial(self, request, *args, **kwargs):
        exposure.require_media_type(MediaTypes.MUSIC.value)
        return super().initial(request, *args, **kwargs)

    def get(self, request, release_group_mbid):
        return Response(
            media_service.media_detail(
                source="musicbrainz",
                media_type=MediaTypes.MUSIC.value,
                media_id=str(release_group_mbid),
                request=request,
                user=request.user if request.user.is_authenticated else None,
                include_music_enrichment=self.include_music_enrichment,
            ),
        )


class MusicBasicDetailView(MusicDetailStageView):
    """Basic release-group detail without deferred music metadata."""


class MusicEnrichmentDetailView(MusicDetailStageView):
    """Deferred cover art and representative release detail for one album."""

    include_music_enrichment = True


class PersonDetailView(APIView):
    """Provider-backed person detail for native clients."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, person_id):
        try:
            credits_page = int(request.query_params.get("credits_page", 1))
        except (TypeError, ValueError):
            credits_page = 0
        if source == "anilist" and not 1 <= credits_page <= anilist.PERSON_PAGE_LIMIT:
            return Response(
                {
                    "credits_page": [
                        f"Use a page from 1 to {anilist.PERSON_PAGE_LIMIT}.",
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return Response(
                media_service.person_detail(
                    source=source,
                    person_id=person_id,
                    request=request,
                    user=request.user if request.user.is_authenticated else None,
                    params=request.query_params,
                    credits_page=credits_page,
                ),
            )
        except NotImplementedError as error:
            return Response({"detail": str(error)}, status=status.HTTP_501_NOT_IMPLEMENTED)


class PersonCompletionView(APIView):
    """Viewer completion for one provider person's full filmography."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SearchRateThrottle]

    @extend_schema(responses=PersonCompletionResponseSerializer)
    def get(self, request, source, person_id):
        try:
            completion = media_service.person_completion(
                source=source,
                person_id=person_id,
                user=request.user,
            )
        except NotImplementedError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                return Response(
                    {"detail": "Person not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            raise
        return Response({"completion": completion})


class BookSeriesDetailView(APIView):
    """Provider-backed media series for native clients."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, series_id):
        try:
            return Response(
                media_service.series_detail(
                    source=source,
                    series_id=series_id,
                    request=request,
                    user=request.user if request.user.is_authenticated else None,
                ),
            )
        except NotImplementedError as error:
            return Response({"detail": str(error)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                return Response(
                    {"detail": "Series not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            raise


class CompanyDetailView(APIView):
    """Provider-backed company profile for native studio pages."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, company_id):
        try:
            return Response(
                media_service.company_detail(
                    source=source,
                    company_id=company_id,
                    request=request,
                    user=(
                        request.user
                        if request.user.is_authenticated
                        else None
                    ),
                ),
            )
        except NotImplementedError as error:
            return Response({"detail": str(error)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
            raise


class CompanyGamesView(APIView):
    """Paginated games developed or published by a provider company."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, company_id):
        try:
            games = media_service.company_games(
                source=source,
                company_id=company_id,
                role=request.query_params.get("role", "developed"),
                sort=request.query_params.get("sort", "popularity"),
                direction=request.query_params.get("direction"),
                params=request.query_params,
                request=request,
                user=request.user if request.user.is_authenticated else None,
            )
        except NotImplementedError as error:
            return Response({"detail": str(error)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
            raise

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(games, request, view=self)
        response = paginator.get_paginated_response(page)
        response.data["completion"] = completion_service.completion_for_summaries(
            request.user if request.user.is_authenticated else None,
            games,
        )
        return response


class CompanyGameOptionsView(APIView):
    """Complete provider-backed filter choices for a company's game catalogs."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, company_id):
        try:
            return Response(media_service.company_game_filter_options(source=source, company_id=company_id))
        except NotImplementedError as error:
            return Response({"detail": str(error)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
            raise


class CompanyAnimeView(APIView):
    """Provider-paginated anime credited to a MAL studio."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, company_id):
        try:
            return Response(
                media_service.company_anime(
                    source=source,
                    company_id=company_id,
                    sort=request.query_params.get("sort", "popularity"),
                    direction=request.query_params.get("direction"),
                    params=request.query_params,
                    request=request,
                    user=(
                        request.user
                        if request.user.is_authenticated
                        else None
                    ),
                ),
            )
        except NotImplementedError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                return Response(
                    {"detail": "Company not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            raise


class CompanyAnimeOptionsView(APIView):
    """Provider-backed filter choices for a MAL studio anime catalog."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, company_id):
        try:
            return Response(
                media_service.company_anime_filter_options(
                    source=source,
                    company_id=company_id,
                ),
            )
        except NotImplementedError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except provider_services.ProviderAPIError as error:
            if error.status_code == status.HTTP_404_NOT_FOUND:
                return Response(
                    {"detail": "Company not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            raise


class MediaReviewsView(MediaExposureMixin, APIView):
    """Public diary reviews for a media identity."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_type, media_id):
        from app.models import DiaryEntry, Item

        item = Item.objects.filter(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=request.query_params.get("season_number"),
            episode_number=request.query_params.get("episode_number"),
        ).first()
        if item is None:
            return Response({"count": 0, "next": None, "previous": None, "results": []})

        entries = DiaryEntry.objects.filter(item=item).exclude(review="")
        if single_weight.uses_half_star_rating(media_type):
            visibility = Q(user__profile_private=False)
            if request.user.is_authenticated:
                from social.models import Block, Follow, FollowStatus

                followed = Follow.objects.filter(
                    from_user=request.user,
                    status=FollowStatus.ACCEPTED,
                ).values("to_user")
                blocked = Block.objects.filter(
                    Q(blocker=request.user) | Q(blocked=request.user),
                ).values_list("blocker_id", "blocked_id")
                blocked_ids = {
                    user_id
                    for pair in blocked
                    for user_id in pair
                    if user_id != request.user.id
                }
                visibility |= Q(user=request.user) | Q(user__in=followed)
                entries = entries.filter(visibility).exclude(user_id__in=blocked_ids)
            else:
                entries = entries.filter(visibility)
        else:
            entries = entries.exclude(visibility="private")
        entries = entries.select_related("item", "user").prefetch_related("tags")
        if request.query_params.get("sort", "popular") == "recent":
            entries = entries.order_by("-created_at", "-id")
        else:
            like_counts = (
                ContentLike.objects.filter(
                    target_type=ContentLike.DIARY_ENTRY,
                    target_id=OuterRef("pk"),
                )
                .values("target_id")
                .annotate(count=Count("id"))
                .values("count")
            )
            entries = entries.annotate(
                review_like_count=Coalesce(
                    Subquery(like_counts, output_field=IntegerField()),
                    Value(0),
                ),
            ).order_by("-review_like_count", "-created_at", "-id")

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(entries, request, view=self)
        viewer = request.user if request.user.is_authenticated else None
        diary_service.prime_diary_likes(page, viewer)
        return paginator.get_paginated_response(
            [diary_service.diary_payload(entry, request=request, viewer=viewer) for entry in page],
        )


class AniListReviewsView(MediaExposureMixin, APIView):
    """Paginated AniList written reviews for MAL anime."""

    permission_classes = [AllowAny]
    throttle_classes = [SearchRateThrottle]

    def get(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.anilist_reviews(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    page=request.query_params.get("page", "1"),
                ),
            )
        except NotImplementedError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class MediaPostersView(MediaExposureMixin, APIView):
    """Selectable poster images for supported media."""

    permission_classes = [IsAuthenticated]

    def get(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.poster_options(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    season_number=request.query_params.get("season_number"),
                    request=request,
                    user=request.user,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MediaPosterPreferenceView(MediaExposureMixin, APIView):
    """Save the viewer's selected poster."""

    permission_classes = [IsAuthenticated]

    def put(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.save_poster_preference(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    season_number=request.data.get("season_number"),
                    poster_url=request.data.get("poster_url"),
                    user=request.user,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MediaBackdropsView(MediaExposureMixin, APIView):
    """Selectable backdrop images for TMDB movie/TV media."""

    permission_classes = [IsAuthenticated]

    def get(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.backdrop_options(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    season_number=request.query_params.get("season_number"),
                    episode_number=request.query_params.get("episode_number"),
                    request=request,
                    user=request.user,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MediaBackdropPreferenceView(MediaExposureMixin, APIView):
    """Save the viewer's selected backdrop."""

    permission_classes = [IsAuthenticated]

    def put(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.save_backdrop_preference(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    backdrop_url=request.data.get("backdrop_url"),
                    user=request.user,
                    season_number=request.data.get("season_number"),
                    episode_number=request.data.get("episode_number"),
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MediaLogosView(MediaExposureMixin, APIView):
    """Selectable title logos for TMDB movie/TV media and IGDB games."""

    permission_classes = [IsAuthenticated]

    def get(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.logo_options(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    request=request,
                    user=request.user,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class MediaLogoPreferenceView(MediaExposureMixin, APIView):
    """Save the viewer's selected title logo."""

    permission_classes = [IsAuthenticated]

    def put(self, request, source, media_type, media_id):
        try:
            return Response(
                media_service.save_logo_preference(
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    logo_url=request.data.get("logo_url"),
                    user=request.user,
                ),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class TVSeasonsView(APIView):
    """TV season summaries."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_id):
        return Response(
            media_service.tv_seasons(
                source=source,
                media_id=media_id,
                request=request,
                user=request.user if request.user.is_authenticated else None,
            ),
        )


class SeasonDetailView(APIView):
    """TV season detail."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_id, season_number):
        return Response(
            media_service.season_detail(
                source=source,
                media_id=media_id,
                season_number=season_number,
                request=request,
                user=request.user if request.user.is_authenticated else None,
            ),
        )


class SeasonEpisodesView(APIView):
    """TV season episodes."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_id, season_number):
        return Response(
            media_service.season_episodes(
                source=source,
                media_id=media_id,
                season_number=season_number,
                request=request,
                user=request.user if request.user.is_authenticated else None,
            ),
        )


class CommunityStatsView(MediaExposureMixin, APIView):
    """Community aggregate placeholder."""

    permission_classes = [AllowAny]

    def get(self, request, source, media_type, media_id):
        return Response(
            media_service.community_stats(
                source=source,
                media_type=media_type,
                media_id=media_id,
                season_number=request.query_params.get("season_number"),
                episode_number=request.query_params.get("episode_number"),
            ),
        )
