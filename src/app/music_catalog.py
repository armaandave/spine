"""Glue between Spine's two music catalogs, Apple Music and MusicBrainz.

See docs/music/apple-music-catalog.md.

- ``canonical_music_ref`` and its batch form ``canonical_music_refs``: the ref an
  album is stored under, so an old MusicBrainz ref, an Apple alias and the album's
  own ref all reach one Item. They read the database only, never the network.
- ``apple_genre_names``: Apple's genre labels without the catch-all Music label, for albums whose
  MusicBrainz genres aren't known yet, so genre chips and library filters keep
  matching the MusicBrainz genre pages.
- ``genre_fill_rows`` and ``store_wikidata_apple_ids``: the link rows the genre link
  fill may write, and Wikidata's Apple Music IDs for it to check.

Don't import ``app.providers.apple_music`` here: it imports this module.
"""

from itertools import batched

from django.db.models import Exists, OuterRef, Q

from app import config
from app.models import AppleAlbum, Item, MediaTypes, MusicAlbumLink, Sources

MUSIC = MediaTypes.MUSIC.value
APPLE_MUSIC = Sources.APPLE_MUSIC.value
MUSICBRAINZ = Sources.MUSICBRAINZ.value
# Matches good enough to send a MusicBrainz ref to Apple Music before its album moves.
REDIRECT_TIERS = (1, 2)

# Apple files every album under "Music" as well, which says nothing.
APPLE_CATCH_ALL_GENRE = "music"
# Release groups per query when Wikidata's Apple Music IDs are stored.
WIKIDATA_STORE_BATCH = 500


def apple_genre_names(names):
    """Keep Apple's genre labels, omitting its catch-all Music label."""
    genres, seen = [], set()
    for name in names or []:
        value = " ".join(str(name or "").split())
        key = value.casefold()
        if key and key != APPLE_CATCH_ALL_GENRE and key not in seen:
            genres.append(value)
            seen.add(key)
    return genres


def canonical_music_ref(source, media_type, media_id):
    """Return the ref an album is stored under, as ``(source, media_type, media_id)``.

    Any other ref comes back as it is. The rules are ``canonical_music_refs``'.
    """
    ref = (source, media_type, str(media_id))
    return canonical_music_refs([ref])[ref]


def canonical_music_refs(refs):
    """Return ``{ref: canonical ref}`` for ``(source, media_type, media_id)`` refs.

    Two queries however many refs there are, and none without an album among them.
    Reads the database only, so run it before any cache read. The rules, in order:

    1. An Apple ref goes to its listing's identity (``AppleAlbum.canonical_id``), or
       to the MusicBrainz Item that identity was moved back to.
    2. A MusicBrainz ref whose album has moved goes to the Item that holds it now,
       even when a stray MusicBrainz Item with that ID exists.
    3. A MusicBrainz ref with its own Item stays.
    4. A MusicBrainz ref goes to an Apple Item first added from an Apple page, when
       the Apple album's bridge names the same release group and title.
    5. With the catalog on Apple Music, a MusicBrainz ref goes to its tier 1, tier 2
       or manual match.
    6. Anything else stays.
    """
    refs = {(source, media_type, str(media_id)) for source, media_type, media_id in refs}
    canonical = {ref: ref for ref in refs}
    apple_ids = {ref[2] for ref in refs if ref[:2] == (APPLE_MUSIC, MUSIC) and ref[2]}
    musicbrainz_ids = {ref[2] for ref in refs if ref[:2] == (MUSICBRAINZ, MUSIC) and ref[2]}
    if not apple_ids and not musicbrainz_ids:
        return canonical

    apple_catalog = config.music_catalog_source() == Sources.APPLE_MUSIC
    aliases, bridged, musicbrainz_held = _apple_albums(
        apple_ids,
        musicbrainz_ids,
        apple_catalog=apple_catalog,
    )
    links, reverted = _links(musicbrainz_ids, {aliases.get(album_id, album_id) for album_id in apple_ids})
    for ref in refs:
        if not ref[2]:
            continue
        if ref[:2] == (APPLE_MUSIC, MUSIC):
            identity = aliases.get(ref[2], ref[2])
            canonical[ref] = (
                (MUSICBRAINZ, MUSIC, reverted[identity])
                if identity in reverted
                else (APPLE_MUSIC, MUSIC, identity)
            )
        elif ref[:2] == (MUSICBRAINZ, MUSIC):
            canonical[ref] = _musicbrainz_ref(
                ref,
                links.get(ref[2]),
                bridged=bridged,
                musicbrainz_held=musicbrainz_held,
                aliases=aliases,
                apple_catalog=apple_catalog,
            )
    return canonical


