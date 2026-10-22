"""Guarded, atomic writes of a resolved featured list, plus snapshots.

``prepare`` fills in missing titles and posters for Items that don't exist yet (the
only network step), ``check_guards`` refuses suspicious results, and ``commit`` does
everything else in one transaction.

Entries deferred by a provider's budget or rate limit (``Resolution.deferred``) don't
count toward the resolve rate. A run with deferred entries writes only when the list
wouldn't shrink (``writes_despite_deferrals``), and is retried at ``deferred_until``.
"""

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, replace
from functools import partial

from django.conf import settings
from django.db import transaction

from app.external_ratings import eligible_rating_sources
from app.mixins import collect_external_rating_item_id, collect_external_rating_item_ids
from app.models import Item, MediaTypes
from app.providers import quota, services
from lists.featured import ratings
from lists.featured.helpers import sanitize
from lists.featured.resolve import PROVIDER_ERRORS, Resolution, deferred_resolution
from lists.featured.types import GuardError
from lists.models import (
    CustomList,
    CustomListItem,
    FeaturedListSnapshot,
    FeaturedListSync,
    PersonListItem,
)

logger = logging.getLogger(__name__)

MAX_UNRESOLVED_ROWS = 50
ITEM_IMAGE_MAX_LENGTH = 200  # Item.image is a default URLField
MIN_RELEASE_YEAR, MAX_RELEASE_YEAR = 1800, 2200
UNCHANGED_NOTE = "the existing list was not changed."


@dataclass(frozen=True)
class WriteResult:
    """What ``commit`` stored."""

    status: str  # ok | unchanged
    custom_list: CustomList
    stored_count: int
    resolved_count: int
    changed: bool


def countable(resolutions):
    """Return the resolutions that count toward resolve rates (overrides may skip)."""
    return [resolution for resolution in resolutions if not resolution.skipped]


def deferred(resolutions):
    """Return the resolutions waiting for a provider's budget or rate limit."""
    return [resolution for resolution in resolutions if resolution.deferred]


def retry_at(resolutions):
    """Return when every deferred entry may be retried, or None."""
    times = [resolution.retry_at for resolution in deferred(resolutions) if resolution.retry_at]
    return max(times) if times else None


def writes_despite_deferrals(definition, resolutions, previous_count):
    """Return whether a run with deferred entries should still write the list.

    Only when the list wouldn't shrink: a new list appears with what resolved, and a
    growing one keeps its progress, but a current list is never cut short for a day.
    """
    stored = len(ordered_matches(definition, resolutions))
    return stored > 0 and stored >= previous_count


def unresolved_rows(resolutions, limit=MAX_UNRESOLVED_ROWS):
    """Return health-report rows for unresolved entries."""
    return [
        {
            "rank": resolution.entry.rank,
            "key": resolution.entry.key,
            "title": resolution.entry.title,
            "year": resolution.entry.year,
            "creator": resolution.entry.creator,
            "reason": resolution.reason,
        }
        for resolution in countable(resolutions)
        if resolution.match is None
    ][:limit]


def row_identity(definition, entry, match):
    """Return what makes two resolved entries one row.

    The matched item; on lists that keep repeats (award histories), the item and the
    entry's note, so each year's win is its own row.
    """
    return (match.ref, entry.note) if definition.keeps_repeats else match.ref


def ordered_matches(definition, resolutions):
    """Return ``[(position, entry, match)]``: resolved, de-duplicated, in list order."""
    rows, seen = [], set()
    for resolution in resolutions:
        match = resolution.match
        if match is None or resolution.skipped:
            continue
        identity = row_identity(definition, resolution.entry, match)
        if identity in seen:
            continue
        seen.add(identity)
        rows.append((resolution.entry, match))
    return [
        (
            entry.rank if definition.is_ranked and entry.rank is not None else index,
            entry,
            match,
        )
        for index, (entry, match) in enumerate(rows, start=1)
    ]


def fingerprint(rows):
    """Return a hash of what the list will hold: positions and refs, in order."""
    payload = [[position, match.ref] for position, _, match in rows]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _item_key(match):
    return (match.source, match.media_type, str(match.media_id))


