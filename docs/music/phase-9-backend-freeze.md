# Music Phase 9: Backend API Freeze

Status: **frozen for iOS implementation**

API version: **v1**

Contract snapshot: **2026-07-16**

These are the Django contracts iOS may implement against. Changing a documented
field name, JSON type, nullability rule, pagination envelope, or success status
requires an intentional v1 contract review with the Swift client.

## Response examples

The examples are rendered DRF responses from a deterministic end-to-end API
test, not hand-written schema approximations:

- [Phase 9 API response bundle](contracts/phase-9-api-responses.example.json)
- [Full album detail](contracts/album-detail.example.json)
- [Track](contracts/track.example.json)
- [Full recording detail](contracts/song-detail.example.json)

The Phase 9 bundle keys name the response family they freeze. Endpoints that
return the same serializer shape deliberately reuse a key.

The enforcing test is
[`src/api/tests/test_music_phase9_contract.py`](../../src/api/tests/test_music_phase9_contract.py).
Update the Django fixture and Swift decoding together; a fixture-only update is
not an acceptable contract change.

## Frozen endpoints

| Contract | Request | Success | Example key |
| --- | --- | --- | --- |
| Metadata | `GET /api/v1/meta/` | `200` | `meta` |
| Music search | `GET /api/v1/media/search/?media_type=music&q={query}&page={page}` | `200` | `music_search` |
| Album detail | `GET /api/v1/media/musicbrainz/music/{release_group_mbid}/` | `200` | `album_detail` |
| Recording detail | `GET /api/v1/media/musicbrainz/music/{release_group_mbid}/recordings/{recording_mbid}/` | `200` | `recording_detail` |
| Tracking detail | `GET /api/v1/tracking/{source}/{media_type}/{media_id}/` | `200`, missing tracking `404` | `tracking_detail` |
| Tracking update | `PUT` or `PATCH` on the tracking detail URL | `200` | `tracking_update` |
| Tracking action | `POST /api/v1/tracking/{source}/{media_type}/{media_id}/actions/{action}/` | `200` | `tracking_action` |
| Diary create | `POST /api/v1/diary/` | `201` | `diary_create` |
| Diary detail | `GET /api/v1/diary/{entry_id}/` | `200` | `diary_detail` |
| Diary list | `GET /api/v1/diary/` | `200` | `diary_list` |
| Lists | `GET /api/v1/lists/` | `200` | `lists` |
| List create | `POST /api/v1/lists/` | `201` | `list_create` |
| List detail | `GET /api/v1/lists/{list_id}/` | `200` | `list_detail` |
| Add list item | `POST /api/v1/lists/{list_id}/items/` | `201` | `list_item_add` |
| Current/public profile | `GET /api/v1/me/` or `GET /api/v1/users/{username}/` | `200` | `profile` |
| Preferences | `PATCH /api/v1/me/preferences/` | `200` | `preferences` |
| Hall of Fame | `GET /api/v1/me/hof/`, `PUT` or `DELETE /api/v1/me/hof/{media_type}/` | `200` | `hall_of_fame` |
| Activity | `GET /api/v1/users/{username}/activity/` | `200` | `activity` |
| Statistics | `GET /api/v1/stats/me/summary/` or `GET /api/v1/users/{username}/stats/summary/` | `200` | `statistics` |
| Filter options | `GET /api/v1/filter-options/?scope={tracking,diary,list}` | `200` | `filter_options` |

Music search requires authentication. Album and recording detail allow anonymous
reads. Tracking, diary writes, lists, preferences, and current-user data require
authentication. When `MUSIC_ENABLED=False`, music is omitted from metadata and
direct music search, detail, recording, tracking, diary, list-item, and Hall of
Fame access is rejected with `404`.

Malformed query parameters or request bodies return DRF field errors with `400`.
Protected routes return `401` without valid authentication. Missing resources
return `404`; music exposure also intentionally uses `404` so a disabled media
type is not discoverable.

Provider failures use the existing standard API error envelope:

```json
{
  "error": {
    "code": "provider_unavailable",
    "message": "MusicBrainz is temporarily unavailable.",
    "request_id": "opaque-request-id"
  }
}
```

The iOS client decodes that nested envelope and presents the provider message
and request ID for `provider_unavailable`, so the user gets a retryable state
instead of an unrelated generic fallback. Unknown `5xx` bodies and upstream
proxy/CDN failures remain deliberately redacted to a safe generic message. DRF
field-error dictionaries are unchanged; this documents existing wire behavior
and adds no endpoint or response field.

Music search accepts `q` (required), `page` (one-based, default `1`), and
`media_type=music`. Its body is empty and its provider pagination fields are
`page`, `total_pages`, `total_results`, and `results`.

### Representative release and tracklist

This section describes MusicBrainz albums. An Apple Music album fills the same
fields from the Apple Music album, and its song pages have their own route; see
[apple-music-catalog.md](apple-music-catalog.md).

There is no separate representative-release endpoint. Album detail returns it at
`music.representative_release`; the complete ordered tracklist is
`music.representative_release.media[].tracks[]`. A missing usable release is the
explicit degraded state `representative_release: null`. Track and Recording MBIDs
remain distinct, and recording detail returns `404` unless the Recording MBID is
present on the representative release in the requested album context.

Album detail is the complete tracklist boundary. Loading or scrolling an album
must not issue a request per track; recording detail is requested only after the
user opens a track. Release-group detail, release candidates, representative
release data, recording detail, and Cover Art Archive hits or misses use the
versioned server cache described in Phase 0. Reopening a cached album therefore
must not call MusicBrainz again. Clients may retry explicit provider and network
error states, but must not replace a successful newer search with a stale result
from an older request.