def canonical_media_ref(ref):
    """Return an API media ref (a dict) with a music album's source and ID made canonical."""
    if ref.get("media_type") != MUSIC:
        return ref
    source, _, media_id = canonical_music_ref(ref.get("source"), MUSIC, ref.get("media_id"))
    if (source, media_id) == (ref.get("source"), str(ref.get("media_id"))):
        return ref
    return {**ref, "source": source, "media_id": media_id}


def payload_ref(payload, *, source=None, media_type=None):
    """Return a provider result's ``(source, media_type, media_id)``."""
    return (
        payload.get("source") or source,
        payload.get("media_type") or media_type,
        str(payload.get("media_id") or payload.get("id") or ""),
    )


def with_canonical_ref(payload, canonical, *, source=None, media_type=None):
    """Return a provider result under its ref in ``canonical`` (from ``canonical_music_refs``)."""
    ref = payload_ref(payload, source=source, media_type=media_type)
    new = canonical.get(ref, ref)
    if new == ref:
        return payload
    return {**payload, "source": new[0], "media_id": new[2]}


def canonical_music_payloads(payloads, *, source=None, media_type=None):
    """Return provider results with every album under its canonical ref, in one lookup.

    ``source`` and ``media_type`` stand in for results that don't name theirs. An
    album that two results now name (two MusicBrainz albums that moved to one Apple
    album) is kept where it first appears.
    """
    payloads = list(payloads)
    refs = [payload_ref(payload, source=source, media_type=media_type) for payload in payloads]
    canonical = canonical_music_refs(refs)
    results = []
    moved_by_ref = {}
    for payload, ref in zip(payloads, refs, strict=True):
        new = canonical[ref]
        moved = new != ref
        if new in moved_by_ref and (moved or moved_by_ref[new]):
            continue
        moved_by_ref.setdefault(new, moved)
        results.append({**payload, "source": new[0], "media_id": new[2]} if moved else payload)
    return results


def genre_fill_rows():
    """Return the link rows the genre link fill may write again: its ``unmatched`` rows.

    An unmatched row is the fill's when no MusicBrainz Item holds the album, or when it
    has never been checked (a Wikidata ID waiting for its check). The data move writes
    the rows of albums that have an Item, always with a check time, so the fill never
    touches them, nor any matched, applied, review or manual row.
    """
    held = Item.objects.filter(source=MUSICBRAINZ, media_type=MUSIC).values("media_id")
    return MusicAlbumLink.objects.filter(status=MusicAlbumLink.Status.UNMATCHED).filter(
        Q(checked_at__isnull=True) | ~Q(release_group_mbid__in=held),
    )


def store_wikidata_apple_ids(apple_ids):
    """Record Wikidata's Apple Music album IDs (P2281) for the genre link fill to check.

    ``apple_ids`` maps release groups to Wikidata's IDs, lowest first; the first is
    kept. A release group with no link row gets an unchecked ``unmatched`` row. A row
    the fill may write again gets the new ID and is checked on the fill's next run.
    Every other row stays as it is. Returns how many rows were added and changed.
    """
    wanted = {mbid: str(ids[0]) for mbid, ids in apple_ids.items() if ids}
    added = changed = 0
    for chunk in batched(sorted(wanted), WIKIDATA_STORE_BATCH):
        linked = set(
            MusicAlbumLink.objects.filter(release_group_mbid__in=chunk).values_list(
                "release_group_mbid",
                flat=True,
            ),
        )
        new = [
            MusicAlbumLink(
                release_group_mbid=mbid,
                apple_album_id=wanted[mbid],
                status=MusicAlbumLink.Status.UNMATCHED,
                method=MusicAlbumLink.Method.WIKIDATA,
            )
            for mbid in chunk
            if mbid not in linked
        ]
        added += len(MusicAlbumLink.objects.bulk_create(new, ignore_conflicts=True))
        stale = []
        for link in genre_fill_rows().filter(release_group_mbid__in=chunk):
            if link.apple_album_id != wanted[link.release_group_mbid]:
                link.apple_album_id = wanted[link.release_group_mbid]
                link.method = MusicAlbumLink.Method.WIKIDATA
                link.checked_at = None
                stale.append(link)
        changed += MusicAlbumLink.objects.bulk_update(stale, ["apple_album_id", "method", "checked_at"])
    return {"added": added, "changed": changed}