def _existing_items(matches):
    groups = defaultdict(set)
    for match in matches:
        groups[(match.source, match.media_type)].add(str(match.media_id))
    found = {}
    for (source, media_type), media_ids in groups.items():
        for item in Item.objects.filter(
            source=source,
            media_type=media_type,
            media_id__in=media_ids,
            season_number__isnull=True,
            episode_number__isnull=True,
        ):
            found[(source, media_type, item.media_id)] = item
    return found


def _metadata_match(match):
    metadata = services.get_media_metadata(
        match.media_type,
        match.media_id,
        match.source,
        include_music_enrichment=False,
    )
    return replace(
        match,
        title=match.title or metadata.get("title") or metadata.get("name") or "",
        image=match.image or metadata.get("image") or settings.IMG_NONE,
        total_pages=match.total_pages or metadata.get("max_progress") or metadata.get("total_pages"),
    )


def prepare(definition, resolutions):
    """Fetch title and poster for new Items whose entry lacked them.

    A failed lookup makes that entry unresolved instead of creating an Item with
    placeholder art.
    """
    if definition.list_type == "people":
        return list(resolutions)
    needed = [
        resolution.match
        for resolution in resolutions
        if resolution.match is not None and not (resolution.match.title and resolution.match.image)
    ]
    if not needed:
        return list(resolutions)
    existing = _existing_items(needed)
    filled, failed, waiting = {}, {}, {}
    for match in needed:
        key = _item_key(match)
        if key in existing or key in filled or key in failed or key in waiting:
            continue
        try:
            filled[key] = _metadata_match(match)
        except quota.Deferred as error:
            waiting[key] = error
        except PROVIDER_ERRORS as error:
            failed[key] = f"metadata lookup failed: {sanitize(error, 160)}"
    prepared = []
    for resolution in resolutions:
        match = resolution.match
        key = _item_key(match) if match is not None else None
        if key in waiting:
            prepared.append(deferred_resolution(resolution.entry, waiting[key]))
        elif key in failed:
            prepared.append(Resolution(resolution.entry, None, "", failed[key]))
        elif key in filled:
            prepared.append(replace(resolution, match=filled[key]))
        else:
            prepared.append(resolution)
    return prepared


def current_count(custom_list):
    """Return how many entries a list holds now."""
    if custom_list is None:
        return 0
    if custom_list.list_type == CustomList.ListType.PEOPLE:
        return custom_list.person_items.count()
    return custom_list.customlistitem_set.count()


def find_list(definition, owner):
    """Return the existing list for a definition's identity, or None."""
    source, source_id = definition.identity
    return CustomList.objects.filter(
        owner=owner,
        import_source=source,
        import_source_id=source_id,
    ).first()


def check_guards(definition, fetch, resolutions, previous_count):
    """Raise ``GuardError`` when the result must not replace the existing list."""
    guards = definition.guards
    if not fetch.entries:
        raise GuardError(
            "empty",
            f"The source returned no entries; {UNCHANGED_NOTE}",
            status=FeaturedListSync.Status.EMPTY,
        )
    if (
        guards.require_exact_count
        and fetch.expected_count
        and len(fetch.entries) != fetch.expected_count
    ):
        raise GuardError(
            "exact_count",
            f"The source reported {fetch.expected_count} entries but returned "
            f"{len(fetch.entries)}; {UNCHANGED_NOTE}",
            expected=fetch.expected_count,
            actual=len(fetch.entries),
        )
    waiting = deferred(resolutions)
    counted = [resolution for resolution in countable(resolutions) if not resolution.deferred]
    resolved = [resolution for resolution in counted if resolution.match is not None]
    if not resolved and waiting:
        return  # nothing to judge yet; the engine records the deferral
    if not resolved:
        raise GuardError(
            "zero_resolved",
            f"None of {len(counted)} entries resolved; {UNCHANGED_NOTE}",
            total=len(counted),
        )
    rate = len(resolved) / len(counted)
    if rate < guards.min_resolve_rate:
        raise GuardError(
            "resolve_rate",
            f"Only {len(resolved)} of {len(counted)} entries resolved ({rate:.0%}, "
            f"minimum {guards.min_resolve_rate:.0%}); {UNCHANGED_NOTE}",
            resolved=len(resolved),
            total=len(counted),
        )
    stored = len(ordered_matches(definition, resolutions))
    if (
        not waiting  # with deferrals the engine writes only when the list doesn't shrink
        and previous_count
        and previous_count >= guards.drop_check_min_previous
        and stored < previous_count * (1 - guards.max_drop)
    ):
        raise GuardError(
            "drop",
            f"The list would shrink from {previous_count} to {stored} entries "
            f"(more than {guards.max_drop:.0%}); {UNCHANGED_NOTE}",
            previous=previous_count,
            stored=stored,
        )