### Tracking requests

`PUT` and `PATCH` accept `status`, `rating`, `progress`, `start_date`, `end_date`,
`notes`, and `season_number`; music ignores numeric progress and always returns
the binary album progress object. The music action used by iOS is `consume`, with
an optional ISO-8601 `consumed_at`. Ratings are decimal strings in responses.

### Diary requests

Create requires `ref`; supported fields are `consumed_at`, `rating`, `review`,
`review_title`, `liked`, `is_rewatch`, `auto_mark_consumed`, `contains_spoilers`,
`visibility`, and `tags`. Detail and create share the same response shape. List
uses the standard `{count,next,previous,results}` page envelope.

### List requests

iOS may also use the following existing operations:

- `GET /lists/?ref[source]=...&ref[media_type]=...&ref[media_id]=...` returns the
  `lists` envelope with `has_item` on each result.
- `PATCH /lists/{id}/` and `PATCH /lists/{id}/items/reorder/` return the
  `list_detail` family.
- `GET /lists/{id}/items/` returns the standard page envelope of media summaries.
- `DELETE /lists/{id}/` and `DELETE /lists/{id}/items/{item_id}/` return `204`
  with no body.
- `GET /lists/{id}/?include_items=false` returns list detail without the `items`
  member.

### Profile, preferences, and Hall of Fame

`GET /me/` and public profile reads have no body. Preferences accepts a partial
object containing keys exposed by the `preferences` example; omitted keys are
unchanged. Hall of Fame `PUT` accepts `{ "ref": { "source", "media_type",
"media_id" } }`; `DELETE` has no body. `GET /me/hof/` returns the same
media-summary family grouped by enabled media type.

### Activity, statistics, and filters

Activity uses cursor pagination (`cursor` query parameter). Statistics accepts
the optional `range`, `start_date`, and `end_date` query parameters documented
by its response. Filter options requires `scope`; iOS passes `media_type=music`
for tracking and diary filters. These endpoints have no request body.

### JSON conventions

- Keys use `snake_case`; MBIDs are lowercase UUID strings. On an Apple Music album
  the `*_mbid` fields hold Apple Music IDs (see apple-music-catalog.md).
- Dates use ISO 8601. MusicBrainz metadata may preserve `YYYY`, `YYYY-MM`, or
  `YYYY-MM-DD`; API-created timestamps include an offset.
- Nullable values are JSON `null`, never an empty object or fabricated value.
- Collection ordering is part of the contract: tracklists are disc/position
  ordered, diary and activity are reverse chronological, and ranked lists expose
  integer positions.
- Standard pages use `{count,next,previous,results}`. Activity uses
  `{next_cursor,previous_cursor,results}`.
- The example IDs are deterministic fixture IDs; clients must treat all database
  IDs as opaque installation-specific integers.

## Validation record

The deterministic contract flow covers metadata, provider-backed music search,
album and recording detail, tracking, diary, lists, Hall of Fame, profile,
preferences, activity, statistics, and filter options. It uses the Year Zero
release group `3bd76d40-7f0e-36b7-9348-91a33afee20e`, representative release
`2d0bad69-f735-484b-bc0b-2ea54c76225e`, and recording
`35518724-a25a-4627-a2cc-0786dd1d2272`.

Validated on **2026-07-16**:

- Phase 9 targeted/provider/model/music/API/profile/stats/event suites: **144
  passed**, one opt-in provider test skipped.
- Complete `api.tests`: **194 passed**.
- Broad `app users integrations lists events social` suite: **793 passed**, 32
  skips. The import pagination assertion and four anime webhook tests were made
  deterministic before the successful rerun.
- `makemigrations --check --dry-run --settings=config.test_settings`: no changes.
- `ruff check src`: passed.

Live validation used `MUSIC_ENABLED=True`, a fresh migrated temporary SQLite
database, a disposable user transaction, and an isolated Redis 8 container. All
17 search/detail/recording/tracking/consume/diary/list/Hall of
Fame/activity/statistics requests returned `200` or `201`. The album exposed
Cover Art Archive artwork, representative release
`2d0bad69-f735-484b-bc0b-2ea54c76225e`, an ordered 16-track list, and recording
`35518724-a25a-4627-a2cc-0786dd1d2272`.

The cold flow made six upstream calls. Repeating search, album detail, and
recording detail made **zero** additional upstream calls. Redis contained the
versioned MusicBrainz search/release-group/release/recording/representative
release keys, the Cover Art Archive key, API search/detail keys, DRF throttle
keys, and the shared `phase9live_musicbrainz_api` limiter bucket. Actual
MusicBrainz dispatch intervals were `2.128`, `1.593`, `1.056`, `1.054`, and
`1.048` seconds, satisfying the one-request-per-second ceiling. The temporary
database transaction was rolled back and the Redis container removed after the
check.

## Deployment handoff

Deployment is performed from the other computer after `search-page-ios` is
pushed:

1. Confirm the production Compose configuration sets `MUSIC_ENABLED=True`.
2. Deploy the pushed `search-page-ios` commit with the existing backend workflow.
3. Confirm migrations complete and clear Django cache.
4. Verify `/api/v1/health/` succeeds.
5. Verify `/api/v1/meta/` includes `music` and `musicbrainz`.
6. Verify music search, album, recording, and tracking routes are available to
   normal authenticated clients.

Public music exposure still requires the project owner to satisfy the separate
MetaBrainz account and licensing requirements recorded in Phase 0.