def _musicbrainz_ref(ref, link, *, bridged, musicbrainz_held, aliases, apple_catalog):
    """Apply rules 2 to 6 to one MusicBrainz ref."""
    mbid = ref[2]
    if (
        link is not None
        and link.status == MusicAlbumLink.Status.APPLIED
        and link.item is not None
        and link.item.media_type == MUSIC
    ):
        return (link.item.source, MUSIC, link.item.media_id)
    held = link.musicbrainz_held if link is not None else musicbrainz_held.get(mbid, False)
    if held:
        return ref
    if bridged.get(mbid):
        return (APPLE_MUSIC, MUSIC, min(bridged[mbid]))
    if (
        apple_catalog
        and link is not None
        and link.status == MusicAlbumLink.Status.MATCHED
        and link.apple_album_id
        and (link.tier in REDIRECT_TIERS or link.method == MusicAlbumLink.Method.MANUAL)
    ):
        return (APPLE_MUSIC, MUSIC, aliases.get(link.apple_album_id, link.apple_album_id))
    return ref


def _apple_albums(apple_ids, musicbrainz_ids, *, apple_catalog):
    """Read the ``AppleAlbum`` rows the rules need, in one query.

    Returns the alias map (Apple ID to its listing's identity); for each release
    group, the Apple identities an Item holds whose bridge has the same title
    (rule 4); and whether each of those release groups has its own MusicBrainz Item.
    """
    rows = Q(pk__in=[])
    if apple_ids:
        rows |= Q(apple_album_id__in=apple_ids)
    if musicbrainz_ids:
        rows |= Q(release_group_mbid__in=musicbrainz_ids, same_title=True)
        if apple_catalog:
            # Rule 5's matches, so they go to their listing's identity too.
            rows |= Q(
                apple_album_id__in=MusicAlbumLink.objects.filter(
                    release_group_mbid__in=musicbrainz_ids,
                    status=MusicAlbumLink.Status.MATCHED,
                ).values("apple_album_id"),
            )
    albums = (
        AppleAlbum.objects.filter(rows)
        .annotate(
            held=Exists(_music_items(APPLE_MUSIC, OuterRef("canonical_id"))),
            musicbrainz_held=Exists(_music_items(MUSICBRAINZ, OuterRef("release_group_mbid"))),
        )
        .values_list("apple_album_id", "canonical_id", "release_group_mbid", "same_title", "held", "musicbrainz_held")
    )
    aliases = {}
    bridged = {}
    musicbrainz_held = {}
    for album_id, identity, mbid, same_title, held, mbid_held in albums:
        aliases[album_id] = identity or album_id
        if mbid in musicbrainz_ids and same_title:
            musicbrainz_held[mbid] = mbid_held
            if held:
                bridged.setdefault(mbid, set()).add(identity)
    return aliases, bridged, musicbrainz_held


def _links(musicbrainz_ids, apple_identities):
    """Read the link rows the rules need, in one query.

    Returns the row for each release group, and for each Apple identity that was
    moved back to MusicBrainz, the release group whose Item still exists (rule 1). A
    reverted row names an Apple identity only once no Apple Item of it holds user rows
    (``music_move.revert``), so an album a reverted one had joined keeps its ref.
    """
    rows = Q(pk__in=[])
    if musicbrainz_ids:
        rows |= Q(release_group_mbid__in=musicbrainz_ids)
    if apple_identities:
        rows |= Q(status=MusicAlbumLink.Status.REVERTED, apple_album_id__in=apple_identities)
    links = (
        MusicAlbumLink.objects.filter(rows)
        .select_related("item")
        .annotate(musicbrainz_held=Exists(_music_items(MUSICBRAINZ, OuterRef("release_group_mbid"))))
        .order_by("release_group_mbid")
    )
    by_mbid = {}
    reverted = {}
    for link in links:
        if link.release_group_mbid in musicbrainz_ids:
            by_mbid[link.release_group_mbid] = link
        if (
            link.status == MusicAlbumLink.Status.REVERTED
            and link.musicbrainz_held
            and link.apple_album_id in apple_identities
        ):
            reverted.setdefault(link.apple_album_id, link.release_group_mbid)
    return by_mbid, reverted


def _music_items(source, media_id):
    return Item.objects.filter(source=source, media_type=MUSIC, media_id=media_id)
