from concurrent.futures import ThreadPoolExecutor, wait

import requests
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.exceptions import ListTypeMismatch, PersonProviderUnavailable
from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile, users_blocked
from api.serializers.common import (
    absolute_url,
    find_item,
    get_or_create_item_from_metadata,
    image_url,
    media_summary_from_item,
    prime_collection_items,
    user_summary,
)
from api.serializers.lists import (
    CollaboratorSerializer,
    CustomListWriteSerializer,
    ListItemsReorderSerializer,
    ListItemWriteSerializer,
    ListPeoplePageSerializer,
    ListPeopleReorderSerializer,
    ListPersonWriteResponseSerializer,
    ListPersonWriteSerializer,
)
from api.services import completion as completion_service
from api.services import filters as filter_service
from api.services import media as media_service
from api.services.social import set_like
from app import exposure
from app.curation import prime_curated_artwork
from app.models import MediaTypes
from app.providers import services as provider_services
from lists.models import CustomList, CustomListItem, FeaturedListSync, PersonListItem
from social.models import Activity, ContentLike, SocialAuditLog
from streaming import filters as streaming_filters

LIST_PREVIEW_ITEM_LIMIT = 12
PERSON_COMPLETION_BATCH_LIMIT = 25
PERSON_COMPLETION_WORKERS = 8
PERSON_COMPLETION_TIMEOUT = 25
# Media types whose detail page has a backdrop.
HEADER_BACKDROP_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.GAME.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
}


HEADER_BACKDROP_ATTEMPTS = 5


def _list_item_summaries(items, request, *, resolve_header):
    """Serialize items, resolving the header backdrop the app shows above the list.

    The first few items that can have a backdrop are tried in order until one has
    it, so a list still gets a header when its first title has no backdrop.
    """
    attempts = HEADER_BACKDROP_ATTEMPTS if resolve_header else 0
    found = False
    summaries = []
    for item in items:
        resolve = not found and attempts > 0 and item.media_type in HEADER_BACKDROP_MEDIA_TYPES
        summary = _list_item_summary(item, request, resolve_backdrop=resolve)
        if resolve:
            attempts -= 1
            found = bool(summary.get("backdrop_url") or summary.get("custom_backdrop_url"))
        summaries.append(summary)
    return summaries


def _list_item_summary(item, request, *, resolve_backdrop):
    """Serialize a list item; only the header item pays for a provider backdrop."""
    try:
        return media_summary_from_item(
            item,
            request=request,
            user=request.user,
            include_resolved_backdrop=resolve_backdrop,
            include_user_state=False,
        )
    except (provider_services.ProviderAPIError, requests.exceptions.RequestException):
        # The header backdrop is decoration: a provider outage must not fail the list.
        return media_summary_from_item(
            item,
            request=request,
            user=request.user,
            include_user_state=False,
        )


def person_list_item_payload(list_person, request=None, completion=None):
    """Serialize a stored provider person snapshot."""
    return {
        "entry_id": list_person.id,
        "id": list_person.person_id,
        "source": list_person.source,
        "name": list_person.name,
        "profile_url": absolute_url(request, list_person.profile_url),
        "known_for_department": list_person.known_for_department or None,
        "position": list_person.position,
        "date_added": list_person.date_added,
        "completion": completion,
    }


def featured_entry_notes(custom_list):
    """Return a featured list's notes by item ID or person ref, else None.

    Only lists the featured-lists engine writes have them; their items gain a ``note``
    key (``"Winner · Best Picture"``, ``"▲2 · $12.4M"`` or null), other lists' items
    keep their shape. People's notes come from here; media rows carry their own
    (``CustomListItem.note``), since an award history lists one work once per win.
    """
    return (
        FeaturedListSync.objects.filter(custom_list=custom_list)
        .values_list("entry_notes", flat=True)
        .first()
    )


def person_note_key(list_person):
    """Return the key a person's featured note is stored under."""
    return f"{list_person.source}:person:{list_person.person_id}"


def _with_person_notes(payloads, list_people, notes):
    if notes is not None:
        for payload, list_person in zip(payloads, list_people, strict=True):
            payload["note"] = notes.get(person_note_key(list_person))
    return payloads


