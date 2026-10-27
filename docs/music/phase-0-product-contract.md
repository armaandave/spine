# Music Phase 0: Product and Data Contract

Status: **product contract locked; production licensing confirmation pending**

Fixture snapshot: **2026-07-16**

Default market for the fixture expectations: **US**

This document is the source of truth for Spine's first MusicBrainz-backed music implementation. Later phases may refine implementation details, but they must not change these product semantics or public identities without first updating this decision record and its contract fixtures.

## Locked product scope

| Concern | Decision |
| --- | --- |
| Media type slug | `music` |
| Primary provider | `musicbrainz` |
| Trackable identity | MusicBrainz Release Group MBID |
| User-facing trackable objects | Albums, EPs, singles, compilations, soundtracks, and other MusicBrainz release-group types |
| Song identity | MusicBrainz Recording MBID |
| Track identity | MusicBrainz Track MBID, scoped to a release and medium |
| Default market setting | One server setting named `MUSIC_DEFAULT_MARKET`; initial value `US` |
| Artwork | Cover Art Archive release-group artwork; otherwise Spine's existing fallback |
| Release event | The release group's first release only |

All release-group primary and secondary types remain under the single `music` media type. Do not introduce separate `album`, `ep`, `single`, or `song` media types.

MusicBrainz's model is the reason for the identity split: a [release group](https://musicbrainz.org/doc/Release_Group) is the album concept; a [release](https://musicbrainz.org/doc/Release) is a particular edition; a [track](https://musicbrainz.org/doc/Track) is an entry on one medium of one release; and a [recording](https://musicbrainz.org/doc/Recording) is distinct audio that can be referenced by many tracks.

### Tracking and diary semantics

Spine keeps its existing stored status values and changes only the music-facing labels:

| Stored/API status | Music UI label |
| --- | --- |
| `Planning` | Planning |
| `In progress` | Listening |
| `Completed` | Listened |
| `Paused` | Paused |
| `Dropped` | Dropped |

- Album completion is binary. Music never exposes track-count progress, a current-track pointer, or a percentage.
- Completing a music item creates the normal album diary entry and leaves the item `Completed`.
- Repeating that action on an already completed music item is labeled **Relisten** and creates another album diary entry through the existing repeat-log behavior.
- Ratings, reviews, likes, custom-list membership, library state, community aggregates, and diary entries attach to the release-group-backed `Item`.
- A song page is a metadata-only child of an album. It does not create an `Item` and cannot be tracked, rated, reviewed, logged, liked, listed, or added to the library.

### Explicit exclusions

This integration does not include lyrics, playback, audio previews, recommendations, charts, song-level social data, or song-level consumption history. Streaming relationships are used to select a representative release; they do not create playback controls.

## Identity and API integration

The existing common media identity fits MusicBrainz without a schema expansion:

```text
source = musicbrainz        # 11 characters; existing limit is 20
media_type = music          # 5 characters; existing limit is 10
media_id = release-group MBID # 36 characters; existing limit is 36
season_number = null
episode_number = null
```

Album search and detail use Spine's existing generic endpoints:

```text
GET /api/v1/media/search/?media_type=music&q=...
GET /api/v1/media/musicbrainz/music/{release_group_mbid}/
```

The generic media detail response gains one optional typed `music` object, in the same additive style as its existing typed media-specific fields. Existing clients may ignore it. Song detail is deliberately nested and read-only so a Recording MBID cannot accidentally enter generic tracking APIs:

```text
GET /api/v1/media/musicbrainz/music/{release_group_mbid}/recordings/{recording_mbid}/
```

The server must verify that the requested recording occurs on the representative release before treating it as a child in this route. It returns `404` when the recording is not in that album context. The recording remains the song identity; the release group and Track MBID provide navigation context only.

Contract examples:

- [Album detail](contracts/album-detail.example.json)
- [Track](contracts/track.example.json)
- [Song detail](contracts/song-detail.example.json)

The contract shapes and provider MBIDs are normative. Spine database IDs, viewer state, and community aggregate values in the album example are illustrative because they are installation- and user-specific.

Wire-format rules:

- JSON uses `snake_case`, matching the current Django API.
- MBIDs are lowercase UUID strings.
- MusicBrainz date precision is preserved: `YYYY`, `YYYY-MM`, and `YYYY-MM-DD` are all valid strings. Missing dates are `null`; Spine must not invent month or day values.
- `release_date` on the common album detail is the release group's first release date. The chosen edition's possibly later date lives at `music.representative_release.date`.
- `music.representative_release.media[].tracks` is the complete ordered tracklist, not a preview.
- Representative releases expose their shared format (or `null` for mixed formats), edition-marker flag, streaming links, disc count, and total track count. Every nested track repeats its disc number and preserves its printed `number`; recording objects include an `isrcs` array.
- `artist_credit` is an ordered array with `join_phrase`; do not flatten multi-artist credits into an identity-bearing string.
- Track duration and recording duration are distinct nullable values. The album row displays `track.length_ms`; the song page displays `recording.length_ms`.

## Representative release policy

> This policy applies to MusicBrainz albums only. An Apple Music album
> (`source: apple_music`) takes its tracklist from the Apple Music album itself; see
> [apple-music-catalog.md](apple-music-catalog.md).

Representative release selection supplies a stable, useful tracklist for the release-group-backed album page. It never changes the album's Spine identity.

### Eligibility and selection tiers

1. Load every release in the release group, paging beyond MusicBrainz's 100-result limit.
2. Build Tier 1 from releases that are all of the following:
   - status `Official`;
   - every audio medium has format `Digital Media`;
   - at least one release-to-URL relationship has MusicBrainz relationship type `free streaming` (`08445ccf-7b99-4438-9f9a-fb9ac18099ee`) or `streaming` (`320adf26-96fa-4183-9045-1f5f32f833cb`).
3. If Tier 1 is non-empty, choose the lowest lexicographic rank produced by these criteria, in this order:
   1. country rank: exact `MUSIC_DEFAULT_MARKET`, then `XW`, then unknown, then other countries ordered by ISO code;
   2. title rank described below;
   3. complete tracklist before incomplete tracklist;
   4. earliest release date;
   5. lexicographically smallest release MBID as the final stable tie-breaker.
4. If Tier 1 is empty, choose the earliest `Official` release with a complete tracklist, then break an equal-date tie by release MBID.
5. If that set is empty, choose the earliest release of any status with a complete tracklist, then break an equal-date tie by release MBID.
6. If no release has a complete tracklist, album metadata may still load, but `representative_release` is `null` and song navigation is unavailable. This is an explicit degraded state, never a fabricated tracklist.

These tiers encode the requested order: official digital streaming, market, ordinary title, completeness, date, earliest official complete fallback, then any complete fallback.

### Title rank

Normalize both titles with Unicode case folding, whitespace collapse, and punctuation-insensitive comparison.

1. Exact normalized match to the release-group title.
2. Match after removing a recognized edition marker.
3. Different title without a recognized edition marker, such as a localized title.
4. Different title containing an edition marker.

The initial marker set is: `deluxe`, `d.l.x.`, `dlx`, `remaster`, `remastered`, `anniversary`, `expanded`, `special edition`, `collector edition`, `collector's edition`, `bonus`, `legacy edition`, `super deluxe`, and `definitive edition`. Markers apply in parenthetical, bracketed, colon-suffix, dash-suffix, and trailing-title forms. The `1989` fixture exists specifically to prevent `D.L.X.` from beating the standard title.

Do not treat translated or transliterated titles as deluxe markers. They remain eligible and can win when they are the only otherwise-qualified streaming release.

### Complete tracklist

A candidate is complete only when:

- it has at least one medium;
- every medium reports a positive track count;
- a full selected-release lookup returns exactly that many tracks for every medium; and
- every returned track has both a Track MBID and a Recording MBID.

For efficiency, ranking may first use medium track counts. Before accepting a winner, the server must perform or use a cached full recording lookup. If verification fails, remove that candidate and rerun selection.

### Partial and absent dates

For sorting only, missing month and day compare as `01`; lower-precision dates sort after a full date with the same computed value. An absent date sorts last. The original precision is always returned to clients unchanged.

