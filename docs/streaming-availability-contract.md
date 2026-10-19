# Streaming Availability Contract

Status: **agent-ready product contract**

Last reviewed: **2026-10-19**

This contract covers "where to watch" for movies and TV in Spine: a user's streaming country and services, the Where to Watch section on detail pages, and the Service filter on collections and Discover.
Only the numbered laws contain accepted product decisions.
The API section is the backend/iOS contract; change it here first if implementation needs a different shape.

## Terms

| Term | Meaning |
| --- | --- |
| Provider / service | A streaming service as identified by TMDB's watch-provider catalog (for example Netflix = 8). |
| Offer | One way to watch a title in a country: a provider plus an offer type. |
| Offer type | `flatrate` (shown as **Stream**), `free` (**Free**), `ads` (**Free with ads**), `rent` (**Rent**), `buy` (**Buy**). |
| Streamable | An offer whose type is Stream, Free, or Free with ads. Rent and Buy are never streamable. |
| Country / region | An ISO 3166-1 alpha-2 code from TMDB's watch-provider regions list. |
| My services | The providers the user has selected in Streaming Services settings. |
| Snapshot | Stored availability for one title in one country (`StreamingAvailability` plus its `StreamingOffer` rows). |

## Data source

### ST-001: Availability comes from TMDB watch providers, sourced from JustWatch

Spine reads availability from TMDB: `watch/providers` appended to movie/TV/season detail calls, `/{movie|tv}/{id}/watch/providers`, `/watch/providers/{movie|tv}?watch_region=`, `/watch/providers/regions`, and `/discover/{movie|tv}` with `with_watch_providers`.
No other availability source is used.
The data has no per-service deep links; the only link is TMDB's watch page for the title and country, which lists JustWatch links.

### ST-002: Only TMDB movies and TV shows have availability

Availability applies to items with `source = tmdb` and `media_type` movie or tv.
Seasons and episodes use their show's availability for filtering.
A season detail page shows the season-level availability TMDB returns for that season.
Other media types never show streaming UI, and the Service filter excludes them.

### ST-003: JustWatch is credited wherever availability appears

Every surface that shows availability shows **Powered by JustWatch**, linking to `https://www.justwatch.com`.
That includes the Where to Watch section and the Streaming Services settings screen.
The About screen credits both TMDB ("This product uses the TMDB API but is not endorsed or certified by TMDB.") and JustWatch.

## Preferences

### ST-100: Each user has one streaming country

The country is stored in the existing `User.watch_provider_region`.
The effective country is resolved in this order:

1. The saved country, when it is a 2-letter code.
2. The `X-Spine-Region` request header (the iOS device region), when the saved value is `UNSET` and the header is a known watch region code.
3. `settings.STREAMING_DEFAULT_REGION` (default `US`).

A saved empty string (the web "Disabled" choice) turns availability off for that user: detail pages return `where_to_watch: null` and the Service filter matches nothing.

### ST-101: Users choose their own services

Users select any number of services (at most 100) from the catalog for their country.
The catalog merges TMDB's movie and TV provider lists for the country, de-duplicated, ordered by JustWatch display priority for that country.
Selections are stored as `UserStreamingService` rows keyed by TMDB provider ID.
Changing country keeps existing selections; selected services missing from the new country's catalog stay selected and are still shown so the user can remove them.

### ST-102: Saving preferences starts an availability check

Saving the country or services queues a background check of the user's library (tracked, listed, and diary movie/TV titles) for that country.
The save itself never waits for TMDB.

### ST-103: A user's services are private

Services are never shown on profiles or to other users.
Filtering another user's public list by My services uses the viewer's services and country.

## Where to Watch on detail pages

### ST-200: Movie, TV, and season detail show Where to Watch

The section appears for TMDB movie, TV, and season detail, for the effective country.
It lists offer groups in this order, omitting empty groups: Stream, Free, Free with ads, Rent, Buy.
Inside each group, the user's services come first and are marked; the rest follow JustWatch display priority.
When at least one of the user's services streams the title, the section says so up front (for example "Streaming on Netflix").
Tapping a provider opens the title's TMDB watch page for that country in the browser.