def list_payload(
    custom_list,
    request=None,
    *,
    include_items=False,
    include_preview_items=False,
    include_completion=False,
):
    """Serialize a custom list."""
    is_people_list = custom_list.list_type == CustomList.ListType.PEOPLE
    items_count = (
        0
        if is_people_list
        else custom_list.items.filter(
            media_type__in=exposure.media_types(),
        ).count()
    )
    entries_count = custom_list.person_items.count() if is_people_list else items_count
    people_count = entries_count if is_people_list else 0
    data = {
        "id": custom_list.id,
        "name": custom_list.name,
        "slug": custom_list.slug,
        "description": custom_list.description,
        "tags": custom_list.tags,
        "visibility": custom_list.visibility,
        "is_ranked": custom_list.is_ranked,
        "list_type": custom_list.list_type,
        "owner": user_summary(custom_list.owner, request=request),
        "collaborators": [
            user_summary(user, request=request) for user in custom_list.collaborators.all()
        ],
        "image_url": image_url(request, custom_list.image),
        "items_count": items_count,
        "people_count": people_count,
        "entries_count": entries_count,
        "updated_at": custom_list.updated_at,
        "like_count": ContentLike.objects.filter(
            target_type=ContentLike.CUSTOM_LIST,
            target_id=custom_list.id,
        ).count(),
    }
    if include_items or include_completion:
        data["completion"] = (
            completion_service.completion_for_items(
                request.user,
                custom_list.items.filter(
                    media_type__in=exposure.media_types(),
                ),
            )
            if not is_people_list and request is not None
            else None
        )
    if include_preview_items or include_items:
        items, people = [], []
        notes = featured_entry_notes(custom_list) if include_items else None
        if is_people_list:
            list_people = custom_list.person_items.all()
            if include_preview_items and not include_items:
                list_people = list_people[:LIST_PREVIEW_ITEM_LIMIT]
            list_people = list(list_people)
            people = _with_person_notes(_person_list_payloads(list_people, request), list_people, notes)
        else:
            list_items = custom_list.customlistitem_set.select_related("item").filter(
                item__media_type__in=exposure.media_types(),
            )
            if include_preview_items and not include_items:
                list_items = list_items[:LIST_PREVIEW_ITEM_LIMIT]
            list_items = list(list_items)
            prime_curated_artwork(list_item.item for list_item in list_items)
            summaries = _list_item_summaries(
                [list_item.item for list_item in list_items],
                request,
                resolve_header=include_items,
            )
            for list_item, item in zip(list_items, summaries, strict=True):
                item["position"] = list_item.position
                if notes is not None:
                    item["note"] = list_item.note or None
                items.append(item)
        if include_preview_items:
            data["preview_items"] = items
            data["preview_people"] = people
        if include_items:
            data["items"] = items
            data["people"] = people
    return data


def _person_list_payloads(list_people, request):
    """Serialize people with bounded, opt-in provider completion lookups."""
    if (
        request is None
        or request.query_params.get("include_completion") != "true"
    ):
        return [
            person_list_item_payload(list_person, request=request)
            for list_person in list_people
        ]

    cache = getattr(request, "_person_completion_cache", None)
    if cache is None:
        cache = {}
        request._person_completion_cache = cache
        request._person_completion_remaining = PERSON_COMPLETION_BATCH_LIMIT

    unresolved = []
    for list_person in list_people:
        key = (list_person.source, list_person.person_id)
        if key not in cache and request._person_completion_remaining > 0:
            unresolved.append((key, list_person))
            request._person_completion_remaining -= 1

    if unresolved:
        completion_service.completed_item_ids(request.user)
        executor = ThreadPoolExecutor(
            max_workers=min(PERSON_COMPLETION_WORKERS, len(unresolved)),
        )
        futures = {
            executor.submit(
                media_service.person_completion,
                source=list_person.source,
                person_id=list_person.person_id,
                user=request.user,
            ): key
            for key, list_person in unresolved
        }
        done, pending = wait(futures, timeout=PERSON_COMPLETION_TIMEOUT)
        for future in done:
            key = futures[future]
            try:
                cache[key] = future.result()
            except Exception:  # noqa: BLE001 - one provider must not fail the page
                cache[key] = None
        for future in pending:
            cache[futures[future]] = None
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    return [
        person_list_item_payload(
            list_person,
            request=request,
            completion=cache.get((list_person.source, list_person.person_id)),
        )
        for list_person in list_people
    ]


