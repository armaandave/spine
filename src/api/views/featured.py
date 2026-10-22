"""Featured-list discovery: the legacy flat endpoint, shelves, and the health report.

Every response here runs a fixed number of queries however many lists are featured.
"""

import hmac
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, F, Window
from django.db.models.functions import RowNumber
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.common import (
    image_url,
    media_summary_from_item,
    prime_collection_items,
    user_summary,
)
from api.views.lists import (
    LIST_PREVIEW_ITEM_LIMIT,
    _list_type_query,
    _person_list_payloads,
    person_list_item_payload,
)
from app import exposure
from app.models import MediaTypes
from app.providers import tmdb
from lists.featured import health, order, registry
from lists.models import CustomList, CustomListItem, PersonListItem
from social.models import ContentLike

CARD_PREVIEW_LIMIT = 5
NOW_LIMIT = 10
SHELF_LIMIT = 12
RECENT_WINDOW = timedelta(days=7)
CARD_POSTER_SIZE = "w342"
SHELF_MEDIA_TYPES = ("movie", "tv", "anime", "manga", "game", "book", "comic", "music")
SHELF_TITLES = {
    "now": "Now",
    "movie": "Movies",
    "tv": "TV",
    "anime": "Anime",
    "manga": "Manga",
    "game": "Games",
    "book": "Books",
    "comic": "Comics",
    "music": "Music",
    "mixed": "Across Media",
}
KIND_TITLES = {
    "chart": "Charts",
    "award": "Awards",
    "canon": "Critics & Canon",
    "community": "Community",
    "curator": "Curators' Picks",
    "editorial": "Editorial",
}
MIXED = "mixed"
MAX_SPECIFIC_TYPES = 2  # lists with more media types go to "Across Media"
SHELVES_PATH = "/api/v1/lists/featured/shelves/"
TV_CHILD_TYPES = {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}


@dataclass
class Featured:
    """A featured list joined with its registry definition and sync state."""

    custom_list: CustomList
    definition: object
    sync: object
    media_types: tuple
    kind: str
    live_status: str | None
    live_window: object
    recent: bool

    @property
    def position(self):
        return self.custom_list.featured_position

    @property
    def changed_at(self):
        return self.sync.content_changed_at if self.sync else None


def _sync_for(custom_list):
    try:
        return custom_list.featured_sync
    except ObjectDoesNotExist:
        return None


def definition_for(custom_list):
    """Return ``(definition, sync)`` for a featured list; either may be None."""
    sync = _sync_for(custom_list)
    definition = registry.get(sync.key) if sync else None
    if definition is None and custom_list.import_source_id:
        definition = registry.by_identity(custom_list.import_source, custom_list.import_source_id)
    return definition, sync


def featured_lists():
    """Return public featured lists with owner, collaborators and sync state loaded."""
    return (
        CustomList.objects.filter(
            is_featured=True,
            visibility=CustomList.Visibility.PUBLIC,
        )
        .select_related("owner", "featured_sync")
        .prefetch_related("collaborators")
        .order_by("featured_position", "id")
    )


def _grouped_counts(queryset, field):
    return dict(
        queryset.order_by().values(field).annotate(total=Count("id")).values_list(field, "total"),
    )


def _row_window(partition):
    return Window(
        RowNumber(),
        partition_by=[F(partition)],
        order_by=[F("position").asc(nulls_last=True), F("date_added").asc(), F("id").asc()],
    )


def preview_items(list_ids, limit):
    """Return ``{list_id: [CustomListItem]}``: the first exposed items of each list."""
    rows = (
        CustomListItem.objects.filter(
            custom_list_id__in=list_ids,
            item__media_type__in=exposure.media_types(),
        )
        .select_related("item")
        .annotate(preview_row=_row_window("custom_list_id"))
        .filter(preview_row__lte=limit)
        .order_by("custom_list_id", "preview_row")
    )
    found = {}
    for row in rows:
        found.setdefault(row.custom_list_id, []).append(row)
    return found


def _preview_people(list_ids, limit):
    rows = (
        PersonListItem.objects.filter(custom_list_id__in=list_ids)
        .annotate(preview_row=_row_window("custom_list_id"))
        .filter(preview_row__lte=limit)
        .order_by("custom_list_id", "preview_row")
    )
    found = {}
    for row in rows:
        found.setdefault(row.custom_list_id, []).append(row)
    return found


def _first_images(list_ids):
    return dict(
        CustomListItem.objects.filter(custom_list_id__in=list_ids)
        .annotate(first_row=_row_window("custom_list_id"))
        .filter(first_row=1)
        .values_list("custom_list_id", "item__image"),
    )