def _safe_year(year):
    return year if isinstance(year, int) and MIN_RELEASE_YEAR <= year <= MAX_RELEASE_YEAR else None


def _new_item(entry, match):
    image = match.image or entry.image or settings.IMG_NONE
    if len(image) > ITEM_IMAGE_MAX_LENGTH:
        image = settings.IMG_NONE
    return Item(
        source=match.source,
        media_type=match.media_type,
        media_id=str(match.media_id),
        title=match.title or entry.title or str(match.media_id),
        image=image,
        release_year=_safe_year(match.year or entry.year),
        total_pages=match.total_pages if match.media_type == MediaTypes.BOOK.value else None,
    )


def ensure_items(rows):
    """Return ``{ref: Item}``, creating missing Items in bulk.

    Call inside ``collect_external_rating_item_ids()``: new Items that have rating
    sources are collected (instead of one task each) for ``ratings.add_pending``.
    """
    matches = [match for _, _, match in rows]
    existing = _existing_items(matches)
    missing = {
        _item_key(match): _new_item(entry, match)
        for _, entry, match in rows
        if _item_key(match) not in existing
    }
    if missing:
        Item.objects.bulk_create(list(missing.values()), ignore_conflicts=True, batch_size=500)
        created = _existing_items([match for _, _, match in rows if _item_key(match) in missing])
        for item in created.values():
            if eligible_rating_sources(item):
                collect_external_rating_item_id(item.pk)
        existing.update(created)
    return {match.ref: existing[_item_key(match)] for match in matches if _item_key(match) in existing}


def movement_notes(previous_positions, rows, items):
    """Return ``{"<item_id>": "▲2" | "▼1" | "NEW"}`` for a chart's new content.

    ``previous_positions`` maps item IDs to their positions before this write; with
    no previous content there are no notes. Unchanged positions get no note.
    """
    if not previous_positions:
        return {}
    notes = {}
    for position, _, match in rows:
        item = items.get(match.ref)
        if item is None:
            continue
        before = previous_positions.get(item.pk)
        if before is None:
            notes[str(item.pk)] = "NEW"
        elif before != position:
            change = before - position
            notes[str(item.pk)] = f"▲{change}" if change > 0 else f"▼{-change}"
    return notes


MAX_NOTE_LENGTH = 200
_MOVEMENT_PREFIXES = ("▲", "▼", "new")


def combine_notes(movement, note):
    """Return what a row shows: Spine's chart movement and the source's own note.

    ``"▲2"`` and ``"$12.4M"`` become ``"▲2 · $12.4M"``. A note that already says how
    the entry moved ("▲2", "New this week") is kept on its own.
    """
    if not movement or not note:
        return movement or note
    if note.casefold().startswith(_MOVEMENT_PREFIXES):
        return note
    return f"{movement} · {note}"


def entry_notes(resolutions, key_for, movement=None):
    """Return ``{key: note}`` for every stored entry with a note or a movement.

    ``key_for(match)`` names the row (an item ID, or a person ref) or returns None. An
    item listed by several entries (one nominee in two categories) joins their notes.
    The API reads people lists' notes from here and media rows' from
    ``CustomListItem.note``; media lists keep this per-item copy for older code.
    """
    movement = movement or {}
    collected = {}
    for resolution in resolutions:
        match = resolution.match
        if match is None or resolution.skipped:
            continue
        key = key_for(match)
        if key is None:
            continue
        notes = collected.setdefault(key, [])
        if resolution.entry.note and resolution.entry.note not in notes:
            notes.append(resolution.entry.note)
    combined = {}
    for key, notes in collected.items():
        note = combine_notes(movement.get(key, ""), "; ".join(notes))[:MAX_NOTE_LENGTH]
        if note:
            combined[key] = note
    return combined


