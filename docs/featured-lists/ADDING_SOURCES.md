# Adding featured-list sources

How to add a source family to `src/lists/featured/`. The contract is `README.md`; this is the how-to. You add files; you shouldn't need to edit shared ones.

## What you add

| File | What |
|---|---|
| `src/lists/featured/adapters/<name>.py` | exports `ADAPTER` (discovered automatically) |
| `src/lists/featured/definitions/<family>.py` | exports `DEFINITIONS: list[FeaturedList]` (discovered automatically) |
| `src/lists/featured/overrides/<family>.csv` | optional manual match fixes |
| `src/lists/featured/static/<key>.csv` + `static/manifest-<family>.csv` | curated lists (no adapter code needed) |
| `src/lists/tests/featured/fixtures/<name>/…` | recorded responses, trimmed |
| `src/lists/tests/featured/test_<name>.py` | adapter tests, no network |

Module names starting with `_` are skipped by discovery.

## An adapter

```python
# src/lists/featured/adapters/steam.py
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://api.steampowered.com/IStoreTopSellersService/GetWeeklyTopSellers/v1/"


def validate(definition):
    """No network. Raise ValueError when params are wrong."""
    country = definition.params.get("country", "US")
    if len(country) != 2 or not country.isupper():
        raise ValueError("country must be a two-letter upper-case code")


def fetch(definition):
    data = get_json("steam", URL, params={"country_code": definition.params.get("country", "US")})
    ranks = (data.get("response") or {}).get("ranks")
    if not ranks:
        raise AdapterError("Steam returned no weekly top sellers.")
    return FetchResult(
        entries=[
            Entry(rank=row["rank"], title=row.get("name", ""), media_type="game", ids={"steam": row["appid"]})
            for row in ranks
        ],
        edition=f"Week of {data['response']['start_date']}",
    )


ADAPTER = Adapter(key="steam", fetch=fetch, validate=validate)
```

Rules:

- `fetch(definition) -> FetchResult`. `definition.params` is your config; `definition.media_types` says what the list may hold.
- Fetch through `lists.featured.helpers`: `get_json(provider, url, params=, headers=)`, `get_text(...)` (HTML), `post_json(provider, url, json=|data=, headers=)`. They send `User-Agent: Spine/1.0 (+https://github.com/armaandave/spine)`, use the shared rate-limited session, and raise `AdapterError` with a key-free message. Tests mock them all by patching `app.providers.services.api_request`.
- To call Spine's providers, go through the module (`from app.providers import tmdb` then `tmdb.find(...)`), never `from app.providers.tmdb import find`, so patches keep working.
- On a source failure raise `AdapterError("…")`. Never put a key, token or keyed URL in a message (`helpers.sanitize()` strips configured keys if you must include provider text).
- Return entries in source order. Ranked lists are sorted by `rank`; entries with the same `key` are dropped after the first. All-time award lists are the exception: return one entry per win, noted with its year alone ("2026", "2025 · tie"), and don't merge a work's wins; there only an entry with the same `key` *and* `note` is dropped (README, "Award histories").
- `version(definition) -> str` is optional: a hash of local inputs (the static adapter hashes its CSV) so editing them re-syncs the list.

## Entries

`Entry(rank, title, year=None, creator="", media_type=None, ids={}, isbns=(), image="", note="")`

- `rank`: source rank (int), or `None` for unranked lists.
- `media_type`: set it per entry whenever the list holds more than one type (`movie` and `tv` charts, cross-media awards). An entry whose type isn't in `definition.media_types` stays unresolved.
- `ids` keys (values are stringified; blanks dropped):

