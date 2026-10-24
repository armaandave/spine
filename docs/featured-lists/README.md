# Featured lists engine: build spec

This is the shared contract for everyone building the featured-lists revamp. `CATALOG.md` next to it lists every planned list with its source, matching path and cadence, from the research on 2026-10-21.

## Goal

Keep about 130 public featured lists, owned by the `Spine` user, current from many sources across every media type, entirely in code. Award winners appear within minutes of the announcement. Every sync is archived, so Spine builds chart history no API keeps. The iOS Search screen shows the lists as shelves.

## Ground rules

- Follow `AGENTS.md`. Model changes need migrations. API changes are additive: installed iOS builds must keep working.
- Tests never touch the network and need no keys. Mock `app.providers.services.api_request` or the provider functions, and keep fixtures in `src/lists/tests/featured/fixtures/<adapter>/`.
- Call provider functions through their module (`hardcover.lookup_book_by_isbn(...)`, never `from app.providers.hardcover import lookup_book_by_isbn`) so existing tests can keep patching them.
- Never touch production: no docker, nothing on port 8000, never read `.env.production`. Dev keys live in the main checkout's `.env`: `TMDB_API`, `MAL_API`, `IGDB_ID`, `IGDB_SECRET` (also Twitch client credentials), `HARDCOVER_API`, `NYT_BOOKS_API_KEY`, `MDBLIST_API` (Plus plan: 100k calls a day), `TRAKT_API`, `LISTENBRAINZ_TOKEN`, `GOOGLE_BOOKS_API_KEY`. Never print a key.
- Run commands with `uv run --locked`. Wrap test runs and servers with `$HOME/.local/bin/agent-cleanup run --owner featured-lists --label <purpose> --timeout <seconds> -- <command>`. Never start a Celery worker on Redis DB 0; use DB 5 with your own `REDIS_PREFIX`.
- Never read anything under `~/Documents`: it hangs. Scratch files go under `~/Library/Caches/spine-featured-build/<you>/`.
- Send `User-Agent: Spine/1.0 (+https://github.com/armaandave/spine)` to external sources (Wikimedia requires a descriptive one). Keep request rates polite.
- Run `scripts/check.sh` before calling your part done. Commit on your own branch with clear messages ending in `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push, and never merge into `myapp-main`.

## Package layout: `src/lists/featured/`

`src/lists/**` is exempt from ruff's `ALL` rules in `pyproject.toml`, so new code needs no lint config.

| Path | Owner | What |
|---|---|---|
| `types.py` | foundation | `Entry`, `FetchResult`, `AdapterError`, `GuardError` |
| `registry.py` | foundation | `FeaturedList`, `LiveWindow`, `Guards`; `all_definitions()`, `get(key)`, `by_identity(source, source_id)`; auto-discovers `definitions/*.py` |
| `definitions/<family>.py` | each family | each module exports `DEFINITIONS: list[FeaturedList]` |
| `definitions/static.py` | foundation | builds definitions from every `static/manifest-*.csv` |
| `adapters/<name>.py` | each family | each module exports `ADAPTER = Adapter(key=..., fetch=..., validate=...)`; `adapters/__init__.py` auto-discovers them into `ADAPTERS` |
| `resolve.py` | foundation | entries → `Item`s |
| `writer.py` | foundation | guarded atomic write, snapshot, health |
| `engine.py` | foundation | `run(definition, owner, trigger)` with a cache lock |
| `dispatch.py` | foundation | pure `due(...)` and the dispatcher task |
| `order.py` | curation | `SHELF_ORDER` and `NOW_PRIORITY`: the curated order of shelves, "Now" and first syncs |
| `health.py` | foundation | `report()` |
| `static/<key>.csv` | canon agents | one curated list each |
| `static/manifest-<family>.csv` | canon agents | one row per static list |
| `static/nominees/<ceremony>.csv` | live-awards agent | pre-matched nominees |
| `overrides/<family>.csv` | each family | manual match fixes: `scope,entry_key,ref,note` |

Auto-discovery means family agents add new files and rarely edit shared ones. If you must change a foundation file to fix a real bug, keep the change small and say so in your report.

## Core types

```python
@dataclass(frozen=True)
class Entry:
    rank: int | None
    title: str
    year: int | None = None
    creator: str = ""                # director, author, artist, developer
    media_type: str | None = None    # when the source knows it
    ids: Mapping[str, str] = {}      # tmdb, imdb, wikidata, steam, igdb, mal, anilist, mbid_rg, olid, hardcover, goodreads, ref
    isbns: tuple[str, ...] = ()
    image: str = ""                  # poster hint, so Items can be created without a provider call
    note: str = ""                   # "2027" (award histories), "Winner · Best Picture", "▲2"
    # .key: the first strong id ("imdb:tt0111161", "isbn:978…", "ref:tmdb:movie:550"),
    # else "t:<normalized title>|<year>"

class FetchResult(NamedTuple):
    entries: list[Entry]
    edition: str = ""                # period label: "Week of Oct 13", "2026", "Fall 2026"
    expected_count: int | None = None
    name: str | None = None          # the remote list's name, when the source has one
    live_label: str = ""             # "7 of 23 announced"
```

### Refs

A ref names a Spine item exactly: `<source>:<media_type>:<media_id>`, using the values of `app.models.Sources` and `MediaTypes`.

- Movies and TV: `tmdb:movie:550`, `tmdb:tv:1396`
- Games: `igdb:game:1942`
- Books: `hardcover:book:<id>`, `openlibrary:book:OL…M` (Spine stores Open Library *editions*)
- Anime and manga: `mal:anime:5114`, `mal:manga:2`, `mangaupdates:manga:<id>`
- Comics: `comicvine:comic:<id>`
- Music: `musicbrainz:music:<release-group-mbid>`

A ref of `-` in an override means "skip this entry".

### Static CSVs (`static/<key>.csv`)

UTF-8 with a header row: `rank,title,year,creator,ref,image,note`.

- `rank` may be blank for unranked lists.
- `ref` must be filled for every row of a curated list. Leave a row out rather than guess.
- `image` is optional: a poster URL saves a provider call when the Item is created.

### Static manifests (`static/manifest-<family>.csv`)

Header row: `key,name,media_types,kind,position,description,source_name,source_url,is_ranked`.

- `media_types` is `|`-separated, for example `movie` or `movie|tv`.
- `kind` is one of `chart`, `award`, `canon`, `community`, `curator`, `editorial`.
- `position` must fall inside the family's range below.

`definitions/static.py` turns each row into a `FeaturedList` with `adapter="static"`, `params={"file": "<key>.csv"}` and no cadence. A static list re-syncs only when its file changes, which the `definition_hash` tracks.

## Registry

```python
@dataclass(frozen=True)
class FeaturedList:
    key: str                          # stable forever, kebab-case, source-prefixed: "letterboxd-top-250-horror"
    name: str
    media_types: tuple[str, ...]      # drives shelves: 1 type → its shelf, 2 → both shelves, 3+ → "Across Media"
    kind: str                         # chart | award | canon | community | curator | editorial
    position: int                     # featured_position: order inside a shelf
    adapter: str
    params: Mapping[str, Any]
    cadence: timedelta | None         # None = static (re-sync when definition_hash changes)
    source_name: str = ""             # "Netflix", "Letterboxd", "The Academy"
    source_url: str = ""
    identity: tuple[str, str] = ("", "")   # (import_source, import_source_id); default ("spine", key)
    description: str = ""
    is_ranked: bool = True
    headline: bool = False            # included in the legacy flat endpoint (installed builds)
    featured: bool = True             # False = sync and stage without showing it
    live_windows: tuple[LiveWindow, ...] = ()
    guards: Guards = Guards()
    soft_time_limit: int = 600
```

**Position ranges:**

| Range | Section |
|---|---|
| 1–6 | the six lists live today (Letterboxd Top 500 and the five NYT charts), all `headline=True` |
| 100–199 | film |
| 200–299 | TV |
| 300–399 | books |
| 400–449 | comics |
| 450–499 | manga |
| 500–599 | games |
| 600–649 | anime |
| 700–799 | music |
| 800–899 | cross-media awards |
| 900+ | people |

The existing lists keep their identities: `("mdblist", "155837")` and `("nyt", <slug>)`.

## Resolver and writer

**Resolver**, in this order:
1. An override from `overrides/*.csv`.
2. An entry `ref`.
3. A direct provider ID: `tmdb`; `imdb` through `tmdb.find`; `steam` through `igdb.external_game`; `mal`, `igdb`, `hardcover` and `mbid_rg` directly.
4. ISBNs: Hardcover, then Open Library, in today's `_resolve_book` order.
5. A strict title search per media type: exact normalized title, year ±1, creator when available. No fuzzy guessing.

After the direct IDs that need no network, and before any lookup (`imdb`, `steam`, ISBNs, title search) or the cache, the engine's resolver reuses the list's own past matches: the Item its latest snapshot holds for the entry's key, when that Item still exists and has one of the entry's media types (one snapshot read per run). Overrides and refs still win. Unlike the cache, these survive a deploy.

Results are cached in Redis under `featured:resolve:<entry.key>`: hits for 45 days (longer than the longest cadence, 33 days; each hit refreshes the TTL), misses for 1 day. Title searches are capped per run (default 50); entries past the cap are deferred for an hour, not unresolved, so the next run searches the next batch. Unresolved entries go to the health report.

**Writer guards**, in order:
1. `require_exact_count`
2. zero resolved
3. resolve rate below `min_resolve_rate`
4. a drop greater than `max_drop` against the previous count, checked only when that count is at least `drop_check_min_previous`

Entries deferred by a provider budget (below) don't count toward the resolve rate, so a list is never `guarded` just because a budget ran out.

Any failure raises `GuardError`, records the status, and leaves the list untouched. Otherwise one transaction does all of this:
- get or create Items inside `app.mixins.collect_external_rating_item_ids()`, and on commit hand the new IDs to `ratings.add_pending(ids)` (never one ratings task per Item; see "External ratings" below);
- get or create the `CustomList` by `(owner, *identity)`;
- set its fields (public, ranked, `is_featured`, `featured_position`, source URL, `last_synced_at`);
- replace the items only when the content fingerprint changed, and set `content_changed_at` when it does;
- upsert today's snapshot.

The `list-media-types` branch is adding `CustomList.locked_media_types`. Once it lands, the writer also sets that lock from `media_types`; that's handled at integration. Don't add a media-type column in this feature.

## Provider budgets

Featured syncs share production's provider keys with the app, so every run (and every dry run) happens inside `app.providers.quota.scope("featured", …)`. Inside it, each `services.api_request` to Hardcover (`api.hardcover.app`) or Open Library (`openlibrary.org`) counts against a per-UTC-day Redis counter: adapter fetches, ISBN lookups, title searches, the writer's metadata fetches for new Items, and the ratings batches for those Items alike. Cached provider answers cost nothing.

| Setting | Default | Why |
|---|---|---|
| `FEATURED_HARDCOVER_DAILY_BUDGET` | 1000 | Hardcover has a daily quota on top of ~60 a minute, and the app's search and book pages need the same key. Steady state needs far less (resolutions are cached for 30 days); a first sync of every book list takes a few days. |
| `FEATURED_OPENLIBRARY_DAILY_BUDGET` | 2000 | Open Library allows 20 a minute per IP, shared with the app. |

- **Budget spent:** the request raises `quota.Deferred` without touching the network until midnight UTC. `0` stops featured calls to that provider entirely.
- **429 with a long wait:** a 429 asking for more than the 60 s `api_request` would sleep (Hardcover's "Try again in 69163 seconds", in `Retry-After` or the body) blocks the provider for that long. Featured requests then defer at once instead of sleeping; a long 429 the app itself receives blocks featured syncs too. Shorter 429s still wait and retry as before.
- **Deferred entries** stay unresolved, aren't cached, and don't count toward the guards' resolve rate. A run with deferred entries writes the list only when it clears `min_resolve_rate` with the deferred entries counted as unmatched, and wouldn't shrink: a new list appears once it clears that bar (never with 1 of its 15 books), a growing one keeps its progress, and a current list is otherwise left as it is. Either way the status is `deferred`, `deferred_until` holds the reset time, and the dispatcher retries the list then. An adapter whose own fetch is deferred records the same.
- The health report shows each list's `deferred_until`, a `deferred` total, and `quota` (budget, used today, blocked until) per provider.

## External ratings for new Items

`ratings.py` trickles new Items' ratings onto the default worker instead of queueing them all at once (a first deploy creates thousands of Items):
- The writer adds new Item IDs to a Redis set (`services.redis_db`).
- Each dispatcher tick queues one `Enrich featured list ratings` batch of at most 50 on the default queue, only when the previous batch has finished (or 30 minutes passed), so app tasks never wait behind a featured backlog.
- The batch runs `enrich_external_ratings_batch` inside the featured quota scope, so its Hardcover and Open Library metadata calls count against the featured budgets. Book Items whose provider has no featured budget left today, or is blocked by a long 429, stay pending until it resets.
- The health report shows `ratings_pending`. If Redis loses the set, list detail still enriches Items when someone opens them.
- Known gap: a transient failure inside a batch retries that Item through the app's own `enrich_external_ratings` task, outside the featured scope, as it would for any Item.

## Models (one migration in `lists`)

- **`FeaturedListSync`**:
  - Identity: `key` (unique slug); `custom_list` (nullable one-to-one, `SET_NULL`, related name `featured_sync`); `definition_hash`; `fingerprint`.
  - Content: `edition`; `content_changed_at`; `live_label`; `item_notes` (charts: `{"<item_id>": "▲2"|"▼1"|"NEW"}` against the previous content, rewritten whenever the items are replaced); `entry_notes` (every list: what each item or person shows, from the entries' `note` and the chart movement, `{"<item_id>" | "<source>:person:<id>": "Winner · Best Picture" | "▲2 · $12.4M"}`, rewritten on every successful run; the API reads it for people lists only, and media lists keep it for older code).
  - Run state: `last_attempt_at`; `last_success_at`; `last_status` (`ok|unchanged|guarded|failed|empty|deferred`); `last_error` (sanitized, never containing a key); `consecutive_failures`; `lease_until`; `deferred_until` (when a deferred run is retried).
  - Counts: `entry_count`; `resolved_count`; `unresolved` (JSON, at most 50 rows of `{rank, key, title, year, creator}`).
- **`FeaturedListSnapshot`**: `sync` (foreign key); `taken_on` (date); `edition`; `fingerprint`; `entries` (JSON `[[rank, entry_key, item_id|null], …]`). Unique on `(sync, taken_on)`. Written only when the fingerprint changes.

- **`CustomListItem`** (migration `lists/0011_customlistitem_occurrence_note`): `occurrence` (which repeat of the item the row is; unique with the list and the item, so people's own lists still hold an item once at occurrence 0) and `note` (the row's note, rewritten on every successful run). Both have database defaults, so code from before the migration can still insert rows.

No new `CustomList` columns.

### Award histories: a row per win

All-time award lists (`kind="award"`, no live windows, media lists: `FeaturedList.keeps_repeats`) give each win its own row, even when the poster repeats: Succession's Emmys are three rows noted "2023", "2022" and "2020", each with its own position, and the item's repeats get occurrences 0, 1, 2. Their notes are the year alone, plus " · tie" (Wikidata) or " · shared" (Nobel) where that applies. On these lists two entries are the same row only when both the item and the note match. Every other list holds an item once: a live ceremony joins one film's categories into one row ("Winner · Best Picture; Winner · Best Director"), and the per-year Steam Awards lists join a game's categories.

- **Counts:** `items_count` (and `entries_count`) counts rows, so the Emmy list's size includes each Succession win. Completion (`completion` on list detail and items, and stats' list progress) counts distinct items: watching Succession once completes all three rows.

## Scheduling

- **Worker:** a second Celery worker consumes the `featured` queue: a new `[program:celery-featured]` in `supervisord.conf`, with the same flags as `celery` plus `-Q featured -n featured@%h --concurrency 2`, `stopasgroup=true` and `stopwaitsecs=60`. The default worker keeps the `celery` queue, so long imports never block an award night.
- **Routing and beat:** `CELERY_TASK_ROUTES` sends `Dispatch featured lists` and `Sync featured list` to `featured`. The beat entry `dispatch_featured_lists` runs every 60 s with `{"queue": "featured", "expire_seconds": 55}` (`django_celery_beat`'s `DatabaseScheduler` ignores `expires`).
- **What the dispatcher queues:** due syncs. A definition is due when:
  - it has never succeeded;
  - its `definition_hash` changed;
  - its `deferred_until` passed;
  - `cadence` has elapsed (with exponential backoff after failures);
  - or it's inside a live window and its interval has elapsed.
- **Retired lists:** each tick first unfeatures retired lists (`dispatch.retire_orphans`, see the legacy endpoint below).
- **Priority:** live windows come first. Non-live work is capped at one sync queued or running at a time (`MAX_NON_LIVE`), stalest first (never-synced lists before any other). Equally stale lists, such as every list that has never synced after a deploy, follow the curated shelf order (`order.py`: each shelf's first list, then each shelf's second, and so on), then `position`, so flagship lists fill first. The cap keeps one of the worker's two slots free for live polls and the dispatcher. A live window polled more often than the dispatcher runs gets one poll per interval each tick (`countdown` 0 and 30 s for a 30 s interval). Leases stop double runs. A poll that finds a newer run already started skips itself and releases only the lease the dispatcher took for it (`lease_until` in its kwargs), never a running sync's.
- **Killed runs back off:** each attempt counts as a failure from the moment it starts (`consecutive_failures + 1`); success resets it and a deferral takes it back. A run killed outright (SIGKILL, `time_limit`, OOM, a deploy restart) therefore still backs off. While a run is in progress, the health report shows it with one failure more than it had and `leased: true`.
- **TaskResult noise:** skip the dispatcher in `app/signals.py:create_task_result_on_publish`, and set `ignore_result=True` on it.
- **Legacy entries:** production's beat already runs `Sync NYT featured lists`, so that entry and task name stay. It now skips charts the engine synced successfully in the last 12 hours (`sync_nyt_featured_lists --skip-synced-within 12`). `django_celery_beat` never deletes rows, so never remove that task name. The `Sync Letterboxd Top 500 featured list` entry never shipped and is gone: the dispatcher syncs that list. The commands stay thin wrappers over the engine and keep their CLI, messages and tests.

## API

All of it is additive.

**`GET /api/v1/lists/featured/` (legacy):**
- Same shape as today.
- By default `results` holds only `headline=True` lists plus any manually featured list that isn't in the registry, so installed builds keep seeing today's six. `?view=all` returns every featured list.
- "Manually featured" means no `FeaturedListSync` row. A list the engine synced whose definition is gone (no definition has its sync key or its identity, such as a past ceremony) is *retired*: every endpoint hides it, and the dispatcher sets `is_featured=False` on it each tick. It's never deleted.
- Each result gains an optional `featured` object, in the card shape below without `preview_items` (`null` when the list has no card). Manually featured lists get one too, with `kind: "editorial"`, `cadence: "static"` and media types taken from their items.

**`GET /api/v1/lists/{id}/`** gains the same optional `featured` object when the list is featured and public; other lists' responses are unchanged (no key).

**Entry notes:** on lists the engine writes, every item in `GET /api/v1/lists/{id}/items/` and the detail's `items`, and every person in `GET /api/v1/lists/{id}/people/` and the detail's `people`, gains `note`: a string or `null`. It's the row's own note ("2023", "Nominee · Fiction", "Joint 21", "Inducted 2019"), with a chart's movement in front ("▲2 · $12.4M"); a source note that already gives the movement ("New this week") stands alone, and an item listed by several entries joins their notes with "; " (except on award histories, where each win is its own row). Media rows read it from `CustomListItem.note`, people from `entry_notes`. Other lists' items have no `note` key.

**`GET /api/v1/lists/featured/shelves/`:**

```json
{
  "generated_at": "2026-10-21T18:02:00Z",
  "shelves": [
    {"key": "now", "title": "Now", "media_type": null, "count": 7, "live_count": 1,
     "kind_counts": {}, "lists": ["…≤10 cards"], "see_all": null},
    {"key": "movie", "title": "Movies", "media_type": "movie", "count": 42, "live_count": 0,
     "kind_counts": {"chart": 6, "award": 9, "canon": 14, "community": 10, "curator": 3},
     "lists": ["…≤12 cards"], "see_all": "/api/v1/lists/featured/shelves/?media_type=movie"},
    {"key": "mixed", "title": "Across Media", "media_type": "mixed", "…": "…"}
  ]
}
```

- **Shelf order:** `now`; then each exposed media type that has lists, in the order movie, tv, anime, manga, game, book, comic, music; then `mixed`.
- **Hidden media types:** lists whose media types are all hidden by `app.exposure` are omitted (music when it's off, for example).
- **Lists below their bar:** a list is omitted, here and on the legacy endpoint, while both its sync's `resolved_count / entry_count` and its stored rows / `entry_count` are below its own `min_resolve_rate`. List detail still serves it.
- **"Now":** lists inside or within 24 h of a live window come first (inside before near, then by `position`). Then the charts in `order.NOW_PRIORITY` whose content changed in the last 7 days, in that order, at most 2 per media type (a chart with two media types counts against both; live lists don't count). At most 10 cards in all. A chart not in `NOW_PRIORITY` never appears in "Now".
- **Within a shelf:** live lists (inside a window) first; then the keys in `order.SHELF_ORDER[<media type>]`, in that order; then every other list by `position`, then name. A list with two media types follows each shelf's own order. "Across Media" has no curated order.

### How to change the order

All curation lives in `src/lists/featured/order.py`; the weekly review edits only that file.

- **Reorder a shelf:** move keys in `SHELF_ORDER["<media type>"]`. The first 12 are what the landing shelf shows. A key not listed falls after the curated ones, by `position`.
- **Change "Now":** reorder or add chart keys in `NOW_PRIORITY`. Only `kind="chart"` lists are allowed, and only the first two recently changed charts per media type show.
- **New list:** add its key where it belongs. A live ceremony list (`booker-prize-2026`) goes right after its award's all-time list; its live state floats it to the top during its window.
- **Planned list not in the registry yet:** add its key to `ALLOWED_MISSING`, or leave it out until it lands.
- **Check:** `uv run --locked python manage.py test lists.tests.featured.test_registry` fails on any unknown key, a key on a shelf its media types don't include, a duplicate, or a non-chart in `NOW_PRIORITY`. The order also decides which never-synced lists the dispatcher syncs first.

**`GET /api/v1/lists/featured/shelves/?media_type=movie`** returns `{"generated_at", "media_type": "movie", "shelves": [...]}`. There's one shelf per kind with lists, in the order chart, award, canon, community, curator, editorial, with titles "Charts", "Awards", "Critics & Canon", "Community", "Curators' Picks" and "Editorial". Each shelf holds all of its cards, ordered by the same rule as the landing shelf (live, curated, then position and name). `?media_type=mixed` does the same for "Across Media" (lists with 3 or more media types). An unknown value is a 400; a media type hidden by `app.exposure` is a 404. Empty shelves are left out, including "Now".

**Card shape:**

```json
{"id": 12, "name": "Netflix Top 10: Films", "slug": "netflix-top-10-films", "description": "…",
 "is_ranked": true, "list_type": "media", "items_count": 10, "media_types": ["movie"], "kind": "chart",
 "source": {"name": "Netflix", "url": "https://www.netflix.com/tudum/top10"},
 "cadence": "weekly", "period_label": "Week of Oct 13",
 "content_changed_at": "…", "last_synced_at": "…",
 "live": null,
 "preview_items": ["…≤5, the same MediaSummary objects list_payload already returns in preview_items, using w342 TMDB posters, each with an optional \"note\""],
 "preview_people": ["people lists only: …≤5, the same person objects list_payload returns in preview_people"]}
```

- `list_type` is `media` or `people` (added later: older payloads lack it, so decode it as optional and fall back to `media`). It's also in the `featured` block on the legacy endpoint and list detail.
- `cadence` is one of `live`, `daily`, `weekly`, `monthly`, `yearly`, `static`.
- `live` is either `null` or `{"status": "upcoming"|"live"|"done", "label": "7 of 23 announced", "starts_at": "…", "ends_at": "…"}`.
- `source.name` and `source.url` may be `null`. `period_label` is the sync's edition, or `null`.
- Each preview item's `note` is chart movement against the list's previous content: `"▲2"`, `"▼1"` or `"NEW"`; `null` when its position didn't change, on a list's first sync, and for every kind other than `chart`. The writer stores it as `FeaturedListSync.item_notes` (`{"<item_id>": note}`) whenever it replaces a chart's items, so requests never scan snapshots.
- People lists (`list_type: "people"`) count people in `items_count`; their `preview_items` is empty and `preview_people` holds their first five people, in list order. Media-list cards have no `preview_people` key, and neither does the `featured` block on the legacy endpoint and list detail.

**`GET /api/v1/lists/featured/health/`:** allowed for staff, or with the header `X-Spine-Report-Token` matching `settings.FEATURED_REPORT_TOKEN`, compared with `hmac.compare_digest`. The setting defaults to `""`, which disables the token path. Everyone else gets a 404. `manage.py featured_lists_report [--json]` prints the same report.

**Performance:** the query count must not grow with the number of lists. Use annotations, one `RowNumber()` window query for previews (`api/serializers/common.py` already imports it), and an `assertNumQueries` ceiling in tests.

## Live award nights

- **Pre-matching:** nominees are matched when nominations come out and stored in `static/nominees/<ceremony>.csv`, with columns `category,rank,title,year,creator,ref,note`.
- **Polling:** the `award_page` adapter polls one batched MediaWiki revisions request for the watched pages, every 20–30 s inside a window. It parses the ceremony page's winner markers (bold first entry, ‡, infobox).
- **Publishing:** a winner is published only when all of these hold:
  - it's a pre-matched nominee;
  - one per category, except ties the source marks;
  - the marker is stable across revisions for at least 60 s with no revert.

  Anything else goes to `unresolved` for review.
- **Nobel:** uses the official API at `api.nobelprize.org/2.1`.
- **Windows:** live windows in the registry are stored in UTC, built from each award's local time zone.
- **How-to:** `LIVE_AWARDS.md` explains adding a ceremony's nominees and window, and reviewing a night.

## iOS: shelves on Search

- **Landing:** the "Now" strip of chart cards, then one sideways row of compact tiles per media type, then "Across Media". A tile is about 148×162 pt: a stack of five posters, a rank chip, a status badge, the name and a count.
- **See all:** opens the media type grouped by kind.
- **Peek:** a long-press shows a context-menu preview.
- **List detail:** gains a metadata line, a live banner and a source link.
- **Edit and Delete:** Edit is shown to the list's owner and collaborators, Delete only to its owner, matching what the server allows. Before this feature every user saw both on Spine's lists.
- **Legacy fallback:** if `shelves/` returns 404, fall back to the legacy endpoint.
- **Decoding:** tolerant: `decodeIfPresent`, with unknown-value fallbacks for every enum.

## Integration notes (for the orchestrator)

- `list-media-types` is in progress in another session. It adds migration `0009_customlist_locked_media_types` and lock checks in both sync commands. If it lands first, renumber this feature's migration after it and have the writer set the lock.
- `lists/0010_featured_deferral_and_entry_notes` (provider deferrals and per-entry notes) follows `0009_featured_list_sync`; renumber both together if another `lists` migration lands first.
- `lists/0011_customlistitem_occurrence_note` adds `CustomListItem.occurrence` and `note`, widens the unique constraint to `(item, custom_list, occurrence)`, and copies each featured list's `entry_notes` onto its existing rows. Award histories re-sync once after it (their definition hash includes `keeps_repeats`).
- Production gets the new worker and migration on the first deploy of this branch. Disk was at 99% on 2026-10-21, so watch the image build.