### ST-201: Unavailable titles say so

When the title has no offers in the country, the section reads "Not available to stream in {Country}." and still shows the JustWatch credit.

### ST-202: Users without services are invited to add them

When the user has no services, the section offers a "Choose your streaming services" link to Streaming Services settings.

## Service filter

### ST-300: Collections can be filtered to what the user can stream

The Library (every shelf, including Planning), list detail, and Diary offer a **Service** filter for movie/TV scopes.
Options:

- **Any** (default): no availability filtering.
- **My services**: titles streamable on at least one of the user's services in their country.
- One or more specific services from the user's services: titles streamable on at least one of the chosen services.

The filter counts toward the active-filter badge like other filters.

### ST-301: The filter needs services and checked titles

My services is unavailable until the user has services; the filter sheet explains how to add them.
Titles not yet checked for the country are excluded from filtered results and reported as pending; the backend queues a check for them, and the UI tells the user availability is still being checked.

### ST-302: Discover can browse what is streaming on the user's services

Movie and TV Discover offers **On my services**.
It restricts TMDB discover results to streamable offers from the user's services in their country and may be used without a genre or year.

## Freshness

### ST-400: Stored availability is refreshed in the background

A snapshot is fresh for 24 hours.
A periodic task refreshes missing and stale snapshots for library, list, and diary titles in active countries (countries saved by users who have services, plus the default country), oldest first, within a bounded batch per run.
Detail views only add missing snapshots, from the metadata they already fetched, stamped as already stale so the periodic task replaces them with live data; they never change an existing snapshot and never make an extra TMDB call for availability.
TMDB's own data can trail JustWatch by about a day.

## API (v1)

All endpoints are under `/api/v1/` and require authentication.
Provider logo URLs use `https://image.tmdb.org/t/p/w154{logo_path}`.

### Provider object

```json
{ "provider_id": 8, "name": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/abc.png", "display_priority": 1 }
```

### `GET /streaming/regions/`

```json
{ "results": [{ "code": "US", "name": "United States" }] }
```

Sorted by name. Cached server-side.

### `GET /streaming/providers/?region=US`

`region` defaults to the user's effective country.

```json
{ "region": "US", "results": [ { "provider_id": 8, "name": "Netflix", "logo_url": "…", "display_priority": 1 } ] }
```

Movie and TV catalogs merged and de-duplicated; ordered by the country's display priority. Cached server-side for 24 hours. Unknown region: 400.

### `GET /me/streaming/` and `PUT /me/streaming/`

Response (both methods):

```json
{
  "region": "US",
  "region_name": "United States",
  "region_is_set": true,
  "enabled": true,
  "provider_ids": [8, 337],
  "providers": [ { "provider_id": 8, "name": "Netflix", "logo_url": "…", "display_priority": 1 } ],
  "attribution": { "name": "JustWatch", "url": "https://www.justwatch.com" }
}
```

- `region` is the effective country (ST-100); `region_is_set` is false when it came from the header or default.
- `enabled` is false only when the saved country is the empty "Disabled" value; then `region` is null.
- `providers` lists the selected services in display-priority order.

`PUT` body: `{ "region": "US", "provider_ids": [8, 337] }`. Both fields required.
Validation: region must be in the TMDB regions list; provider IDs must be unique positive integers present in that region's catalog or already stored; at most 100. Demo accounts get 403.
On success it replaces the selection, saves the region, queues the library check (ST-102), and returns the response above.

### Media detail: `where_to_watch`

`GET /media/{source}/{type}/{id}/` (and season detail) gains `where_to_watch`. The legacy `providers` field is unchanged.

```json
"where_to_watch": {
  "region": "US",
  "region_name": "United States",
  "link": "https://www.themoviedb.org/movie/693134-dune-part-two/watch?locale=US",
  "has_services": true,
  "on_my_services": true,
  "groups": [
    {
      "type": "flatrate",
      "label": "Stream",
      "providers": [
        { "provider_id": 1899, "name": "HBO Max", "logo_url": "…", "display_priority": 151, "is_mine": true }
      ]
    },
    {
      "type": "rent",
      "label": "Rent",
      "providers": [
        { "provider_id": 2, "name": "Apple TV Store", "logo_url": "…", "display_priority": 9, "is_mine": false }
      ]
    }
  ],
  "attribution": { "name": "JustWatch", "url": "https://www.justwatch.com" }
}
```