def _renumber_list_items(custom_list):
    if custom_list.list_type == CustomList.ListType.PEOPLE:
        list_items = list(custom_list.person_items.all())
        model = PersonListItem
    else:
        list_items = list(custom_list.customlistitem_set.all())
        model = CustomListItem
    for index, list_item in enumerate(list_items, start=1):
        list_item.position = index
    model.objects.bulk_update(list_items, ["position"])


def _require_list_type(custom_list, list_type):
    if custom_list.list_type != list_type:
        raise ListTypeMismatch


def _list_type_query(request):
    value = request.query_params.get("list_type", CustomList.ListType.MEDIA)
    if value not in {
        CustomList.ListType.MEDIA,
        CustomList.ListType.PEOPLE,
        "all",
    }:
        raise ValidationError({
            "list_type": ["Use media, people, or all."],
        })
    return value


def _person_snapshot(ref):
    try:
        person = provider_services.get_person_page(ref["source"], ref["id"])
    except provider_services.ProviderAPIError as error:
        if error.status_code == status.HTTP_404_NOT_FOUND:
            raise NotFound("Person not found.") from error
        raise

    person_id = str(person.get("person_id") or ref["id"]).strip()
    name = str(person.get("name") or "").strip()
    if not person_id or not name:
        raise PersonProviderUnavailable
    return {
        "source": ref["source"],
        "person_id": person_id[:255],
        "name": name[:255],
        "profile_url": str(
            person.get("image") or person.get("profile_url") or "",
        )[:2048],
        "known_for_department": str(
            person.get("known_for_department") or "",
        )[:255],
    }


def _touch_list(custom_list):
    custom_list.save(update_fields=["updated_at"])


def _can_view_list(user, custom_list):
    if users_blocked(user, custom_list.owner):
        return False
    return (
        custom_list.visibility != CustomList.Visibility.PRIVATE
        or custom_list.user_can_view(user)
    )


def _lists_visible_to(viewer, owner):
    """Return ``owner``'s lists that ``viewer`` may browse.

    All of them for the owner. Anyone else must be able to see the owner's profile
    (``can_view_user_profile``: no block either way, and a private account only for
    accepted followers), then gets the public ones and any they help edit. Unlisted
    lists open only from their link.
    """
    lists = CustomList.objects.filter(owner=owner)
    if viewer == owner:
        return lists
    if not can_view_user_profile(viewer, owner):
        return lists.none()
    helps_edit = CustomList.collaborators.through.objects.filter(
        customlist_id=OuterRef("pk"),
        user_id=viewer.pk,
    )
    return lists.annotate(helps_edit=Exists(helps_edit)).filter(
        Q(visibility=CustomList.Visibility.PUBLIC) | Q(helps_edit=True),
    )


def _has_tag(tags, tag):
    """Return whether a list's tags hold ``tag``, ignoring case and outer spaces."""
    wanted = tag.strip().casefold()
    return isinstance(tags, list) and any(
        isinstance(value, str) and value.strip().casefold() == wanted for value in tags
    )


def _item_from_ref(ref):
    exposure.require_media_type(ref["media_type"])
    item = find_item(ref)
    if item is not None:
        return item
    metadata = provider_services.get_media_metadata(
        ref["media_type"],
        ref["media_id"],
        ref["source"],
        [ref.get("season_number")] if ref.get("season_number") is not None else None,
        ref.get("episode_number"),
    )
    return get_or_create_item_from_metadata(ref, metadata)