def row_notes(definition, resolutions, rows, items, movement=None):
    """Return each row's note, in ``rows`` order: its entries' notes after the chart movement.

    Entries that make one row (an item listed twice in a ceremony) join their notes
    with "; "; on lists that keep repeats each year's row has its own note ("2023").
    """
    movement = movement or {}
    collected = {}
    for resolution in resolutions:
        match = resolution.match
        if match is None or resolution.skipped:
            continue
        notes = collected.setdefault(row_identity(definition, resolution.entry, match), [])
        if resolution.entry.note and resolution.entry.note not in notes:
            notes.append(resolution.entry.note)
    found = []
    for _, entry, match in rows:
        item = items.get(match.ref)
        moved = movement.get(str(item.pk), "") if item is not None else ""
        note = "; ".join(collected.get(row_identity(definition, entry, match), []))
        found.append(combine_notes(moved, note)[:MAX_NOTE_LENGTH])
    return found


def _stored_items(rows):
    """Return ``{ref: Item}`` for rows whose Items already exist."""
    existing = _existing_items([match for _, _, match in rows])
    return {match.ref: existing[_item_key(match)] for _, _, match in rows if _item_key(match) in existing}


def _note_keys(people, rows, items):
    """Return ``key_for(match)`` for ``entry_notes``: person refs, or item IDs."""
    if people:
        return lambda match: match.ref
    if not items:  # content unchanged: the Items already exist
        items = _stored_items(rows)
    return lambda match: str(items[match.ref].pk) if match.ref in items else None


def media_rows(custom_list, rows, items, notes):
    """Return unsaved ``CustomListItem`` rows; an item's repeats get occurrences 0, 1, 2…"""
    occurrences = defaultdict(int)
    planned = []
    for (position, _, match), note in zip(rows, notes, strict=True):
        item = items.get(match.ref)
        if item is None:
            continue
        planned.append(
            CustomListItem(
                custom_list=custom_list,
                item=item,
                position=position,
                occurrence=occurrences[item.pk],
                note=note,
            ),
        )
        occurrences[item.pk] += 1
    return planned


def _replace_media(custom_list, planned):
    CustomListItem.objects.filter(custom_list=custom_list).delete()
    CustomListItem.objects.bulk_create(planned, batch_size=500)


def _update_media_notes(custom_list, planned):
    """Rewrite the stored rows' notes; the content is unchanged, so the rows match."""
    wanted = {(row.item_id, row.occurrence): row.note for row in planned}
    stale = []
    for stored in CustomListItem.objects.filter(custom_list=custom_list).only("id", "item_id", "occurrence", "note"):
        note = wanted.get((stored.item_id, stored.occurrence), "")
        if stored.note != note:
            stored.note = note
            stale.append(stored)
    CustomListItem.objects.bulk_update(stale, ["note"], batch_size=500)


def _replace_people(custom_list, rows):
    PersonListItem.objects.filter(custom_list=custom_list).delete()
    PersonListItem.objects.bulk_create(
        [
            PersonListItem(
                custom_list=custom_list,
                source=match.source,
                person_id=str(match.media_id)[:255],
                name=(match.title or entry.title or str(match.media_id))[:255],
                profile_url=(match.image or entry.image)[:2048],
                known_for_department=match.known_for_department[:255],
                position=position,
            )
            for position, entry, match in rows
        ],
        batch_size=500,
    )