def _summary(list_item, request, *, poster_size=None):
    summary = media_summary_from_item(
        list_item.item,
        request=request,
        user=request.user,
        include_user_state=False,
    )
    if poster_size:
        for key in ("image_url", "poster_url"):
            summary[key] = tmdb.sized_image_url(summary[key], poster_size)
    summary["position"] = list_item.position
    return summary


def list_payloads(lists, request):
    """Serialize lists like ``list_payload(include_preview_items=True)``, batched."""
    lists = list(lists)
    media_ids = [item.id for item in lists if item.list_type != CustomList.ListType.PEOPLE]
    people_ids = [item.id for item in lists if item.list_type == CustomList.ListType.PEOPLE]
    item_counts = _grouped_counts(
        CustomListItem.objects.filter(
            custom_list_id__in=media_ids,
            item__media_type__in=exposure.media_types(),
        ),
        "custom_list_id",
    )
    person_counts = _grouped_counts(
        PersonListItem.objects.filter(custom_list_id__in=people_ids),
        "custom_list_id",
    )
    likes = _grouped_counts(
        ContentLike.objects.filter(
            target_type=ContentLike.CUSTOM_LIST,
            target_id__in=[item.id for item in lists],
        ),
        "target_id",
    )
    previews = preview_items(media_ids, LIST_PREVIEW_ITEM_LIMIT)
    people = _preview_people(people_ids, LIST_PREVIEW_ITEM_LIMIT)
    first_images = _first_images(media_ids)
    prime_collection_items(
        [row.item for rows in previews.values() for row in rows],
        request.user,
    )
    payloads = []
    for custom_list in lists:
        is_people = custom_list.list_type == CustomList.ListType.PEOPLE
        items_count = 0 if is_people else item_counts.get(custom_list.id, 0)
        entries_count = person_counts.get(custom_list.id, 0) if is_people else items_count
        list_people = people.get(custom_list.id, [])
        image = (
            (list_people[0].profile_url if list_people and list_people[0].profile_url else settings.IMG_NONE)
            if is_people
            else first_images.get(custom_list.id) or settings.IMG_NONE
        )
        payloads.append({
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
            "image_url": image_url(request, image),
            "items_count": items_count,
            "people_count": entries_count if is_people else 0,
            "entries_count": entries_count,
            "updated_at": custom_list.updated_at,
            "like_count": likes.get(custom_list.id, 0),
            "preview_items": [
                _summary(row, request) for row in previews.get(custom_list.id, [])
            ] if not is_people else [],
            "preview_people": _person_list_payloads(list_people, request) if is_people else [],
        })
    return payloads


def _manual_media_types(lists):
    rows = (
        CustomListItem.objects.filter(custom_list_id__in=[item.id for item in lists])
        .order_by()
        .values_list("custom_list_id", "item__media_type")
        .distinct()
    )
    found = {}
    for list_id, media_type in rows:
        value = MediaTypes.TV.value if media_type in TV_CHILD_TYPES else media_type
        found.setdefault(list_id, set()).add(value)
    return {
        list_id: tuple(value for value in SHELF_MEDIA_TYPES if value in values)
        for list_id, values in found.items()
    }


def featured_cards(now, lists=None):
    """Return featured lists as ``Featured``, keeping only exposed media types.

    Lists whose media types are all hidden are dropped.
    """
    exposed = set(exposure.media_types())
    lists = list(featured_lists() if lists is None else lists)
    joined = [(custom_list, *definition_for(custom_list)) for custom_list in lists]
    manual = _manual_media_types([row[0] for row in joined if row[1] is None])
    cards = []
    for custom_list, definition, sync in joined:
        if definition is not None:
            media_types = definition.media_types
            kind = definition.kind
            window = definition.live_window_at(now)
        else:
            media_types = manual.get(custom_list.id, ())
            kind = "editorial"
            window = None
        media_types = tuple(value for value in media_types if value in exposed)
        if not media_types:
            continue
        changed_at = sync.content_changed_at if sync else None
        cards.append(
            Featured(
                custom_list=custom_list,
                definition=definition,
                sync=sync,
                media_types=media_types,
                kind=kind,
                live_status=window.status(now) if window else None,
                live_window=window,
                recent=bool(changed_at and now - changed_at <= RECENT_WINDOW),
            ),
        )
    return cards