### Configuration boundary

`MUSIC_DEFAULT_MARKET=US` is a server-wide default. Do not add a user field, preference endpoint, migration, or iOS setting for music market in the initial integration.

## Artwork and release events

- Album artwork uses `https://coverartarchive.org/release-group/{release_group_mbid}/front-500` or the equivalent URL returned by the [release-group Cover Art Archive API](https://musicbrainz.org/doc/Cover_Art_Archive/API).
- A Cover Art Archive `404` is a normal cacheable miss and maps to Spine's existing fallback. It is not a provider error shown to the user.
- The artwork source release chosen by Cover Art Archive does not have to equal Spine's representative release.
- Song pages reuse their parent album's resolved artwork. There is no independent song artwork lookup.
- The album's calendar/release date is the release group's first release date, never the representative edition date and never a reissue/remaster date.
- Create one calendar event from the MusicBrainz release group's first release date. A full `YYYY-MM-DD` uses that day, `YYYY-MM` uses the first day of that month, and `YYYY` uses January 1. The original precision remains unchanged in metadata.
- A future first-release date creates the same single upcoming event as any other supported media type.

## Fixture corpus

The expected selections below were computed from the live MusicBrainz API on 2026-07-16 with `MUSIC_DEFAULT_MARKET=US`. Automated unit tests must use recorded provider fixtures, not live calls. A separate opt-in audit may compare current live data and update expectations only through review, because community data and merged MBIDs can change.

| # | Release group and MBID | Expected representative release | Coverage |
| --- | --- | --- | --- |
| 1 | Pink Floyd — *The Dark Side of the Moon* (`f5093c06-23e3-404f-aeaa-40f72885ee3a`) | `82e70b1b-5165-4d04-9951-959b707bdb39` — XW digital, 2016, 10 tracks | 146 releases; many countries/formats; reissues/remasters; over 25 releases |
| 2 | Nine Inch Nails — *Year Zero* (`3bd76d40-7f0e-36b7-9348-91a33afee20e`) | `2d0bad69-f735-484b-bc0b-2ea54c76225e` — XW digital, 2016-09-02, 16 tracks | Apple Music relationship; missing annotation; ordinary album contract example |
| 3 | Nine Inch Nails — *The Fragile* (`fc4d7589-a6b9-35ca-a6b6-8c8e70c3baaa`) | `4771bdd1-7e9e-42f3-b8e7-b85c71c39024` — XW digital, 2015, 2 media/23 tracks | Multi-disc album |
| 4 | Various Artists — *Pulp Fiction: Music From the Motion Picture* (`1703cd63-9401-33c0-87c6-50c4ba2e0ba8`) | `729642fc-cd24-416d-9f14-4611b04a0614` — XW digital, 2019-03-15, 20 tracks | Soundtrack and Various Artists credit |
| 5 | 久石譲 — *千と千尋の神隠し サウンドトラック* (`dc1f9d7d-9a98-3f2c-83aa-c16dbb4a9ae1`) | `80eec3d5-a44a-43b6-a95b-b4004461c6ca` — XW digital, unknown date, 21 tracks | Non-Latin group title; translated release title; absent selected-release date |
| 6 | Nirvana — *MTV Unplugged in New York* (`fb3770f6-83fb-32b7-85c4-1f522a92287e`) | `195973ed-07eb-4db0-951b-515fdb6dc20c` — XW digital, 2005, 14 tracks | Live album; 46 releases |
| 7 | Alice in Chains — *Jar of Flies* (`3b6b7688-c97b-3d11-9334-3158468074f7`) | `cb7b887a-44bf-4a59-a5da-d229c0357128` — XW digital, 2014-07-25, 7 tracks | EP primary type |
| 8 | Nine Inch Nails — *Survivalism* (`8a772dab-6832-34cd-85c6-a9f666058593`) | `09fc1530-ccb4-44c5-96ad-629a9d783f70` — digital, unknown country, 2007-04-02, 2 tracks | Single primary type; bonus/remix editions in one group |
| 9 | Madonna — *The Immaculate Collection* (`b14e9927-081e-4a07-b0f7-8ddf22319b27`) | `e3584562-03f0-428e-8aeb-abc96b46cd73` — digital, unknown country, 2023-05-05, 17 tracks | Compilation secondary type |
| 10 | Daft Punk — *Random Access Memories* (`aa997ea0-2936-40bd-884d-3af8a0e064dc`) | `ec116461-5b0d-4c98-bb44-a4de5de63076` — US digital, 2013, 13 tracks | Exact configured-market win; anniversary/deluxe alternatives |
| 11 | Taylor Swift — *1989* (`4d9ec1c2-58ec-48a4-aa0a-916718adead0`) | `f04b5341-454f-4085-b90d-82d5c6a6cb82` — US digital, 2025-06-06, 13 tracks | 97 releases; standard vs `D.L.X.`; Qobuz subscription-streaming relation |
| 12 | Uruk-Hai — *Elbentanz* (`87199163-cf50-3c84-8774-e09c5de47d6a`) | `f2de8bce-e736-4318-928e-63a1a1e38914` — AT CD, 2003, 14 tracks | Exactly one release; year-only date; no streaming relation; missing annotation; Cover Art Archive 404 |
| 13 | Tylor Walsh — *Frøstbytę* (`e13cb9ab-19b2-4df6-bb50-20a7cdd65f11`) | `dcdba0f4-c979-4947-bfc2-b820406e9cad` — CA CD, 2026-12-17, 5 tracks | Future first release; exactly one release; Cover Art Archive 404 |
| 14 | Various Artists — *Now That’s What I Call Music* (`bcd7ac33-7a46-346d-b2f2-72dcf51012af`) | `7e917011-ab60-41c3-a65f-1fe2638e29ae` — GB digital, 2018-07-20, 2 media/30 tracks | True Various Artists compilation; Spotify relationship |
| 15 | Pink Floyd — *The Making of The Dark Side of the Moon* (`fc851e0c-0261-472b-847c-a0cf8fedf80f`) | `65071ca2-6d90-4b35-a7ac-c5057ce28123` — DE DVD-Video, 2003, 23 tracks | Primary type `Other`, secondary type `Interview`; non-digital fallback |

### Recording-identity fixture

These are deliberately three different song pages even though their names share the same underlying song concept:

| Variant | Recording MBID | Expected distinction |
| --- | --- | --- |
| Studio | `97d09e1b-8812-45fd-830f-0200a3c0e3b8` | *Survivalism*, 264000 ms, official studio recording |
| Remix | `04bd34a4-bb7c-4acc-885b-0a643e7e3d4b` | *Survivalism (deadmau5 remix)*, 217500 ms |
| Live | `61e7a5b8-5fdb-4524-8983-bdb7ca243a52` | *Survivalism*, 2009-09-10 at The Wiltern, 301000 ms |

Do not merge these by normalized title, ISRC, or work relationship. Recording MBID is authoritative.

## API and ingestion constraints for later phases

- All MusicBrainz and Cover Art Archive calls are server-side. The iOS app calls Spine only.
- The shared MusicBrainz limiter must enforce no more than one request per second across processes and must send a meaningful contactable User-Agent. MusicBrainz throttling is HTTP `503`, so provider retry behavior must cover `503`, not only `429`.
- Search, group detail, release candidates, selected-release tracklist, recording detail, and Cover Art Archive misses need cacheable, versioned representations using Spine's existing Django/Redis cache conventions.
- Do not poll MusicBrainz for changes. Normal cached reads and deliberate fixture refreshes are sufficient.
- Do not ingest the canonical CSV dataset initially. The [canonical release mapping](https://musicbrainz.org/doc/Canonical_MusicBrainz_data) is a separate synchronized dataset and its choice can change between dumps.
- Reconsider canonical data only if the frozen corpus cannot pass 15/15 after reasonable deterministic policy corrections, or if at least three fixtures require one-off exceptions. Any adoption needs its own sync, cache invalidation, licensing, and operations phase.

## In-app attribution

The iOS product identifies MusicBrainz as the metadata source and Cover Art
Archive as the artwork source in Profile Settings under Data Sources, with links
to both projects. Album detail also keeps the existing concise metadata/artwork
attribution visible near provider-backed content. Artwork accessibility labels
describe the image as an album cover without implying that Spine, MusicBrainz,
or Cover Art Archive owns it.

This attribution is required product copy, not a substitute for the unresolved
production account and licensing decision below. The final wording and any
additional placement must be updated if MetaBrainz's written response imposes
specific attribution or share-alike terms.

## Licensing and production account gate

This is the only incomplete Phase 0 exit criterion. No MetaBrainz confirmation has been represented as received.

Official guidance currently says:

- the public web service is free for [non-commercial use](https://musicbrainz.org/doc/MusicBrainz_API), requires a meaningful User-Agent, and is limited to one call per second;
- MetaBrainz lists a $0+ [non-profit/open-source category](https://metabrainz.org/supporters/account-type), but also lists public mobile apps and public start-up products under Bronze at $100+/month and asks uncertain users to contact them;
- MusicBrainz core metadata is CC0, while annotations, tags/genre associations, ratings, derived statistics, and search indexes are supplementary CC BY-NC-SA data ([database breakdown](https://musicbrainz.org/doc/MusicBrainz_Database)); and
- Cover Art Archive images have separate copyright considerations; using the archive does not grant ownership of album artwork ([cover art guidance](https://musicbrainz.org/doc/Cover_Art)).

Before production launch, the owner must obtain written confirmation from [MetaBrainz](https://metabrainz.org/contact) covering:

1. whether Spine's open-source code plus public iOS product and its actual monetization plan qualify for the non-profit/open-source account or require Bronze/another commercial tier;
2. whether the planned server-side web-service usage and Redis caching are covered;
3. whether Spine may display supplementary annotations and genres in its actual product category, and what attribution/share-alike obligations apply;
4. whether any additional MetaBrainz requirement applies to Cover Art Archive access; and
5. the production User-Agent/contact identity they want Spine to use.

Until written confirmation arrives, implementation may use core fields and local development fixtures, but production launch is blocked. Annotations, community tags/genres, and MusicBrainz ratings must stay behind the licensing decision rather than silently entering a potentially commercial product.

Suggested contact summary:

> Spine is an open-source Django and SwiftUI media tracker preparing a public iOS music feature. Its server would call MusicBrainz WS/2 at no more than one request per second with a contactable User-Agent, cache search/detail data in Redis, use release-group MBIDs as album identity, use Recording MBIDs for read-only song metadata, and display Cover Art Archive release-group art. Please confirm the appropriate account/license tier for our actual monetization model and whether supplementary annotations/genres may be displayed.

Record MetaBrainz's reply, date, contact, account category, attribution requirements, supplementary-data decision, and any commercial terms in this section before marking Phase 0 complete.

## Exit checklist

- [x] Product and identity decisions recorded.
- [x] Representative-release algorithm made deterministic.
- [x] Album-detail, track, and song-detail JSON examples recorded.
- [x] Fifteen-release-group corpus and expected representatives recorded.
- [x] Studio/remix/live Recording MBIDs recorded.
- [x] Canonical CSV explicitly deferred.
- [ ] MetaBrainz has confirmed the production account/license category in writing.

## Primary references

- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [MusicBrainz API rate limiting](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting)
- [Release groups](https://musicbrainz.org/doc/Release_Group)
- [Release-group style and grouping](https://musicbrainz.org/doc/Style/Release_Group)
- [Releases and media](https://musicbrainz.org/doc/Release)
- [Recordings](https://musicbrainz.org/doc/Recording)
- [Tracks](https://musicbrainz.org/doc/Track)
- [Release-to-URL streaming relationships](https://musicbrainz.org/relationships/release-url)
- [Cover Art Archive API](https://musicbrainz.org/doc/Cover_Art_Archive/API)
- [MusicBrainz data licenses](https://musicbrainz.org/doc/About/Data_License)
- [MetaBrainz account types](https://metabrainz.org/supporters/account-type)
- [Canonical MusicBrainz data](https://musicbrainz.org/doc/Canonical_MusicBrainz_data)