| Key | Resolves to |
|---|---|
| `ref` | exactly `<source>:<media_type>:<media_id>` |
| `tmdb` | `tmdb:movie|tv:<id>` (needs `media_type`, or a list with only one of movie/tv) |
| `imdb` | TMDB through `tmdb.find` (network, cached) |
| `igdb` | `igdb:game:<id>` |
| `steam` | IGDB through `igdb.external_game` (network, cached) |
| `mal` | `mal:anime|manga:<id>` (same media-type rule as `tmdb`) |
| `mangaupdates` | `mangaupdates:manga:<id>` |
| `hardcover` | `hardcover:book:<id>` |
| `olid` | `openlibrary:book:<OL…M>` (editions only; turn works into editions in your adapter) |
| `mbid_rg` | `musicbrainz:music:<release-group mbid>` |
| `comicvine` | `comicvine:comic:<id>` |
| `wikidata`, `goodreads`, `anilist` | identity only (used in the key; not resolvable) |
| `review` | never resolves: the entry goes to `unresolved` with this text as its reason, unless an override matches its key (award winners that aren't pre-matched nominees) |
| `<source>_person` | people lists: `tmdb_person`, `openlibrary_person`, `hardcover_person`, `musicbrainz_person`, `mal_person`, `mangaupdates_person` |

- `isbns`: every ISBN you have, best first. Books try Hardcover, then Open Library, per ISBN.
- `image`: a poster URL lets the writer create a new Item without a provider call. Without one (or without a title) the writer fetches the provider's metadata once; if that fails, the entry is unresolved instead of getting placeholder art.
- `note`: free text shown later ("Winner · Best Picture", "▲2", "Inducted 2019"); on all-time award lists, the year alone ("2027").
- `entry.key` is the entry's stable identity: the first id in the order `ref, tmdb, imdb, igdb, steam, mal, mangaupdates, anilist, mbid_rg, hardcover, olid, comicvine, wikidata, goodreads`, then a `*_person` id, then `isbn:<first>`, else `t:<normalized title>|<year>`. `tmdb` and `mal` keys include the media type (`tmdb:movie:550`). Overrides and the health report use it.

Anything without a direct id goes to a strict title search: exact normalized title (or the result's main title before a subtitle), year within ±1, creator when both sides have one, and a unique match when there's no year or creator. When nothing matches that way, the entry's own main title is tried ("Master of the Senate: The Years of Lyndon Johnson" finds "Master of the Senate"), but only when the year or creator agrees and exactly one result qualifies, so adapters no longer need to strip subtitles themselves. No fuzzy matching. Searches are capped per run by `Guards.max_title_searches` (default 50); entries past the cap are deferred and searched on a later run. Results are cached under `featured:resolve:…` (hits 45 days, refreshed on every hit; misses 1 day), so a re-run is cheap.

## A definitions module

```python
# src/lists/featured/definitions/games.py
from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards, live_window

DEFINITIONS = [
    FeaturedList(
        key="steam-weekly-top-sellers",      # forever; kebab-case; source first
        name="Steam Weekly Top Sellers",
        media_types=("game",),
        kind="chart",                       # chart award canon community curator editorial
        position=510,                       # inside your family's range (README)
        adapter="steam",
        params={"country": "US"},
        cadence=timedelta(days=7),          # None = static: re-sync only when the definition changes
        source_name="Steam",
        source_url="https://store.steampowered.com/charts/topselling/global",
        description="The week's best-selling games on Steam.",
        guards=Guards(min_resolve_rate=0.8),
    ),
]
```

- `identity` defaults to `("spine", key)`; set it only to adopt an existing list.
- `headline=True` is only for the six original lists (positions 1–6).
- `featured=False` syncs and stages a list without showing it.
- `list_type="people"` writes people (`PersonListItem`) instead of Items; use positions 900+. Name search for people needs `params["person_source"]` (`openlibrary`, `tmdb`, …).
- `live_windows=(live_window("2027-03-14 16:00", "America/Los_Angeles", timedelta(hours=4), interval=timedelta(seconds=30), label="The Oscars"),)`: polled every `interval` from 20 minutes before the start; cards show `upcoming`/`live`/`done` from 24 h before to 24 h after.
- `Guards`: `require_exact_count` (entries must equal `FetchResult.expected_count`), `min_resolve_rate` (0.5), `max_drop` (0.5) once the previous count is at least `drop_check_min_previous` (20), `max_title_searches` (50). A failed guard leaves the list untouched and records `guarded` in the health report.
- The drop guard only judges lists that held at least 20 entries, so fixed-size charts also set `params["min_entries"]` (10 for a top 10) and call `adapters/_rows.require()`: a source that returns fewer rows raises `AdapterError` and the list stays as it was.
- `tags` sets `CustomList.tags`; `soft_time_limit` (600 s) bounds one run.

## Static lists

No code: add `static/<key>.csv` (`rank,title,year,creator,ref,image,note`, every row with a `ref`) and a row in `static/manifest-<family>.csv` (`key,name,media_types,kind,position,description,source_name,source_url,is_ranked`; `media_types` like `movie|tv`; positions 900+ become people lists with `<source>:person:<id>` refs). Editing the CSV re-syncs the list.

## Overrides

`overrides/<family>.csv`, columns `scope,entry_key,ref,note`:

```
scope,entry_key,ref,note
letterboxd-top-500,t:the godfather part ii|1974,tmdb:movie:240,MDBList sent no TMDB id
nyt,isbn:9780593798430,hardcover:book:1234567,Hardcover has the boxed set first
*,imdb:tt0000001,-,never list this
```

`scope` is a list key, an adapter key, or `*` (the most specific wins). `entry_key` is exactly what the dry run or health report prints. `ref` is a Spine ref, or `-` to drop the entry (dropped entries don't count against the resolve rate). Changing an override re-syncs the lists it scopes.

## Running one list locally

From your worktree, with the dev keys the settings read from the main checkout's `.env`:

```sh
cd src
uv run --locked python manage.py migrate
uv run --locked python manage.py shell -c "from django.contrib.auth import get_user_model; get_user_model().objects.get_or_create(username='Spine')"
uv run --locked python manage.py sync_featured_list steam-weekly-top-sellers --dry-run   # fetch + resolve, writes nothing
uv run --locked python manage.py sync_featured_list steam-weekly-top-sellers            # writes the list
uv run --locked python manage.py featured_lists_report                                   # or --json
```

The dry run prints every entry as `#rank title (year) / creator -> ref [how]  {entry_key}`, then `Resolved n/m (…%); k rows; u unresolved; s title searches.`, the unresolved rows, and what the guards would say. `--show N` prints only the first N entries. Wrap long runs in `agent-cleanup` as `README.md` describes.

## Tests

No network and no keys. Reuse `src/lists/tests/featured/helpers.py`:

- `load_fixture("<adapter>", "<file>")`: parsed JSON for `.json`, text otherwise.
- `api_routes({"url fragment": payload | exception | callable(url, params)})`: patches `services.api_request`; an unexpected URL fails the test.
- `make_definition(**fields)`: a valid `FeaturedList` (adapter `"test"`, position 150, movie).
- `fake_adapter(result)`: a temporary `"test"` adapter returning a `FetchResult`, a list of entries, or `callable(definition)`.
- `FeaturedTestCase`: clears the cache, creates the `Spine` owner, `run_list(definition, entries)` runs the engine, `assertListRefs(custom_list, refs)`.
- `registry.override_definitions([...])` swaps the registry inside a block.

```python
from lists.featured.adapters import ADAPTERS
from lists.featured.definitions.games import DEFINITIONS
from lists.tests.featured.helpers import FeaturedTestCase, api_routes, load_fixture


class SteamAdapterTests(FeaturedTestCase):
    def test_weekly_top_sellers(self):
        definition = DEFINITIONS[0]
        with api_routes({"GetWeeklyTopSellers": load_fixture("steam", "weekly.json")}):
            result = ADAPTERS["steam"].fetch(definition)
        self.assertEqual(result.entries[0].ids, {"steam": "730"})
```

`src/lists/tests/featured/test_registry.py` already checks every definition (keys, identities, media types, kinds, position ranges, your `validate()`, static files, overrides), so a bad definition fails CI. Finish with `scripts/check.sh`.

## Checklist

1. Adapter with `validate()`; every failure is an `AdapterError`.
2. Definitions in your position range, each with `source_name` and `source_url`.
3. Fixtures trimmed to a few entries, no keys in them.
4. Tests for parsing, ids, editions and failures.
5. `sync_featured_list <key> --dry-run` against the live source: note the resolve rate; add overrides or ids for misses.
6. `scripts/check.sh` green.
