# Apple Music catalog

Spine is moving its music catalog from MusicBrainz release groups to Apple Music albums,
so albums are listed the way Apple Music lists them: a deluxe edition such as *SOS
Deluxe: LANA* is its own album instead of one of dozens of editions inside *SOS*.
MusicBrainz stays permanently, both as the fallback and for the detailed genres behind
the genre pages.

## Status

Step 1, the groundwork, is in place:

- the `apple_music` source
- the `MusicAlbumLink` and `AppleAlbum` tables
- the Apple Music API client
- the listing rule

Step 2 is being built. So far:

- the catalog setting
- Apple album and song pages for `apple_music` refs
- the Item fallback when Apple Music fails
- Apple search, people search and artist pages for the `apple_music` source
- canonical refs at every entry point, so an album's old MusicBrainz ref and its Apple
  aliases reach one Item
- Other Versions, More by and You Might Also Like on Apple album pages
- Apple's badge, the "Data from Apple Music" credit and the trademark line on the web's
  Apple album pages
- the MusicBrainz bridge of Apple albums, filled in the background
- genre albums linked to Apple albums, and Apple genre pages from them
- featured lists: the `apple_album` ID, canonical music matches, and a pause for the
  data move
- the data move and its undo (`manage.py music_catalog`, run on production through
  `scripts/music-catalog-production.sh`), and the fallback to MusicBrainz

The catalog setting still defaults to MusicBrainz, so search, people search, genre
pages, featured lists and every existing album are served from MusicBrainz. Only a
request that names an `apple_music` ref or source reaches Apple Music. No album has
moved, so canonical refs change nothing yet. The two background tasks below do
nothing until the Apple Music key is set.

## Catalog setting

- `MUSIC_CATALOG_SOURCE` picks where new music albums come from: `apple_music`, or
  anything else for MusicBrainz, so a typo falls back safely.
- It defaults to `MUSIC_CATALOG_DEFAULT` (`musicbrainz`). Tests always use the default,
  never `.env`.
- `app.config.get_sources` and `get_default_source_name` read it at call time for
  music. `MEDIA_TYPE_CONFIG` is built when the module loads, so its music entry can't
  hold the answer.
- MusicBrainz refs keep working whatever the setting says.

## Listings and identity (`src/app/music_listing.py`)

Apple's catalog has more than one album record for what a person sees as one album: an
explicit album and its clean twin, or an older upload and its re-upload. Spine shows each
group as one **listing**.

- **Same listing:** albums in each other's `other-versions` family, with the same artist
  and the same normalized title. Normalizing applies NFKC, casefolding, straight quotes,
  `[]` read as `()`, and collapsed spaces.
- **Edition words keep listings apart.** "SOS" and "SOS Deluxe: LANA" are two listings,
  and so are "(Deluxe)", "(Remastered 2022)" and " - Single" versions.
- **An album's ID is the number at the end of its `url`.** Apple keeps old duplicate IDs
  whose link names the real album: Year Zero's `1147788150` links to `1440826538`.
- **The identity** is the one ID Spine stores for a listing, chosen in this order:
  1. a member that already has a Spine `Item`; its stored ID never moves
  2. it can be streamed (`playParams`)
  3. it is complete, or not released yet
  4. explicit, then unrated, then clean
  5. the most tracks
  6. the lowest ID
- **Hidden listings** are left out of search, artist pages and other versions. A listing
  is hidden when no member can be streamed, or when every member is incomplete with a
  release date that has passed. Its IDs still open.

## Canonical refs (`src/app/music_catalog.py`)

One album can be asked for under more than one ref: the MusicBrainz ref it had before
it moved to Apple Music, which installed builds, recent searches, people lists and old
web links keep, or an Apple alias (a clean twin, an older upload).
`canonical_music_ref(source, media_type, media_id)` returns the ref the album is
stored under. It reads the database only, never the network, and runs before any
cache read. `canonical_music_refs(refs)` answers a whole page in two queries.

The rules, in order:

1. **An Apple ref** goes to its listing's identity (`AppleAlbum.canonical_id`). If that
   identity is the Apple album of a `reverted` link row whose MusicBrainz Item still
   exists, it goes to that MusicBrainz ref. A reverted row names its Apple album only
   once no Apple Item of it holds user rows (see Undoing), so an album that a reverted
   album had joined keeps its own ref.
2. **A MusicBrainz ref with an `applied` link row** goes to the linked Item's ref. This
   wins over a stray MusicBrainz Item with the same ID.
3. **A MusicBrainz ref with its own Item** stays: unmatched albums, albums in review and
   albums not moved yet.
4. **A MusicBrainz ref whose release group an Apple Item's bridge names**, with the same
   title, goes to that Apple Item, so an album first added from an Apple page doesn't
   get a second Item.
5. **With the catalog on Apple Music**, a MusicBrainz ref with a `matched` tier 1, tier 2
   or manual row goes to that Apple album.
6. Anything else stays.

With no link rows and no bridges, every ref comes back as it is.

**Where it runs**

