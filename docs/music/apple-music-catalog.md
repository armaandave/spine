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

The catalog setting still defaults to MusicBrainz, so search, people search and every
existing album are served from MusicBrainz. Only a request that names an `apple_music`
ref or source reaches Apple Music. No album has moved, so canonical refs change
nothing yet.

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
   exists, it goes to that MusicBrainz ref.
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
  stray MusicBrainz Item. Rule 2 hides it from every read, and the data move's repair
  joins its rows to the Apple Item.

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
    usual retries.
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
  request fails, the page shows the album asked for.
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
| `genres` | The bridge's MusicBrainz genres, else the Item's genre filters, else Apple's genres in MusicBrainz's words (`app/music_catalog.py`, without "Music"); never fewer than the Item has, because album pages rewrite its genre filters |
| `music.release_group_mbid`, `representative_release.release_mbid` | The Apple album ID |
| `music.cover_art` | `{source: "apple_music", release_group_mbid: <id>, fallback_used: true}` |
| `selection_basis`, `country`, `format`, `status` | `"apple_music"`, the storefront, `"Digital Media"`, `"Official"` |
| `media[]`, `tracks[]` | Grouped by `discNumber`; `track_mbid` and `recording_mbid` are the song ID (`-2` and so on when a song repeats); `number` is a string; `is_video` for music videos |
| `artist_credit[]` | One per Apple artist, joined as "A, B & C". `artist_mbid` is always null; the new `artist_source` and `artist_id` name the Apple artist |
| `streaming_links` | Apple Music first (`kind: "album"`), then Spotify: the exact album when the bridge or a link row knows it (`kind: "album"`), else Spotify's search for the artist and album (`kind: "search"`) |
| `score`, `annotation` | Null: no community score and no editor's notes |
| `related` | Empty for now |

New keys are optional additions: `music.content_rating` and each track's
`content_rating`, and each recording's `composer_name` and `source_url`.

**Cover art.** An Apple Music album always shows Apple's cover.
`app.curation.music_picks_apply` is false for Apple Music albums, and every reader of
saved poster and backdrop picks checks it. The picks stay saved, and apply again if an
album goes back to MusicBrainz.

**External ratings.** Apple Music albums have none. A MusicBrainz rating kept on an
album that moved to Apple Music isn't shown.

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

## Fixtures

Recorded responses live in `src/app/tests/mock_data/apple_music/`.

- `scripts/music/record_apple_fixtures.py --token-file <file>` records them. The token
  file must be mode 600 and outside the repository. `--only catalog` records the
  listing-rule and client fixtures, `--only album_pages` the album pages
  (`page_*.json`), and `--only search` the search pages with their families
  (`search_albums_*.json`, `search_families_*.json`), an artist search and SZA's
  artist page (`person_sza*.json`).
- The script trims each response to the fields the code reads. It refuses to write a
  file containing anything that looks like a token.
- The tests sign with a throwaway key, and they fail on any request that would reach the
  network.

## Contracts

`docs/music/contracts/apple-album-detail.example.json` and
`apple-song-detail.example.json` are the rendered album and song responses, which the
Swift contract tests decode. `src/api/tests/test_music_apple_contract.py` checks that
every key the app requires is present and not null, and compares the responses with
these files. After a deliberate change, rewrite them by running that test with
`UPDATE_MUSIC_CONTRACTS=1`.