class ListsView(APIView):
    """List/create custom lists."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        requested_list_type = request.query_params.get("list_type")
        list_type = _list_type_query(request)
        ref_keys = {
            "source": "ref[source]",
            "media_type": "ref[media_type]",
            "media_id": "ref[media_id]",
            "season_number": "ref[season_number]",
            "episode_number": "ref[episode_number]",
        }
        person_ref_keys = {
            "source": "person_ref[source]",
            "id": "person_ref[id]",
        }
        has_ref = any(key in request.query_params for key in ref_keys.values())
        has_person_ref = any(
            key in request.query_params for key in person_ref_keys.values()
        )
        if has_ref and has_person_ref:
            raise ValidationError({
                "non_field_errors": ["Provide either ref or person_ref, not both."],
            })
        if has_ref:
            if requested_list_type not in (None, CustomList.ListType.MEDIA):
                raise ValidationError({
                    "list_type": ["Media membership requires list_type=media."],
                })
            serializer = ListItemWriteSerializer(
                data={
                    "ref": {
                        name: request.query_params.get(param)
                        for name, param in ref_keys.items()
                        if request.query_params.get(param) not in (None, "")
                    },
                },
            )
            serializer.is_valid(raise_exception=True)
            lists = CustomList.objects.get_user_lists_with_item(
                request.user,
                _item_from_ref(serializer.validated_data["ref"]),
            )
        elif has_person_ref:
            if requested_list_type not in (None, CustomList.ListType.PEOPLE):
                raise ValidationError({
                    "list_type": ["Person membership requires list_type=people."],
                })
            serializer = ListPersonWriteSerializer(
                data={
                    "ref": {
                        name: request.query_params.get(param)
                        for name, param in person_ref_keys.items()
                        if request.query_params.get(param) not in (None, "")
                    },
                },
            )
            serializer.is_valid(raise_exception=True)
            ref = serializer.validated_data["ref"]
            lists = CustomList.objects.get_user_lists_with_person(
                request.user,
                ref["source"],
                ref["id"],
            )
        else:
            lists = (
                CustomList.objects.filter(Q(owner=request.user) | Q(collaborators=request.user))
                .select_related("owner")
                .prefetch_related("collaborators")
                .distinct()
            )
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
                    {
                        **list_payload(custom_list, request=request, include_preview_items=True),
                        **({"has_item": custom_list.has_item} if has_ref else {}),
                        **(
                            {
                                "has_person": custom_list.has_person,
                                "person_entry_id": custom_list.person_entry_id,
                            }
                            if has_person_ref
                            else {}
                        ),
                    }
                    for custom_list in lists[:100]
                ],
            },
        )

    def post(self, request):
        serializer = CustomListWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            custom_list = CustomList.objects.create(
                owner=request.user,
                name=data["name"],
                slug=data.get("slug", ""),
                description=data.get("description", ""),
                visibility=data.get("visibility", CustomList.Visibility.PRIVATE),
                is_ranked=data.get("is_ranked", False),
                list_type=data.get("list_type", CustomList.ListType.MEDIA),
            )
        except IntegrityError as error:
            raise ValidationError({
                "slug": ["You already have a list with this slug."],
            }) from error
        if "collaborator_usernames" in data:
            users = get_user_model().objects.filter(username__in=data["collaborator_usernames"])
            custom_list.collaborators.set(users)
        Activity.objects.create(
            actor=request.user,
            verb="list_created",
            target_type="list",
            target_id=custom_list.id,
            visibility=custom_list.visibility,
            snapshot={
                "name": custom_list.name,
                "list_type": custom_list.list_type,
            },
        )
        return Response(list_payload(custom_list, request=request), status=status.HTTP_201_CREATED)


class TaggedListsView(APIView):
    """One user's lists with a tag, as far as the viewer may see them, A to Z."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        from api.views.featured import list_payloads

        tag = request.query_params.get("tag", "").strip()
        username = request.query_params.get("owner", "").strip()
        missing = {
            field: ["This field is required."]
            for field, value in (("tag", tag), ("owner", username))
            if not value
        }
        if missing:
            raise ValidationError(missing)
        owner = get_object_or_404(get_user_model(), username=username)
        # Tags are a JSON list, so the match runs here: the same on SQLite and Postgres,
        # and Unicode-aware. Only the ID and tags of the owner's tagged lists are read.
        rows = (
            _lists_visible_to(request.user, owner)
            .exclude(tags=[])
            .order_by(Lower("name"), "id")
            .values_list("id", "tags")
        )
        list_ids = [list_id for list_id, tags in rows if _has_tag(tags, tag)]
        paginator = StandardResultsSetPagination()
        page_ids = paginator.paginate_queryset(list_ids, request, view=self)
        lists = (
            CustomList.objects.select_related("owner")
            .prefetch_related("collaborators")
            .in_bulk(page_ids)
        )
        return paginator.get_paginated_response(
            list_payloads([lists[list_id] for list_id in page_ids], request),
        )