| Where | What it covers |
|---|---|
| `MediaExposureMixin.dispatch` (`api/views/mixins.py`) | Path refs: detail, tracking, reviews, community, external ratings, posters. In `dispatch`, because DRF passes `initial()` a copy of the kwargs |
| The MusicBrainz `basic`, `enrichment` and song views, and the Apple song view | The music views not on the mixin. A moved album's `basic` and `enrichment` answer with its Apple page, with a 200 and no redirect |
| `find_item` and `get_or_create_item_from_metadata` (`api/serializers/common.py`) | Refs in request bodies: diary, lists, likes, Hall of Fame |
| The music branch of `services.get_media_metadata` | Every metadata read; the payload names the canonical `source` and `media_id`. Calendar, filter backfill, web pages |
| Result lists (`canonical_music_payloads`) | Search, All media search, genre pages, artist pages (before their Items are looked up or made), related sections, completion counts and web search cards. One batch per page |
| Web `media_details` | A 302 from an old `/details/musicbrainz/music/<mbid>/…` link to the album's page |
| Yamtrack import | Each music row as it is read, before the duplicate check. A moved album keeps its own title and cover |

- **Cached pages aren't flushed.** Search, genre and artist pages keep the refs they
  were fetched with and are corrected when served. An album that two results now name
  is shown once, where it first appears.
- **Song pages.** A MusicBrainz song path of a moved album is a 404, because its
  MusicBrainz song ID isn't on the Apple album. Installed builds open songs with the
  ref the album page answered with, so they use the Apple song path.
- **No backstop.** Items are still made in many places, and not all of them go through
  these entry points: a web action from a page opened before the move can still make a
  stray MusicBrainz Item. Rule 2 hides it from every read, and `music_catalog check
  --repair` joins its users' rows to the Apple Item (see Moving existing albums).

## Tables

- **`MusicAlbumLink`** has one row per MusicBrainz release group Spine has matched, or
  tried to match, to an Apple album. It records:
  - the match: status, method and tier
  - the MusicBrainz genres and the exact Spotify link
  - what the album's `Item` looked like before a move, so the move can be undone
- **`AppleAlbum`** has one row per Apple album ID Spine has seen. It records:
  - the listing identity the ID belongs to
  - its UPC
  - its MusicBrainz release group, genres, first release date and Spotify link

## Client (`src/app/providers/apple_music.py`)

- **Developer token.** It is signed with ES256 from three settings:
  `APPLE_MUSIC_TEAM_ID`, `APPLE_MUSIC_KEY_ID`, and `APPLE_MUSIC_PRIVATE_KEY` (base64 of
  the `.p8`, on one line).
  - It lasts 12 hours, is kept in memory, and is re-signed when less than an hour is
    left.
  - It is never logged or cached.
  - When the settings are missing, calls raise "Apple Music isn't configured" without
    sending a request.
- **Rate limits.** There are two sessions, each with its own Redis bucket:
  - Requests someone is waiting on go through the user-facing session, at 5 per second.
    They have a 10-second timeout and no retries on 429. A 429, a 5xx or a timeout
    pauses them for 60 seconds, using a flag in Redis.
  - Tasks and commands go through the background session, at 2 per second, with the
    usual retries, and never pause the user-facing one. So does every request made
    inside a `quota.scope`: featured list syncs reach Apple through search and album
    pages, which don't say they are background work.
- **Fetches.** They return Apple's JSON unmapped:
  - `search`
  - `albums`, 100 IDs per call
  - `album`, which follows the tracks `next` link
  - `albums_by_upc` and `songs_by_isrc`, 25 per call
  - `artist`
- **Apple's behaviour, as observed in October 2026:**
  - `filter[upc]` returns a barcode's whole product family, not just the album with that
    barcode.
  - `songs?filter[isrc]` returns every album a recording is on, compilations included.
  - `other-versions` doesn't always include the deluxe: SOS and LANA are separate
    families.
  - Clean edits have different ISRCs from the explicit songs.

## Album pages (`apple_music.music`)

`services.get_media_metadata` sends `apple_music` music refs here. It imports the
module inside the function: `apple_music` builds on `services`' error class, so a
top-level import would be circular.

- **One request** serves the page:
  `albums/{id}?include=tracks,artists&views=other-versions,related-albums`, cached for a
  day. The album page, its songs and the writes that fetch an album's metadata all share
  it. It serves both of the MusicBrainz page's stages.
- **Identity.** The album and its other versions are grouped into listings. The payload's
  `media_id` is its listing's identity, which can differ from the ID asked for (a clean
  twin, an older upload). The API answers with that ID.
- **Content** (tracks, title, cover, Apple Music link) comes from the listing's best
  member. That costs a second request only when it isn't the album asked for. If that
  request fails, the page shows the album asked for; when the failure may pass, the
  page is a partial page (see When Apple Music fails).
- **`AppleAlbum` rows.** Every album ID the page sees, its own and the one in its link,
  is upserted with its listing's identity and barcode. A row seen without a barcode
  keeps the one it has. A listing whose members already point at an ID an Item holds
  keeps that ID.
- **A taken-down album** (a 404 for an ID an Item holds) is replaced by an album with the
  same normalized title from its stored barcode's family. The replacement is recorded as
  an alias of the held ID, so the Item keeps its identity.

**The response keeps today's shape**, with every field installed app builds require:

| Field | Value |
|---|---|
| `title` | Apple's name without " - EP" / " - Single"; the suffix becomes `primary_type` |
| `image` | The artwork template at 600x600; anything over 200 characters (`Item.image`) is the placeholder |
| `release_date`, `details.first_release_date` | Apple's `releaseDate`, or MusicBrainz's first release date from the bridge when it is surely earlier and the release group has the same title |
| `genres` | The bridge's MusicBrainz genres whenever it has any, else the Item's genre filters, else Apple's genres in MusicBrainz's words (`app/music_catalog.py`, without "Music"). Album pages rewrite the Item's genre filters from this list: Apple's broad genres give way to MusicBrainz's once the bridge has them, and the list is empty only when all three are, so the filters are never emptied |
| `music.release_group_mbid`, `representative_release.release_mbid` | The Apple album ID |
| `music.cover_art` | `{source: "apple_music", release_group_mbid: <id>, fallback_used: true}` |
| `selection_basis`, `country`, `format`, `status` | `"apple_music"`, the storefront, `"Digital Media"`, `"Official"` |
| `media[]`, `tracks[]` | Grouped by `discNumber`; `track_mbid` and `recording_mbid` are the song ID (`-2` and so on when a song repeats); `number` is a string; `is_video` for music videos |
| `artist_credit[]` | One per Apple artist, joined as "A, B & C". `artist_mbid` is always null; the new `artist_source` and `artist_id` name the Apple artist |
| `streaming_links` | Apple Music first (`kind: "album"`), then Spotify: the exact album when the bridge or a link row knows it (`kind: "album"`), else Spotify's search for the artist and album (`kind: "search"`) |
| `score`, `annotation` | Null: no community score and no editor's notes |
| `related` | On album pages, `other_versions`, `more_by_artist` and `related_albums` (below); empty for every other read |

New keys are optional additions: `music.content_rating` and each track's
`content_rating`, and each recording's `composer_name` and `source_url`.

**Cover art.** An Apple Music album always shows Apple's cover.
`app.curation.music_picks_apply` is false for Apple Music albums, and every reader of
saved poster and backdrop picks checks it. The picks stay saved, and apply again if an
album goes back to MusicBrainz.

**External ratings.** Apple Music albums have none. A MusicBrainz rating kept on an
album that moved to Apple Music isn't shown.

## Related sections

Album pages, and only they, ask for an Apple album's related albums
(`get_media_metadata(…, include_music_related=True)`, from the API's album detail).
Diary and tracking writes, the calendar and other metadata reads skip them, so they cost
no requests there. They are part of the album's cached payload, in both of the page's
stages, because installed builds replace the first stage's payload with the second's.

`related_sections_from_payload` has a music branch that turns them into three sections:

| Section | Title | Albums |
|---|---|---|
| `other_versions` | Other Versions | The other listings of the album's version family, in Apple's order, then its editions found by shared songs |
| `more_by_artist` | More by *first artist* | The first album artist's Albums section on Apple Music (`artists/{id}/view/full-albums`), without this album |
| `related_albums` | You Might Also Like | Apple's similar albums by other artists, from the album page's `related-albums` view |

- **At most 10 albums a section.** The album's own listing and hidden listings are left
  out of every section, and so is an empty section.
- **Editions by shared songs.** Apple's family doesn't always hold the deluxe: SOS and
  SOS Deluxe: LANA, Thriller and Thriller 40, and Who's Next and Life House are separate
  families. So one request (`songs?filter[isrc]=…`) finds every album the first song of
  each disc is on; LANA puts its new songs first, so its second disc finds SOS. One batch
  (`albums?ids=…&include=tracks&views=other-versions`) then reads those albums. An album
  is kept when it has the same artists, isn't a compilation or a single, has the same
  content rating (clean edits have their own ISRCs), and either holds every ISRC of this
  album (a bigger edition) or has all of its own on this album (the original). The
  listing rule then folds the kept albums' twins, so LANA's three albums show as one.
  A re-recording such as 1989 (Taylor's Version) has new ISRCs and stays unlinked.
- **Badges.** Each other version has a `relation`: "Original album" for one whose songs
  are all on this album; else the first of "Live", "Anniversary", "Deluxe" (deluxe,
  expanded, bonus, extended, or a bare "edition") or "Remaster" that its title names;
  else "Deluxe" for a bigger edition; else "Other version".
- **Each album is a search row:** its listing's identity (or its own ID in the other two
  sections), title, 600x600 cover, "Artist · Year · Type" subtitle and content rating.
- **Requests.** Up to three more per album page: the shared-songs lookup and its batch
  (cached for a day for the album) and the artist's albums (cached for a day for the
  artist). Similar albums come with the page. A request that fails leaves out only its
  part of the page and isn't cached. When the failure may pass (no answer, the pause,
  a 429 or a 5xx), the whole page is a partial page, which the music caches don't keep
  (see When Apple Music fails); a 404 or another 4xx is an answer, and the page is kept
  without that part.
- **Serving.** Every album goes through `canonical_music_refs` in one batch, so an alias
  or a moved album opens under the ref that holds it, and appears once. Their Items and
  the viewer's state are read in a fixed number of queries.
- **Every card is checked** against the app's strict `MediaSummary` decoder (text, lists
  of text, whole numbers, the poster orientation). One card it couldn't decode would
  break the whole page, so such a card is left out.

## Song pages

`/api/v1/media/apple_music/music/<album_id>/recordings/<song_id>/` is the path
installed builds already call. The page is built from the matching track in the album's
cached page, with no extra request:

- `parent_album`, `albums` and `releases` hold the album only.
- `context_release` is the album and the track.
- Apple's composer line becomes one text-only credit in the new `credits` key.
- Works and alternative recordings are empty.
- A song that isn't on the album is a 404.

## Search (`apple_music.search_albums`)

`services.search` sends music searches with the `apple_music` source here; every other
music search goes to MusicBrainz as before. The API's music searches pass the catalog's
source when the request names none, so installed builds, which never send one, get
Apple Music results once the catalog is `apple_music`.