- `null` for non-TMDB or non-movie/TV/season items, and when availability is disabled.
- `groups` contains only non-empty groups, in ST-200 order. An empty `groups` array means not available in the country (ST-201).
- `link` may be null when TMDB has no page for that country.
- `on_my_services` is true when any streamable offer is one of the user's services.

### Service filter parameter

`service` on `GET /tracking/`, `GET /lists/{id}/items/`, and `GET /diary/`:

- `service=mine`: streamable on any of the user's services.
- `service=<provider_id>` (repeatable): streamable on any listed provider.

Rules: matching uses the effective country and streamable offer types only; seasons and episodes match via their show; non-movie/TV items never match; `service=mine` with no services matches nothing.
When `service` is present, the paginated response adds:

```json
"streaming": { "region": "US", "pending_count": 3 }
```

`pending_count` counts movie/TV titles in the unfiltered-by-service result that have no snapshot for the country; the backend queues checks for them (debounced per user).
It is 0 when the country is not active (ST-400), since no snapshots are stored there.

### Filter options: `streaming`

`GET /filter-options/` adds a `streaming` object for every scope:

```json
"streaming": {
  "available": true,
  "region": "US",
  "region_name": "United States",
  "services": [ { "value": "8", "label": "Netflix", "logo_url": "…" } ]
}
```

`available` is true when the scope can contain movie/TV items. `services` lists the user's services (empty when none).

### Discover

`GET /media/discover/` accepts `service=mine` or repeated `service=<provider_id>` for `media_type` movie or tv.
With `service`, genre/year/platform become optional.
The backend sends `watch_region`, `with_watch_providers` (pipe-joined, meaning any), and `with_watch_monetization_types=flatrate|free|ads`; the cache key includes the region and provider IDs.
`service=mine` with no services returns 400 `{"service": ["Choose your streaming services first."]}`.

## Storage (implemented in `src/streaming/`)

- `StreamingProvider(id = TMDB provider_id, name, logo_path, display_priority)`.
- `StreamingAvailability(title_type movie|tv, tmdb_id, region, link, fetched_at)`, unique per title and region.
- `StreamingOffer(availability, provider, monetization, display_priority)`, unique per availability, provider, and type.
- `UserStreamingService(user, provider)`, unique per user and provider.
- Snapshots are written only for active countries (ST-400).

## Operations

- **Deploy:** the only migration is `streaming.0001_initial`, which is additive. The Celery worker and beat must be running, as they already are in production (`supervisord.conf`).
- **Prime storage after the first deploy (optional):** `python manage.py backfill_streaming_availability --limit 2000` queues refresh batches for library, list, and diary titles in active countries. Add `--dry-run` to see the counts first. The worker then processes the batches through the shared TMDB rate limiter.
- **Periodic refresh:** the beat entry `queue_stale_streaming_availability` (task "Queue stale streaming availability") runs every 6 hours. It queues missing titles first, then those older than 24 hours, up to 1000 titles per run. It also deletes snapshots older than 30 days in countries that are no longer active.
- **On save:** `PUT /me/streaming/` queues "Check streaming library" for that user, covering up to 1000 titles, at most once a minute per user and country.
- **Settings:** `STREAMING_DEFAULT_REGION` (default `US`) is the country for users who have not picked one and whose client sends no `X-Spine-Region`.
- **Data terms:**
  - TMDB requires crediting JustWatch wherever this data appears (ST-003).
  - TMDB's API is free only for non-commercial use. If Spine starts earning revenue, all of its TMDB use, including this feature, needs TMDB's commercial license.
- **Known follow-ups:**
  - JustWatch lists service variants as separate providers (for example "Netflix Standard with Ads" and Amazon/Apple channels); the picker does not group them yet.
  - Per-service deep links would need a different source, such as the Streaming Availability API by Movie of the Night.