class ListDetailView(APIView):
    """Read/update/delete a custom list."""

    permission_classes = [IsAuthenticated]

    def get_object(self, request, list_id, *, include_items=True):
        queryset = CustomList.objects.select_related("owner").prefetch_related("collaborators")
        if include_items:
            queryset = queryset.prefetch_related(
                "customlistitem_set__item",
                "person_items",
            )
        custom_list = get_object_or_404(
            queryset,
            id=list_id,
        )
        if not _can_view_list(request.user, custom_list):
            return None
        return custom_list

    def get(self, request, list_id):
        include_entries = request.query_params.get("include_entries")
        if include_entries is None:
            include_entries = request.query_params.get("include_items")
        include_items = include_entries != "false"
        custom_list = self.get_object(request, list_id, include_items=include_items)
        if custom_list is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        from api.views.featured import featured_block

        payload = list_payload(
            custom_list,
            request=request,
            include_items=include_items,
            include_completion=True,
        )
        featured = featured_block(
            custom_list,
            request,
            items_count=payload["entries_count"],
        )
        if featured is not None:
            payload["featured"] = featured
        return Response(payload)

    def patch(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        serializer = CustomListWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        previous_visibility = custom_list.visibility
        if "list_type" in data:
            raise ValidationError({
                "list_type": ["List type cannot be changed."],
            })
        for field in ["name", "slug", "description", "visibility"]:
            if field in data:
                setattr(custom_list, field, data[field])
        if data.get("is_ranked") is True and not custom_list.is_ranked:
            _renumber_list_items(custom_list)
        if "is_ranked" in data:
            custom_list.is_ranked = data["is_ranked"]
        custom_list.save()
        if "visibility" in data:
            Activity.objects.filter(
                target_type=ContentLike.CUSTOM_LIST,
                target_id=custom_list.id,
            ).update(visibility=custom_list.visibility)
            if custom_list.visibility != previous_visibility:
                SocialAuditLog.objects.create(
                    actor=request.user,
                    action="list_visibility_update",
                    target_type=ContentLike.CUSTOM_LIST,
                    target_id=custom_list.id,
                    metadata={
                        "previous": previous_visibility,
                        "current": custom_list.visibility,
                        "list_type": custom_list.list_type,
                    },
                )
        if "collaborator_usernames" in data:
            users = get_user_model().objects.filter(username__in=data["collaborator_usernames"])
            custom_list.collaborators.set(users)
        return Response(list_payload(custom_list, request=request, include_items=True))

    def delete(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_delete(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        Activity.objects.filter(
            target_type=ContentLike.CUSTOM_LIST,
            target_id=custom_list.id,
        ).delete()
        custom_list.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListItemsView(APIView):
    """List or add items in a custom list."""

    permission_classes = [IsAuthenticated]

    def get(self, request, list_id):
        custom_list = get_object_or_404(
            CustomList.objects.select_related("owner").prefetch_related("collaborators"),
            id=list_id,
        )
        if not _can_view_list(request.user, custom_list):
            return Response(status=status.HTTP_404_NOT_FOUND)
        _require_list_type(custom_list, CustomList.ListType.MEDIA)

        list_items = CustomListItem.objects.filter(
            custom_list=custom_list,
            item__media_type__in=exposure.media_types(),
        ).select_related("item")
        rating_scope_queryset = list_items
        filter_service.ensure_filter_metadata(list_items, request.query_params)
        list_items = filter_service.apply_item_filters(list_items, request.query_params)
        list_items = filter_service.apply_user_status_filter(
            list_items,
            request.user,
            request.query_params.get("status"),
        )
        list_items = filter_service.annotate_user_rating(list_items, request.user)
        list_items = filter_service.apply_rating_range(
            list_items,
            request.query_params,
            "user_rating",
        )
        list_items, streaming = streaming_filters.apply_service_filter(
            list_items,
            request.query_params,
            user=request.user,
            request=request,
        )
        if request.query_params.get("sort") or request.query_params.get("ordering"):
            list_items = filter_service.order_queryset(
                list_items,
                request.query_params,
                your_rating_field="user_rating",
                default_sort="date_added",
                extra_sorts={
                    "date_added": "date_added",
                    "position": "position",
                },
                rating_scope_queryset=rating_scope_queryset,
            )

        paginator = StandardResultsSetPagination()
        page = list(paginator.paginate_queryset(list_items, request, view=self))
        prime_collection_items([list_item.item for list_item in page], request.user)
        notes = featured_entry_notes(custom_list)
        # Only the plain first page, which the app opens a list with, resolves the
        # header backdrop; filtered, sorted and later pages make no provider calls.
        summaries = _list_item_summaries(
            [list_item.item for list_item in page],
            request,
            resolve_header=paginator.page.number == 1
            and set(request.query_params) <= {"page", "page_size"},
        )
        response = paginator.get_paginated_response(
            [
                {
                    **summary,
                    "position": list_item.position,
                    "date_added": list_item.date_added,
                    "your_rating": f"{list_item.user_rating:.1f}"
                    if list_item.user_rating is not None
                    else None,
                    **({"note": list_item.note or None} if notes is not None else {}),
                }
                for list_item, summary in zip(page, summaries, strict=True)
            ],
        )
        response.data["completion"] = completion_service.completion_for_items(
            request.user,
            custom_list.items.filter(
                media_type__in=exposure.media_types(),
            ),
        )
        if streaming is not None:
            response.data["streaming"] = streaming
        return response

    def post(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        _require_list_type(custom_list, CustomList.ListType.MEDIA)
        serializer = ListItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = _item_from_ref(serializer.validated_data["ref"])
        defaults = {}
        if custom_list.is_ranked:
            max_position = (
                CustomListItem.objects.filter(custom_list=custom_list).aggregate(Max("position"))["position__max"] or 0
            )
            defaults["position"] = max_position + 1
        # A person adds an item once; only award histories repeat one (occurrence 1+).
        _, created = CustomListItem.objects.get_or_create(
            custom_list=custom_list,
            item=item,
            occurrence=0,
            defaults=defaults,
        )
        if created:
            Activity.objects.create(
                actor=request.user,
                verb="list_item_added",
                target_type="list",
                target_id=custom_list.id,
                item=item,
                visibility=custom_list.visibility,
                snapshot={"list_name": custom_list.name},
            )
        return Response({"item": media_summary_from_item(item, request=request, user=request.user)}, status=status.HTTP_201_CREATED)


class ListItemDetailView(APIView):
    """Remove an item from a list."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, list_id, item_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        _require_list_type(custom_list, CustomList.ListType.MEDIA)
        item = get_object_or_404(custom_list.items, id=item_id)
        exposure.require_media_type(item.media_type)
        deleted, _ = CustomListItem.objects.filter(custom_list=custom_list, item_id=item_id).delete()
        if deleted and custom_list.is_ranked:
            _renumber_list_items(custom_list)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListItemsReorderView(APIView):
    """Reorder list items."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        _require_list_type(custom_list, CustomList.ListType.MEDIA)
        serializer = ListItemsReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item_ids = serializer.validated_data["item_ids"]
        current_ids = list(CustomListItem.objects.filter(custom_list=custom_list).values_list("item_id", flat=True))
        if len(item_ids) != len(current_ids) or set(item_ids) != set(current_ids):
            return Response(
                {"item_ids": ["Must include exactly all items currently in the list."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        list_items = {
            list_item.item_id: list_item
            for list_item in CustomListItem.objects.filter(custom_list=custom_list)
        }
        for index, item_id in enumerate(item_ids, start=1):
            list_items[item_id].position = index
        CustomListItem.objects.bulk_update(list_items.values(), ["position"])
        return Response(list_payload(custom_list, request=request, include_items=True))


class ListPeopleView(APIView):
    """List or add provider-backed people in a people list."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=ListPeoplePageSerializer)
    def get(self, request, list_id):
        custom_list = get_object_or_404(
            CustomList.objects.select_related("owner").prefetch_related(
                "collaborators",
            ),
            id=list_id,
        )
        if not _can_view_list(request.user, custom_list):
            return Response(status=status.HTTP_404_NOT_FOUND)
        _require_list_type(custom_list, CustomList.ListType.PEOPLE)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(
            custom_list.person_items.all(),
            request,
            view=self,
        )
        list_people = list(page)
        return paginator.get_paginated_response(
            _with_person_notes(
                _person_list_payloads(list_people, request),
                list_people,
                featured_entry_notes(custom_list),
            ),
        )

    @extend_schema(
        request=ListPersonWriteSerializer,
        responses={
            status.HTTP_200_OK: ListPersonWriteResponseSerializer,
            status.HTTP_201_CREATED: ListPersonWriteResponseSerializer,
        },
    )
    def post(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        _require_list_type(custom_list, CustomList.ListType.PEOPLE)

        serializer = ListPersonWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ref = serializer.validated_data["ref"]
        existing = PersonListItem.objects.filter(
            custom_list=custom_list,
            source=ref["source"],
            person_id=ref["id"],
        ).first()
        if existing is not None:
            return Response({
                "created": False,
                "person": person_list_item_payload(existing, request=request),
            })

        snapshot = _person_snapshot(ref)
        with transaction.atomic():
            locked_list = CustomList.objects.select_for_update().get(
                id=custom_list.id,
            )
            defaults = {
                "name": snapshot["name"],
                "profile_url": snapshot["profile_url"],
                "known_for_department": snapshot["known_for_department"],
            }
            if locked_list.is_ranked:
                max_position = (
                    PersonListItem.objects.filter(
                        custom_list=locked_list,
                    ).aggregate(Max("position"))["position__max"]
                    or 0
                )
                defaults["position"] = max_position + 1
            list_person, created = PersonListItem.objects.get_or_create(
                custom_list=locked_list,
                source=snapshot["source"],
                person_id=snapshot["person_id"],
                defaults=defaults,
            )
            if created:
                person_snapshot = {
                    key: value
                    for key, value in person_list_item_payload(
                        list_person,
                        request=request,
                    ).items()
                    if key
                    in {
                        "id",
                        "source",
                        "name",
                        "profile_url",
                        "known_for_department",
                    }
                }
                Activity.objects.create(
                    actor=request.user,
                    verb="list_item_added",
                    target_type=ContentLike.CUSTOM_LIST,
                    target_id=locked_list.id,
                    visibility=locked_list.visibility,
                    snapshot={
                        "name": locked_list.name,
                        "list_name": locked_list.name,
                        "list_type": locked_list.list_type,
                        "entry_type": "person",
                        "person": person_snapshot,
                    },
                )
                _touch_list(locked_list)

        return Response(
            {
                "created": created,
                "person": person_list_item_payload(
                    list_person,
                    request=request,
                ),
            },
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )


class ListPersonDetailView(APIView):
    """Remove a person membership from a people list."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={status.HTTP_204_NO_CONTENT: None})
    def delete(self, request, list_id, entry_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        _require_list_type(custom_list, CustomList.ListType.PEOPLE)

        with transaction.atomic():
            locked_list = CustomList.objects.select_for_update().get(
                id=custom_list.id,
            )
            list_person = get_object_or_404(
                PersonListItem,
                id=entry_id,
                custom_list=locked_list,
            )
            list_person.delete()
            if locked_list.is_ranked:
                _renumber_list_items(locked_list)
            _touch_list(locked_list)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListPeopleReorderView(APIView):
    """Reorder all memberships in a people list."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ListPeopleReorderSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def patch(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id)
        if not custom_list.user_can_edit(request.user):
            return Response(status=status.HTTP_403_FORBIDDEN)
        _require_list_type(custom_list, CustomList.ListType.PEOPLE)

        serializer = ListPeopleReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry_ids = serializer.validated_data["entry_ids"]
        with transaction.atomic():
            locked_list = CustomList.objects.select_for_update().get(
                id=custom_list.id,
            )
            list_people = {
                list_person.id: list_person
                for list_person in PersonListItem.objects.select_for_update().filter(
                    custom_list=locked_list,
                )
            }
            if (
                len(entry_ids) != len(list_people)
                or set(entry_ids) != set(list_people)
            ):
                raise ValidationError({
                    "entry_ids": [
                        "Must include exactly all people currently in the list.",
                    ],
                })
            for index, entry_id in enumerate(entry_ids, start=1):
                list_people[entry_id].position = index
            PersonListItem.objects.bulk_update(
                list_people.values(),
                ["position"],
            )
            _touch_list(locked_list)

        return Response(
            list_payload(locked_list, request=request, include_items=True),
        )


class ListCollaboratorsView(APIView):
    """Add a collaborator."""

    permission_classes = [IsAuthenticated]

    def post(self, request, list_id):
        custom_list = get_object_or_404(CustomList, id=list_id, owner=request.user)
        serializer = CollaboratorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_object_or_404(get_user_model(), username=serializer.validated_data["username"])
        custom_list.collaborators.add(user)
        return Response(list_payload(custom_list, request=request, include_items=True))


class ListCollaboratorDetailView(APIView):
    """Remove a collaborator."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, list_id, user_id):
        custom_list = get_object_or_404(CustomList, id=list_id, owner=request.user)
        custom_list.collaborators.remove(user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListLikeView(APIView):
    """Like/unlike a list."""

    permission_classes = [IsAuthenticated]

    def post(self, request, list_id):
        return Response(set_like(request.user, target_type=ContentLike.CUSTOM_LIST, target_id=list_id, liked=True))

    def delete(self, request, list_id):
        return Response(set_like(request.user, target_type=ContentLike.CUSTOM_LIST, target_id=list_id, liked=False))