- **Two requests per page.** One Spine page is one Apple page of up to 25 albums
  (`search?types=albums&limit=25`, then Apple's `next` link), plus one batch of those
  albums with their version families (`albums?ids=…&views=other-versions`), so the
  listing rule can fold clean twins and re-uploads. Apple stops after about 100
  albums.
- **Apple's order is kept.** There is no re-ranking, no popular-albums merge and no
  ListenBrainz lookup. A listing appears once, at the best position of any of its
  members, and never again on a later page. Hidden listings are left out.
- **Each row shows its listing:** its identity as `media_id`, and its best member's
  title, cover (600x600) and date. `AppleAlbum` rows are upserted for every album the
  page saw, as on album pages.
- **What a row shows:**
  - the title without " - EP" / " - Single", which becomes `primary_type`
  - the subtitle "Artist · Year · Type", plus " · Clean" when the listing has no
    explicit album to show instead
  - the new optional `content_rating` (`explicit` or `clean`), which search and artist
    summaries pass through
- **Pre-orders and video albums stay**, as Apple shows them.
- **Caching.** Each page is cached for 6 hours with the identities shown so far, so a
  later page continues from Apple's `next` link without repeating a listing. The API's
  music search cache (stale results served while a worker refreshes them) sits on top,
  keyed by source, as for MusicBrainz.
- **All media search.** Apple sends no popularity, so its order stands in for it:
  `provider_rank_boost = max(0, 30 − 3 × position)`. Apple's first album then leads an
  equally named film with a few hundred votes, while a popular one still comes first
  (`app/tests/providers/test_search_rank_mixed.py`, tuned with "renaissance",
  "thriller" and "sos").
- **No waiting on Apple.** Searches use the user-facing session: a 10-second timeout,
  no sleeping on a 429, and the minute's pause after a failure, during which they fail
  at once. The caller's own timeout isn't passed on. MusicBrainz's "a newer search
  replaces this one" stays on the MusicBrainz path only.

## People search and artist pages

- **People search** (`apple_music.search_people`) asks
  `search?types=artists`. `services.person_search_sources()` is what the API's people
  search asks: every person source, but only the music catalog's artists, so
  MusicBrainz today.
- **`apple_music` is a person source** (`services.SUPPORTED_PERSON_SOURCES`), so its
  artist pages open and its artists can join people lists.
- **Artist pages** (`apple_music.person_page`, cached for a day) are Apple's own
  sections, in Apple's order, so a standard album can sit behind its deluxe as it does
  in Apple Music:

  | Apple section | Credit group |
  |---|---|
  | Albums (`full-albums`) | Albums |
  | Singles & EPs (`singles`) | EPs for " - EP" albums, else Singles |
  | Live Albums (`live-albums`) | Live releases |
  | Compilations (`compilation-albums`) | Compilations |
  | Appears On (`appears-on-albums`) | Appears on |

  - One request reads the artist and the first 10 albums of each section. A section
    with more follows its `next` link, which Apple answers 100 at a time, up to 100
    albums. One batch then reads every album's version family.
  - Each album is shown as its listing, which folds Appears On's clean twins into one.
    Hidden listings are left out.
  - An album in several sections is one credit in each of their groups.
  - Genres are Apple's, in MusicBrainz's words.

## The MusicBrainz bridge

An Apple album's bridge is its `AppleAlbum` row's MusicBrainz side: the release group,
its MusicBrainz genres, its first release date, and the exact Spotify album link. Album
pages read it for their genres, date and Spotify button (see Album pages).

- **Queued by album pages, filled in the background.** When an album page's identity
  has no bridge, or one checked more than 30 days ago, the page adds the ID to a Redis
  set (`apple_music:bridge_queue`, with the checkout's `REDIS_PREFIX`). A set holds an
  ID once, however often the page is read. No task is queued per page view.
- **"Fill Apple album bridges"** (`app.music_links.fill_bridges`) runs every 5 minutes
  on the default queue:
  - at most a minute a run; an album isn't started without time for its requests
  - MusicBrainz requests 3 seconds apart, on the shared MusicBrainz session, and at
    most 1,500 a day (a Redis counter per day), so the requests people are waiting on
    keep most of MusicBrainz's one a second
  - one background Apple batch for the albums' titles, artists and barcodes
  - it stops at the first failed request, and albums it didn't finish stay queued;
    an album MusicBrainz answers a lasting error for (a 4xx other than 429, such as a
    removed release group) is only marked as checked, so the albums after it get
    their turn
  - it publishes no task result (`app.signals.RESULTLESS_TASKS`)
- **Where the bridge comes from:**
  - A matched or applied link row that names one of the album's IDs gives the release
    group, genres and Spotify link with no MusicBrainz request, when the row has genres
    and the genre table has the album's title and date; otherwise one request for the
    release group.
  - Otherwise the album's barcode, in up to three requests:
    1. `release?query=barcode:(<12 digits> OR <13 digits>)`, keeping a release whose
       artist matches and whose title, or its group's, matches the album's; Apple's
       barcodes carry padding zeros, which are dropped
    2. `release/<id>?inc=url-rels+release-groups`, for the Spotify album link
    3. `release-group/<id>?inc=genres`, for the genres, title and first release date
  - An album with no barcode, or one MusicBrainz doesn't know, is only marked as
    checked, so it isn't queued again for a month.
- **`same_title`** is set when Apple's title, without " - EP" / " - Single", is the
  release group's. SOS Deluxe: LANA's barcode leads to SOS's release group: LANA gets
  its genres, but not its 2022 date.
- **After filling,** an Item that holds the album gets its genre and date filters again
  (`apple_music.filter_metadata`, the album page's own rules, so MusicBrainz's genres
  replace Apple's), and the album's cached detail pages, both stages and their
  `:validation` keys, are dropped (`api.services.media.drop_music_detail_cache`).

## Genre pages

The genre pages keep MusicBrainz's detailed genres. `MusicGenreAlbum` stays keyed by
release group, and its weekly refresh is unchanged apart from one addition.

- **Wikidata's Apple Music IDs.** The weekly refresh's Wikidata query also asks for
  each album's Apple Music album ID (P2281), through `OPTIONAL`, so no album drops out.
  The winning album's lowest ID is stored on a link row
  (`music_catalog.store_wikidata_apple_ids`): a release group with no row gets an
  unchecked `unmatched` row (method `wikidata`), and a row the genre link task may
  write again gets the new ID. Every other row stays as it is.
- **"Link genre albums to Apple"** (`app.music_links.link_genre_albums`) runs hourly
  on the featured queue, at most 10 minutes a run, on the background Apple bucket,
  most read-about albums first. No MusicBrainz requests, and it never writes the genre
  table. It links each genre album with no link row, or whose own unmatched row is
  unchecked or more than 30 days old:
  1. Wikidata's ID, checked 100 at a time (`albums?ids=…&views=other-versions`): the
     album must have the same artist and title, with no edition words the MusicBrainz
     title lacks; the match is its listing's identity.
  2. Otherwise Apple search (`apple_music.match_album`, below).

  It writes tier-3 `matched` rows (method `wikidata` or `search`) or `unmatched` rows,
  with the album's title, cover and genres. A failed Apple request ends the run; the
  next run carries on.
- **Whose rows are whose.** The genre link task writes only rows for release groups
  with no row, and its own `unmatched` rows: those of albums no MusicBrainz Item holds,
  or never checked (`music_catalog.genre_fill_rows`). Rows of albums that have an Item
  belong to the data move, which always records when it checked them. Tier-3 rows feed
  genre pages and featured lists; they never redirect old links and never move a
  logged album.
- **Matching by search** (`apple_music.match_album(title, artist)`): one background
  search for "artist title" and one batch of the hits' version families. A hit counts
  when it isn't a single, has the same artist and the same title, or the same once
  " - EP" and trailing bracketed tags ("(Remastered)") are left out of Apple's title.
  Names match without case, accents or punctuation. The match is the one listing that
  counts best: the exact title first, then no edition words such as "(Deluxe)" that the
  MusicBrainz title lacks (a remaster or mix is neutral). A tie, such as two equal
  "Stratosphere" listings, is no match.
- **Apple genre pages** (`apple_music.discover`), for a request with the `apple_music`
  source, or any music genre request once the catalog is Apple Music (installed builds
  send an older album's MusicBrainz source with its genre chip):
  - the genre's albums that a matched or applied link row ties to an Apple album, in
    the genre table's order
  - each under its canonical ref; an album a MusicBrainz Item holds keeps its
    MusicBrainz ref, so its data stays together
  - an album two rows name is shown once
  - the link row's title and cover, else the Item's, with MusicBrainz's artist and year
  - no MusicBrainz top-up and no request at all when a page opens: six queries

## Featured lists

- **`apple_album`**, an Apple Music album ID, is a direct ID (`apple_music:music:<id>`),
  tried before `mbid_rg`. It is last in `KEY_ID_ORDER`, so entries that already carry
  `mbid_rg` or `wikidata` keep their keys, snapshots and chart moves.
- **Both sources resolve.** `ITEM_SOURCES` for music is Apple Music, then MusicBrainz,
  and `parse_ref` accepts either. A title search for music asks only the catalog's
  source.
- **Canonical matches.** Every music match, whichever step found it (an override, a
  ref, an ID, the snapshot, or the cache), is made canonical in one batch after the
  cache read (`resolve.canonical_resolutions`); the 45-day cache keeps the raw match.
  A match that moves to another source loses its cover, which was the old source's.
  The writer does it again before it writes, so an album that moved since it resolved
  lands on its Apple Item, and two entries that are now one album make one row.
- **With the catalog on Apple Music,** a MusicBrainz album that no MusicBrainz Item
  holds becomes its Apple album: the one its matched or applied link row names (any
  tier), or, with no row, Apple search (`apple_music.ref_for_release_group`), which
  records a tier-3 row either way. Up to 50 searches a run, and none after a failed
  one; a miss keeps the MusicBrainz album, so lists don't shrink. With the catalog on
  MusicBrainz, today's matching runs.
- **Apple requests from syncs are background requests.** A sync runs inside
  `quota.scope`, so its title searches and album pages go through the background
  session and never pause the app's own Apple calls.
- **The data move pauses music lists.** `dispatch.pause_music_lists()` sets a Redis
  flag for up to 6 hours, until `resume_music_lists()`. While it is set, the dispatcher
  skips music lists, and a music sync queued before it gives back its lease.
- **Unchanged:** every adapter, including the Apple Music charts, which still go
  through MusicBrainz.

## Web

- **Album page** (`templates/app/media_details.html`). An Apple Music album shows Apple's
  "Listen on Apple Music" badge (`static/img/listen-on-apple-music.svg`, unaltered, 40
  pixels tall) linked to the album on Apple Music, the "Data from Apple Music" credit, and
  a page footer with "Apple and Apple Music are trademarks of Apple Inc., registered in
  the U.S. and other countries." An album opened from its Item has no link, so no badge;
  the credit and the footer stay. MusicBrainz albums show none of it.
- **No related sections, poster picker, song pages or artist pages** on the web. The page
  reads the album without its related albums, so it costs one request.
- **Search tabs** come from `app.config.get_sources`, so the music tab is the catalog's
  source: MusicBrainz now, Apple Music once the catalog is.
- **Old links.** `/details/musicbrainz/music/<mbid>/…` for an album that moved redirects
  to its Apple page (see Canonical refs).

## When Apple Music fails

An `apple_music` album that has an Item opens from the Item whenever Apple Music fails:
no key, a rejected key, a takedown, a 429, a timeout, or the minute's pause after one.

- The page has the Item's title, cover, release date and genre filters, `music: null`,
  and no buttons.
- It is marked `item_fallback`, and the music caches never keep it, so the full page
  comes back once Apple Music answers.
- This one place covers album pages, diary and tracking writes, the calendar and every
  other metadata read.
- An album with no Item still fails.

When Apple Music answers the album but fails one of the page's other requests for a
moment (the best member's page, or a related section's lookup), the page opens without
that part and is marked `partial_page`. The music caches don't keep it either: a stale
copy stays until a refresh gets the whole page, and the refresh task reports
`partial_page` instead of `refreshed`.

## Moving existing albums (`manage.py music_catalog`)

Albums already in Spine were added as MusicBrainz release groups. The `music_catalog`
command (`src/app/music_move.py`) moves them to their Apple Music albums. Nothing is
deleted, and every move can be undone.

| Step | What it does | Changes data |
|---|---|---|
| `check [--repair]` | Tests the key with one Apple Music request, then counts link rows by status, strays, albums kept on both, rows in tables the move doesn't know, Apple Music albums not from the move, and genre pages under the switch's gate (20 or more albums, under 80% of them linked). `--repair` joins strays first | Only with `--repair` |
| `map [--limit N]` | Matches albums to Apple Music albums. Writes link rows (and the `AppleAlbum` rows of the albums it sees), nothing else | No |
| `report [--csv PATH]` | Counts every album by group and status. `--csv` writes one row per album to a file only its owner can read, or to standard output with `-` (the counts then go to standard error) | No |
| `link --mbid M --apple A` | Records a manual pick | Yes |
| `apply [--limit N]` | Moves matched albums | Yes |
| `revert --mbid M` or `--all` | Undoes moves | Yes |

- Every step can run again: it skips what is done and carries on from what isn't. Each
  album is its own transaction, so a killed run loses at most the album it was on.
- On production, steps run only through `scripts/music-catalog-production.sh` (below),
  and never while a deploy could start: a deploy recreates the container and kills the
  run.

### Which albums, in which order

`map` and `apply` take the MusicBrainz album Items in three groups:

1. **Albums with user data:** tracking, a log, a like, a row in someone's own list, a
   Hall of Fame pick or a profile backdrop. They are matched by barcode.
2. **Albums only featured lists hold.**
3. **Other albums,** with no user data at all.

Groups 2 and 3 are matched through their link row, else through Apple search with the
artist the genre table or a cached MusicBrainz album knows. They make no MusicBrainz
requests, so one whose artist isn't known is left and counted. Moving them keeps an
unused MusicBrainz Item from holding on to its album's old ref.

### Matching (`map`)

`map` matches group-1 albums again on every run, whatever their row says, except a
manual pick or a moved album, and copies the Item's genre filters (MusicBrainz's genre
names) into the row. It looks at albums it has never checked first, then the longest
unchecked. A group-2 or group-3 album is matched again only after 30 days unmatched.

For a group-1 album:

1. **Barcodes.** Every Official release's barcode from the release browse (cached for a
   day and shared with album pages). One with fewer than 12 digits is skipped; the rest
   compare as 13 digits, so `00602547582812` and `602547582812` are one barcode. They
   are looked up 25 at a time (`albums?filter[upc]=`), which answers each with its whole
   product family. An album's ID is the one in its link.
2. **Guard.** An Apple album passes when:
   - its title, without " - EP", " - Single" and trailing bracketed tags, is the
     album's, or contains it and the Apple album's own barcode is one of MusicBrainz's;
   - its artist is a credited name, an artist's name, or an artist's sort name in
     natural order ("Utada, Hikaru", for 宇多田ヒカル, reads "Hikaru Utada"), or its own
     barcode is one of MusicBrainz's and its title is the same.

   An unrelated album that has one of MusicBrainz's barcodes fails on its title.
3. **Pick,** in this order: its own barcode over a family's; the same title; no edition
   words the MusicBrainz title lacks; the track count nearest the one most of
   MusicBrainz's Official releases have; explicit, then unrated, then clean; the lowest
   ID. The pick becomes its listing's identity, and the listing's aliases are recorded.
4. **Songs,** when no barcode finds the album: one release's ISRCs, looked up 25 at a
   time (`songs?filter[isrc]=`), and the album that has the most of them, when one
   listing does and it has the same title and artist and a track count within one.
5. **Search** last (`apple_music.match_album`).

The exact Spotify link comes from the release with the pick's barcode, or one with the
same title and track count, in the same release browse. MusicBrainz requests go out 3
seconds apart, one request a try (the MusicBrainz client's own quick retries are off),
and wait 30 seconds, then 60, while MusicBrainz is busy (503). An album MusicBrainz no
longer has (a 404 or 400) is `unmatched`, with its check time, and the run carries
on; any other failed request stops the run, naming the album it was on. Run `map` at
night.

| Tier | When | Row |
|---|---|---|
| 1 | The Apple album's own barcode, the same title, and a track count within one | `matched` |
| 2 | The same through the barcode's product family only | `matched` |
| 3 | Anything else found: songs, search, or a barcode match that misses tier 1 or 2 | `review` for group 1; `matched` for groups 2 and 3 |
| none | Nothing passes | `unmatched` |

`link --mbid M --apple A` records a manual pick (method `manual`, no tier). It checks
the Apple album with one request and stores its listing's identity. An album that has
moved must be reverted first.

### Moving (`apply`)

`apply` moves tier 1, tier 2 and manual matches, and tier 3 for albums with no user
data; `review` rows wait for a manual pick. Featured music lists are paused while it
runs (`lists.featured.dispatch.pause_music_lists`). Albums with the most user rows go
first, so when two MusicBrainz albums are one Apple album, the bigger is re-keyed and
the other joins it. Each album, in its own transaction, is:

- **Re-keyed in place** when no Apple Music Item holds any album of its listing. The
  Item keeps its pk, so every row that points at it follows: tracking, logs, likes,
  lists, featured lists, events, ratings and cover picks. A queryset update sets its
  source, ID, title and cover to the Apple album's and clears its accent colour, so no
  signal fires and no history or statistics task is written. Its old fields and filters
  are saved on the link row (`previous`) first. Its genre and date filters then follow
  the album page's rules, and its Apple album gets the MusicBrainz bridge from the link
  row (release group, genres, first release date and exact Spotify link), marked as
  checked.
- **Joined** when an Apple Music Item already holds the listing: another MusicBrainz
  album moved there first, or the album was first added from an Apple page. Each user's
  rows move to that Item (below). The MusicBrainz Item stays, unused: its old ref
  reaches the Apple Item (canonical ref rule 2), and the next featured sync moves its
  featured rows.

The row then becomes `applied`, and once it is committed both refs' cached album pages
are dropped.

- Each album compares its users' row counts before and after, inside its transaction,
  which rolls back if they differ.
- Every 50 albums and at the end, a post-check stops the run if a moved Item lost its
  pk or its ref, a joined album's MusicBrainz Item is gone, or a stray still holds
  rows. Then strays are repaired, as `check --repair` does.
- `apply` reads the albums' dates and genres in one background Apple Music request per
  100 albums, and leaves an album Apple Music no longer has.

### Moving user rows

When an album joins another, and when `check --repair` joins a stray, the rows of each
user who has none on the target Item move there by foreign-key update: tracking
(`Music`), logs, likes, rows in the user's own lists (not featured ones), activity and
progress, and the Hall of Fame and profile backdrop picks.

- A user with rows on both Items keeps all of them where they are: moving only some
  would part a tracking row from the log that holds its rating. `report` and `check`
  list them, for moved albums and for reverted ones.
- Every move is recorded on the link row (`moved_rows`), so `revert` can undo it; a
  stray's rows stay with the album instead (see Undoing).
  Nothing is deleted, and history is unaffected, because the history tables don't
  store the Item.
- Not moved: featured list rows, cover picks, notification exclusions, and rows
  derived from the provider (genre filters, calendar events, external ratings,
  series). `check` counts rows left on an old Item in any table it doesn't know, so a
  table added later shows up.

A **stray** is a MusicBrainz Item made for an album after it moved, by a path that
didn't send the old ref to the album's Item. Canonical ref rule 2 already hides it from
every read, and `check --repair` joins its users' rows.

### Undoing (`revert`)

- **`revert --mbid M`** undoes one album:
  - A re-keyed album gets back its source, ID, title, cover, accent colour, dates and
    filters, and its cover picks apply again.
  - A joined album's moved rows go back to its MusicBrainz Item. The Item it joined
    keeps its own Apple ref: its other rows (an album first added from an Apple page,
    or another album's move) are still there.
  - If a stray has taken a re-keyed album's old ID, the stray's users' rows join the
    album instead, and the stray takes the album's Apple ID, so the album still gets
    its own Item back: its pk, saved fields and cover picks. Rows `check --repair`
    moved from the stray stay on the album. A user with rows on both keeps the
    stray's where they are; the stray then keeps the Apple ref, and `report` and
    `check` list the album until a later `revert --all` can join them.
  - A manual pick whose Apple album has an Item that never moved goes to MusicBrainz,
    as `--all` does below. An Apple album that a moved album holds is left alone
    ("nothing to revert: a moved album holds its Apple album").
- **`revert --all`** refuses while the catalog is `apple_music`: switch it back first.
  It undoes every move, newest first. Then each Apple Music album that never moved
  (first added from an Apple page) goes to MusicBrainz:
  - into the MusicBrainz album that had joined it, if one did;
  - else, when its bridge names a release group with the same title, under a `bridge`
    link row: its rows join that release group's MusicBrainz Item if there is one, or
    it is re-keyed with MusicBrainz's title and cover (one request, at MusicBrainz's
    pace);
  - else it is counted ("left on Apple Music: no bridge") and listed by `report`, for
    `link` and then `revert --mbid`.

  A user with rows on both keeps them where they are ("Apple album kept: users have
  rows on both"), and the album keeps its own Apple ref. A release group whose row a
  move already reverted keeps that row, and the Apple album that names it stays on
  Apple Music with its own ref ("left on Apple Music: its MusicBrainz album was
  reverted already").
- A reverted row is `reverted`. Once no Apple Item of the album holds user rows, it
  names the album's Apple identity, so the Apple refs the album was known by reach its
  MusicBrainz Item again (canonical ref rule 1). That is why a single album can be
  reverted even with the catalog on Apple Music.
- The backup taken before the run is the last resort.

### Edge cases

- **One MusicBrainz album, several Apple albums** (SOS and SOS Deluxe: LANA): everything
  goes to the pick, and the deluxe starts empty.
- **No match:** the album stays on MusicBrainz, with its page; `report` lists it and
  `link` can fix it.
- **Review:** the album waits for a manual pick.
- **Cover picks** stay saved, aren't used on Apple Music albums, and are used again
  after `revert`.

### On production (`scripts/music-catalog-production.sh`)

```
scripts/music-catalog-production.sh <check|map|report|link|apply|revert> [args...] [--confirm]
```

- Run it only on request, from the server Mac (AGENTS.md, section 16).
- **Preflight:** production answers its health check, the running container's commit
  is `~/projects/spine-deploy`'s, and migration `app 0086_apple_music_catalog` is
  applied.
- **Backup first:** `link`, `apply`, `revert` and `check --repair` need `--confirm`,
  and first write a `pg_dump -Fc` to `~/projects/spine-deploy/backups/music-catalog/`,
  checked with `pg_restore --list` and readable by its owner only.
- It runs `manage.py music_catalog` in the app container with production's Compose
  project and environment file, as `scripts/external-ratings-production.sh` does.
- `report` writes its CSV to the same private folder, because it holds library
  titles; the console shows counts only. Nothing from it is committed.

A move goes in this order:

1. **Rehearse** on the test backend, with the branch served by
   `scripts/ios/test-backend.sh use`: log a dozen MusicBrainz albums as the dev user
   (SOS, Thriller, a self-titled album, an album with a clean twin, No Fences), then
   run `map`, `report`, `apply`, `check --repair` and the fallback below.
2. `check`, which tests the key.
3. `map` at night, then `report`; record a pick for each `review` album with `link`.
4. `apply --limit 10 --confirm`, look at those albums, then `apply --confirm` for the
   rest, then `check --repair --confirm`.

## Falling back to MusicBrainz

The MusicBrainz provider, its routes and search, the genre table, the featured
adapters, the cover picker and MusicBrainz ratings stay permanently. To go back:

1. Set `MUSIC_CATALOG_SOURCE=musicbrainz` in production's environment (or remove it)
   and deploy. Search, genre pages and featured lists go back to MusicBrainz. Albums
   that moved still open, as Apple Music albums.
2. `scripts/music-catalog-production.sh revert --all --confirm`, which backs up first.
   Moved albums get back their MusicBrainz identity, title, cover, genres, accent
   colour and cover picks, and moved rows go back. Albums first added from Apple pages
   go to MusicBrainz through their bridge; the rest, and users with rows on both, are
   listed.
3. To stop every Apple Music request too, remove the three key settings and deploy. An
   Apple Music album left over opens from its Item.
4. Only then may a commit from before step 2 be deployed again (`scripts/ios/SHIP.md`,
   Workflow B).

Moving the albums again later means `map`, then `apply`, run back to back. `map` turns
reverted rows back into matched ones, so until `apply` runs, an Apple Music link saved
before the fallback opens the Apple Music album without the user's logs.

`app.tests.test_music_catalog_backfill` runs this end to end: it moves an album,
switches the catalog to Apple Music and back, runs `revert --all`, and checks that the
album's MusicBrainz page, its search summary, its Item and every user row are as they
started.

## Fixtures

Recorded responses live in `src/app/tests/mock_data/apple_music/`.

- `scripts/music/record_apple_fixtures.py --token-file <file>` records them. The token
  file must be mode 600 and outside the repository. `--only catalog` records the
  listing-rule and client fixtures, `--only album_pages` the album pages
  (`page_*.json`), and `--only search` the search pages with their families
  (`search_albums_*.json`, `search_families_*.json`), an artist search and SZA's
  artist page (`person_sza*.json`). `--only related` records the related sections'
  fixtures from the recorded album pages: each page's shared-songs lookup and batch
  (`editions_songs_*.json`, `editions_albums_*.json`, and Kill Bill's
  `USRC12204584`), its artist's albums (`artist_albums_*.json`), and the For All the
  Dogs, Scary Hours Edition and Who's Next (Remastered 2022) pages. `--only bridge`
  records the MusicBrainz answers the bridge reads for the SOS and LANA pages
  (`musicbrainz_barcode_*.json`, `musicbrainz_release_*.json`,
  `musicbrainz_release_group_*.json`); it needs no token and keeps MusicBrainz
  requests 3.5 seconds apart. `--only backfill` records what the data move's matcher
  reads for SOS, First Love (credited in Japanese on MusicBrainz) and No Fences (not on
  Apple Music): each release browse (`musicbrainz_releases_*.json`), Apple Music's
  answer to its barcodes (`backfill_upc_*.json`) and the matched album's family
  (`backfill_family_*.json`), and for No Fences one release's ISRCs, Apple Music's songs
  for them and a search.
- The script trims each response to the fields the code reads. It refuses to write a
  file containing anything that looks like a token.
- The tests sign with a throwaway key, and they fail on any request that would reach the
  network.

## Contracts

`docs/music/contracts/apple-album-detail.example.json` and
`apple-song-detail.example.json` are the rendered album and song responses, which the
Swift contract tests decode. `src/api/tests/test_music_apple_contract.py` checks that
every key the app requires is present and not null, including on every related album,
checks the related sections' titles and sizes, and compares the responses with these
files. After a deliberate change, rewrite them by running that test with
`UPDATE_MUSIC_CONTRACTS=1`.