def card_payload(featured, request, *, items_count, previews=None, people=None, now=None):
    """Return the card shape; ``previews=None`` omits ``preview_items``.

    People lists also get ``preview_people`` from ``people`` alongside the
    empty ``preview_items``.
    """
    custom_list, definition, sync = featured.custom_list, featured.definition, featured.sync
    window = featured.live_window
    payload = {
        "id": custom_list.id,
        "name": custom_list.name,
        "slug": custom_list.slug,
        "description": custom_list.description,
        "is_ranked": custom_list.is_ranked,
        "items_count": items_count,
        "media_types": list(featured.media_types),
        "kind": featured.kind,
        "source": {
            "name": (definition.source_name if definition else "") or None,
            "url": (definition.source_url if definition else "") or custom_list.import_source_url or None,
        },
        "cadence": registry.cadence_label(definition) if definition else "static",
        "period_label": (sync.edition if sync else "") or None,
        "content_changed_at": featured.changed_at,
        "last_synced_at": custom_list.last_synced_at,
        "live": {
            "status": window.status(now or timezone.now()),
            "label": (sync.live_label if sync else "") or window.label or None,
            "starts_at": window.starts_at,
            "ends_at": window.ends_at,
        } if window else None,
    }
    if previews is not None:
        notes = (sync.item_notes or {}) if sync and featured.kind == "chart" else {}
        payload["preview_items"] = [
            {
                **_summary(row, request, poster_size=CARD_POSTER_SIZE),
                "note": notes.get(str(row.item_id)),
            }
            for row in previews
        ]
        if custom_list.list_type == CustomList.ListType.PEOPLE:
            payload["preview_people"] = [
                person_list_item_payload(row, request=request) for row in people or []
            ]
    return payload


def featured_block(custom_list, request, *, items_count):
    """Return the card fields (no previews) for one featured list, or None."""
    if not custom_list.is_featured or custom_list.visibility != CustomList.Visibility.PUBLIC:
        return None
    now = timezone.now()
    cards = featured_cards(now, [custom_list])
    if not cards:
        return None
    return card_payload(cards[0], request, items_count=items_count, now=now)


def _key(featured):
    return featured.definition.key if featured.definition else None


def _shelf_sorted(cards, media_type):
    """Sort a shelf: live, then the curated keys in order, then position and name."""
    curated = order.shelf_index(media_type)

    def sort_key(featured):
        rank = curated.get(_key(featured))
        return (
            featured.live_status != "live",
            rank is None,
            rank or 0,
            featured.position,
            featured.custom_list.name,
            featured.custom_list.id,
        )

    return sorted(cards, key=sort_key)


def _now_cards(cards):
    """Return "Now": lists near a live window, then recently changed priority charts.

    Charts follow ``order.NOW_PRIORITY``, at most ``order.NOW_PER_TYPE`` per media type.
    """
    chosen = sorted(
        (card for card in cards if card.live_status is not None),
        key=lambda card: (card.live_status != "live", card.position, card.custom_list.id),
    )
    priority = order.now_index()
    charts = sorted(
        (card for card in cards if card.live_status is None and card.recent and _key(card) in priority),
        key=lambda card: priority[_key(card)],
    )
    per_type = {}
    for card in charts:
        if len(chosen) >= NOW_LIMIT:
            break
        if any(per_type.get(media_type, 0) >= order.NOW_PER_TYPE for media_type in card.media_types):
            continue
        for media_type in card.media_types:
            per_type[media_type] = per_type.get(media_type, 0) + 1
        chosen.append(card)
    return chosen[:NOW_LIMIT]


def _shelf_types(featured):
    if len(featured.media_types) > MAX_SPECIFIC_TYPES:
        return (MIXED,)
    return featured.media_types


def _counts_for(cards):
    media = [card.custom_list.id for card in cards if card.custom_list.list_type != CustomList.ListType.PEOPLE]
    people = [card.custom_list.id for card in cards if card.custom_list.list_type == CustomList.ListType.PEOPLE]
    counts = _grouped_counts(
        CustomListItem.objects.filter(custom_list_id__in=media, item__media_type__in=exposure.media_types()),
        "custom_list_id",
    )
    counts.update(_grouped_counts(PersonListItem.objects.filter(custom_list_id__in=people), "custom_list_id"))
    return counts


class _CardRenderer:
    """Renders cards with one batched count query and a preview query per list type."""

    def __init__(self, request, cards, shown):
        self.request = request
        self.counts = _counts_for(cards)
        self.previews = preview_items([card.custom_list.id for card in shown], CARD_PREVIEW_LIMIT)
        self.people = _preview_people(
            [card.custom_list.id for card in shown if card.custom_list.list_type == CustomList.ListType.PEOPLE],
            CARD_PREVIEW_LIMIT,
        )
        prime_collection_items(
            [row.item for rows in self.previews.values() for row in rows],
            request.user,
        )
        self.now = timezone.now()

    def __call__(self, card, *, with_previews=True):
        return card_payload(
            card,
            self.request,
            items_count=self.counts.get(card.custom_list.id, 0),
            previews=self.previews.get(card.custom_list.id, []) if with_previews else None,
            people=self.people.get(card.custom_list.id, []),
            now=self.now,
        )