def _save_list(definition, owner, fetch, now):
    source, source_id = definition.identity
    custom_list, created = CustomList.objects.get_or_create(
        owner=owner,
        import_source=source,
        import_source_id=source_id,
        defaults={
            "name": definition.name or fetch.name or definition.key,
            "slug": definition.key,
            "list_type": definition.list_type,
        },
    )
    if custom_list.list_type != definition.list_type:
        msg = f"List {custom_list.pk} is a {custom_list.list_type} list, not {definition.list_type}."
        raise ValueError(msg)
    custom_list.name = definition.name or fetch.name or custom_list.name
    custom_list.description = fetch.description or definition.description or custom_list.description
    if definition.tags:
        custom_list.tags = list(definition.tags)
    custom_list.visibility = CustomList.Visibility.PUBLIC
    custom_list.is_ranked = definition.is_ranked
    custom_list.is_featured = definition.featured
    custom_list.featured_position = max(definition.position, 0)
    if definition.source_url:
        custom_list.import_source_url = definition.source_url
    custom_list.last_synced_at = now
    custom_list.save()
    return custom_list, created


def commit(definition, owner, fetch, resolutions, *, sync, now, definition_hash):
    """Write the list, its sync state and today's snapshot in one transaction."""
    rows = ordered_matches(definition, resolutions)
    new_fingerprint = fingerprint(rows)
    people = definition.list_type == "people"
    counted = countable(resolutions)
    with transaction.atomic():
        custom_list, created = _save_list(definition, owner, fetch, now)
        changed = created or new_fingerprint != sync.fingerprint or sync.custom_list_id != custom_list.pk
        items = {}
        if changed:
            sync.item_notes = {}
            if people:
                _replace_people(custom_list, rows)
            else:
                with collect_external_rating_item_ids() as new_item_ids:
                    items = ensure_items(rows)
                if definition.kind == "chart" and definition.is_ranked and not created:
                    previous = dict(
                        CustomListItem.objects.filter(custom_list=custom_list).values_list("item_id", "position"),
                    )
                    sync.item_notes = movement_notes(previous, rows, items)
                notes = row_notes(definition, resolutions, rows, items, sync.item_notes)
                _replace_media(custom_list, media_rows(custom_list, rows, items, notes))
                if new_item_ids:  # trickled onto the default worker by the dispatcher
                    transaction.on_commit(partial(ratings.add_pending, sorted(new_item_ids)), robust=True)
        elif not people:  # same rows; a source note ("$12.4M") may still have changed
            items = _stored_items(rows)
            notes = row_notes(definition, resolutions, rows, items, sync.item_notes)
            _update_media_notes(custom_list, media_rows(custom_list, rows, items, notes))

        sync.entry_notes = entry_notes(resolutions, _note_keys(people, rows, items), sync.item_notes)

        FeaturedListSync.objects.filter(custom_list=custom_list).exclude(pk=sync.pk).update(custom_list=None)
        sync.custom_list = custom_list
        sync.definition_hash = definition_hash
        sync.edition = fetch.edition[:255]
        sync.live_label = fetch.live_label[:255]
        if changed:
            sync.fingerprint = new_fingerprint
            sync.content_changed_at = now
        sync.last_success_at = now
        sync.last_status = FeaturedListSync.Status.OK if changed else FeaturedListSync.Status.UNCHANGED
        sync.deferred_until = retry_at(resolutions)
        sync.last_error = ""
        if sync.deferred_until is not None:
            sync.last_status = FeaturedListSync.Status.DEFERRED
            sync.last_error = sanitize(deferred(resolutions)[0].reason)
        sync.consecutive_failures = 0
        sync.entry_count = len(counted)
        sync.resolved_count = sum(1 for resolution in counted if resolution.match is not None)
        sync.unresolved = unresolved_rows(resolutions)
        sync.save()

        if changed:
            FeaturedListSnapshot.objects.update_or_create(
                sync=sync,
                taken_on=now.date(),
                defaults={
                    "edition": fetch.edition[:255],
                    "fingerprint": new_fingerprint,
                    "entries": [
                        [
                            resolution.entry.rank,
                            resolution.entry.key,
                            items[resolution.match.ref].pk
                            if resolution.match is not None and resolution.match.ref in items
                            else None,
                        ]
                        for resolution in counted
                    ],
                },
            )

    return WriteResult(
        status=sync.last_status,
        custom_list=custom_list,
        stored_count=len(rows),
        resolved_count=sync.resolved_count,
        changed=changed,
    )