def _shelf(key, media_type, cards, limit, render, *, see_all=None, kind_counts=None):
    shown = cards[:limit] if limit else cards
    return {
        "key": key,
        "title": SHELF_TITLES.get(key) or KIND_TITLES.get(key, key.title()),
        "media_type": media_type,
        "count": len(cards),
        "live_count": sum(1 for card in cards if card.live_status == "live"),
        "kind_counts": kind_counts if kind_counts is not None else {},
        "lists": [render(card) for card in shown],
        "see_all": see_all,
    }


def _kind_counts(cards):
    counts = {}
    for kind in KIND_TITLES:
        total = sum(1 for card in cards if card.kind == kind)
        if total:
            counts[kind] = total
    return counts


def landing_shelves(request, cards, now):
    """Return the landing shelves: Now, one per media type, then Across Media."""
    now_cards = _now_cards(cards)
    by_type = {}
    for card in cards:
        for media_type in _shelf_types(card):
            by_type.setdefault(media_type, []).append(card)
    by_type = {media_type: _shelf_sorted(shelf, media_type) for media_type, shelf in by_type.items()}
    shelf_order = [value for value in (*SHELF_MEDIA_TYPES, MIXED) if by_type.get(value)]
    shown = {card.custom_list.id: card for card in now_cards}
    for media_type in shelf_order:
        shown.update({card.custom_list.id: card for card in by_type[media_type][:SHELF_LIMIT]})
    render = _CardRenderer(request, cards, list(shown.values()))
    shelves = []
    if now_cards:
        shelves.append(_shelf("now", None, now_cards, NOW_LIMIT, render))
    shelves.extend(
        _shelf(
            media_type,
            media_type,
            by_type[media_type],
            SHELF_LIMIT,
            render,
            see_all=f"{SHELVES_PATH}?media_type={media_type}",
            kind_counts=_kind_counts(by_type[media_type]),
        )
        for media_type in shelf_order
    )
    return shelves


def media_type_shelves(request, cards, media_type):
    """Return one shelf per kind for a media type (or ``mixed``), with every card."""
    selected = _shelf_sorted(
        (card for card in cards if media_type in _shelf_types(card)),
        media_type,
    )
    render = _CardRenderer(request, selected, selected)
    return [
        _shelf(kind, media_type, kind_cards, None, render)
        for kind in KIND_TITLES
        if (kind_cards := [card for card in selected if card.kind == kind])
    ]


class FeaturedListsView(APIView):
    """Return public lists selected for Search discovery (installed builds)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        list_type = _list_type_query(request)
        show_all = request.query_params.get("view") == "all"
        lists = featured_lists()
        if list_type != "all":
            lists = lists.filter(list_type=list_type)
        now = timezone.now()
        selected = []
        for custom_list in lists:
            definition, _ = definition_for(custom_list)
            if show_all or definition is None or definition.headline:
                selected.append(custom_list)
        payloads = list_payloads(selected, request)
        cards = {card.custom_list.id: card for card in featured_cards(now, selected)}
        for payload, custom_list in zip(payloads, selected, strict=True):
            card = cards.get(custom_list.id)
            payload["featured"] = (
                card_payload(card, request, items_count=payload["entries_count"], now=now)
                if card is not None
                else None
            )
        return Response(
            {
                "count": len(payloads),
                "next": None,
                "previous": None,
                "results": payloads,
            },
        )


class FeaturedShelvesView(APIView):
    """Return featured lists grouped into Search shelves."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        now = timezone.now()
        media_type = request.query_params.get("media_type")
        if media_type in (None, ""):
            return Response({
                "generated_at": now,
                "shelves": landing_shelves(request, featured_cards(now), now),
            })
        if media_type != MIXED:
            if media_type not in SHELF_MEDIA_TYPES:
                raise ValidationError({"media_type": [f"Use one of {', '.join((*SHELF_MEDIA_TYPES, MIXED))}."]})
            exposure.require_media_type(media_type)
        return Response({
            "generated_at": now,
            "media_type": media_type,
            "shelves": media_type_shelves(request, featured_cards(now), media_type),
        })


def may_read_health(request):
    """Return whether the request is from staff or carries the report token."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        return True
    token = settings.FEATURED_REPORT_TOKEN or ""
    supplied = request.headers.get("X-Spine-Report-Token") or ""
    return bool(token) and hmac.compare_digest(supplied.encode(), token.encode())


class FeaturedHealthView(APIView):
    """Return the featured-lists health report to staff or the report token."""

    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        if not may_read_health(request):
            return Response(status=404)
        return Response(health.report())
