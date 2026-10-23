# Spine featured lists: catalog and plan

Research by eight agents on 2026-10-21. "Verified" means an agent fetched it; everything else is reported (docs, news, third parties).

## Launch catalog

### Movies (~29)

| List | Source and adapter | Matching | Cadence |
|---|---|---|---|
| Letterboxd Top 500 (live today) | MDBList external list 155837 | TMDB IDs from MDBList | daily (MDBList paid tier refreshes daily; free weekly) |
| Letterboxd Top 250 Documentary, Sci-Fi, Horror, Animated, Rom-Com; Top 100 Anime Films | letterboxd.com/official/ lists, mirrored as MDBList external lists (needs paid slots) | TMDB via MDBList | daily |
| IMDb Top 250 | MDBList public list 101881 (snoak), read through `/lists/{id}/items`, no slot needed (verified ranks match) | TMDB, IMDb | daily |
| IMDb MovieMeter | MDBList official list `moviemeter` (free) | TMDB, IMDb | daily |
| JustWatch streaming charts | MDBList official list `justwatch-streaming-charts` (free) | TMDB, IMDb | daily |
| Oscars Best Picture (all-time) | Wikidata `P166 = Q102427`: 99 works, TMDB on 98 (verified) | TMDB direct | yearly + live night |
| Oscars, this year's ceremony (all categories) | nominees matched in January; live from Wikipedia's ceremony page (+3 min measured) | pre-matched nominees | live window |
| Palme d'Or | Wikidata `Q179808`: 86 works, TMDB 98% (ties are real) | TMDB direct | yearly |
| Golden Globes (Best Drama, Musical/Comedy) | official WordPress REST API `goldenglobes.com/wp-json/wp/v2/nomination?award-categories=<id>` with `acf.is_winner` (verified, has 2026) | title + year → TMDB | yearly + live (Wikipedia +4 min) |
| BAFTA Best Film | Wikipedia / Wikidata | TMDB | yearly |
| Criterion Collection by spine number | Wikidata P12279: 1,264 films, 1,237 with TMDB (verified) | TMDB direct | monthly (Wikidata lags) |
| National Film Registry | Wikidata `P361 Q823422`: 870 films, 851 TMDB (verified; official total ~925, top up from LoC) | TMDB direct | yearly, December |
| Sight & Sound 2022 critics' 250 and directors' 100 | static import from BFI pages | title + year + director → TMDB | static (next 2032) |
| AFI 100 Years… 100 Movies (+ other AFI lists) | static import | TMDB | static |
| 1001 Movies You Must See Before You Die | static import (Letterboxd copy via a temporary MDBList slot) | TMDB via MDBList | static (pick an edition) |
| TSPDT 1,000 Greatest Films | theyshootpictures.com/gf1000.htm HTML (verified) | title matching | yearly, ~Jan 1 |
| NYT 100 Best Movies of the 21st Century | static import | TMDB | static |
| Edgar Wright's 1000, Scorsese's and Tarantino's favourites | static import via Letterboxd lists | TMDB | static |
| Netflix Top 10 Films (weekly) | `netflix.com/tudum/top10/data/all-weeks-global.tsv`, no auth, history from 2021-07-04 (verified) | title (+ season) → TMDB: 85% exact, ~92% with fuzzy + year (verified sample) | weekly, Tuesdays |
| US weekend box office | Trakt API `/movies/boxoffice` (free app client ID) | IMDb/TMDB IDs included | weekly, Monday |
| TMDB trending this week | TMDB API (key exists) | TMDB direct | daily |

### TV (~12)

| List | Source and adapter | Matching | Cadence |
|---|---|---|---|
| IMDb Top 250 TV | MDBList public list 94576 (treeman183), no slot | TMDB, IMDb | daily |
| Letterboxd Top 250 Miniseries | Letterboxd official, MDBList external | TMDB | daily |
| Emmys Outstanding Drama, Comedy, Limited (all-time) | Television Academy pages (server-rendered); Wikidata is poor for TV | title → TMDB TV | yearly + live (Wikipedia +2 min) |
| Netflix Top 10 TV | Netflix TSV | title + season → TMDB | weekly |
| Nielsen Streaming Top 10 | nielsen.com/data-center/top-ten/ HTML, ~2.5 weeks behind, no archive | title → TMDB | weekly |
| Trakt trending shows | Trakt API | IDs included | daily |
| BBC Culture 100 greatest 21st-century TV, Rolling Stone 100, WGA 101 | static imports | TMDB | static |

### Books (~45)

| List | Source and adapter | Matching | Cadence |
|---|---|---|---|
| NYT Best Sellers, all 19 active lists (5 live today) | `lists/overview.json` (one call) or `lists/current/{slug}.json`; business and graphic/manga are now monthly | ISBN → Hardcover → Open Library; new releases need a title + author fallback (ISBN hit rate 12/15 Hardcover on this week's fiction) | weekly / monthly, Wednesdays |
| Pulitzer: Fiction, General Nonfiction, History, Biography, Memoir, Poetry | Wikidata for history (Open Library work IDs on 71/71 fiction works, but 2024–26 only on authors); Wikipedia for recent years | Open Library work → edition, or Hardcover title + author | yearly, early May |
| Booker, International Booker | Wikidata (Open Library 100%) + Wikipedia | same | yearly (Nov; May) |
| National Book Awards (5 categories) | nationalbook.org year page (server-rendered, fetchable) | title + author | yearly, mid-November |
| Nobel Prize in Literature (people list) | official API `api.nobelprize.org/2.1/nobelPrizes` (verified) | Wikidata / Open Library author | yearly, October |
| Hugo Best Novel | thehugoawards.org WordPress REST + Wikidata (Open Library 97%) | Open Library | yearly, Aug/Sep |
| Women's Prize, Newbery, Caldecott, Edgar, NBCC | Wikidata / Wikipedia | title + author | yearly |
| Oprah's Book Club, Reese's Book Club, Read with Jenna | Wikipedia list (Oprah's site 403s); reesesbookclub.com; today.com | title + author | monthly |
| Apple Books Top Paid, Top Audiobooks | `rss.marketingtools.apple.com/api/v2/us/books/top-paid/50/books.json` (verified) | cover filename is an ISBN in 22/25, else title + author | daily |
| NYT 100 Best Books of the 21st Century; Modern Library 100; Time 100; Guardian 100; BBC Big Read; 1001 Books | static imports | title + author | static |
| Hardcover Trending | Hardcover GraphQL `books_trending` (key exists) | Hardcover IDs direct | daily |
| Open Library Trending | `openlibrary.org/trending/daily.json` | work → `cover_edition_key` | daily |

### Comics (~4)

| List | Source | Matching | Cadence |
|---|---|---|---|
| Eisner Awards (Best Graphic Album, Best Continuing Series) | Wikipedia (comic-con.org 503s; Wikidata has no Comic Vine IDs) | title → ComicVine volume | yearly, July |
| NYT Graphic Books and Manga (live today) | NYT API, now monthly | ISBN → volume | monthly |
| Harvey Awards | Wikipedia | title | yearly, October |

### Manga (~5)

| List | Source | Matching | Cadence |
|---|---|---|---|
| MAL Top Manga, Most Popular Manga | MAL v2 `manga/ranking?ranking_type=manga|bypopularity` (verified) | MAL IDs direct | daily |
| MangaUpdates top rated | MangaUpdates API (verified) | MU IDs direct | daily |
| Kono Manga ga Sugoi! (Men's and Women's) | Wikipedia tables 2006–2026 (verified) | title → MAL/MU (romanized vs English titles) | yearly, December |
| Manga Taishō | Wikipedia tables 2008–2026 (verified) | title → MAL/MU | yearly, March |

### Games (~15)

| List | Source | Matching | Cadence |
|---|---|---|---|
| The Game Awards GOTY (all-time) | Wikidata `Q78762377`: 12/12 with numeric IGDB ID qualifier P9043 (verified) | IGDB direct | yearly |
| The Game Awards, this year (every category) | nominees ~Nov 16–17 matched to IGDB; live from Wikipedia (+2 min measured); show Dec 10, 2026 | pre-matched nominees | live window |
| The Steam Awards (every category, every year) | store.steampowered.com/steamawards/<year>, `data-steam_awards_config` JSON with `winner_appid` (verified 2024, 2025) | appid → IGDB via `external_games` (12/12 verified) | yearly, Dec/Jan |
| D.I.C.E., BAFTA Games, Golden Joysticks, GDC GOTY | Wikipedia / Wikidata | IGDB | yearly |
| Steam Weekly Top Sellers | `IStoreTopSellersService/GetWeeklyTopSellers/v1`, no key, past weeks available (verified) | appid → IGDB | weekly |
| Steam Most Played | `ISteamChartsService/GetMostPlayedGames/v1`, no key (verified) | appid → IGDB | weekly |
| Most Wishlisted Upcoming | IGDB `popularity_primitives` type 10 (verified) | IGDB direct | daily |
| Twitch Top Games | Helix `games/top` with our IGDB Twitch credentials; `igdb_id` on 80/100 (verified) | IGDB direct; drop non-games | live |
| World Video Game Hall of Fame | Wikipedia (Wikidata has 2 items) | IGDB | yearly, May |
| Time All-TIME 100, Edge 100, 1001 Video Games | static imports | IGDB | static |

### Anime (~6)

| List | Source | Matching | Cadence |
|---|---|---|---|
| MAL Top Anime, Top Airing, Most Popular, This Season | MAL v2 `anime/ranking` and `anime/season/{y}/{s}` (verified) | MAL IDs direct (Spine stores anime per season) | daily |
| Crunchyroll Anime of the Year (+ categories) | Wikipedia (official site 403s; Wikidata empty) | title → season-level MAL ID | yearly, ~late May |

### Music (~16): Spine music is albums only (MusicBrainz release groups)

| List | Source | Matching | Cadence |
|---|---|---|---|
| Rolling Stone 500 Greatest Albums (2020) | MusicBrainz series `6a4b53b9-2756-4afe-93f2-306039d41910` (verified, 500 unique) | release-group IDs included | static |
| 1001 Albums You Must Hear Before You Die (2005 edition) | MB series `4bc2a338-e1d8-4546-8a61-640da8aaf888` (verified) | included | static |
| Apple Music 100 Best Albums | MB series `d1d985e6-94ea-4c68-a7ac-63b98b6c0318` (verified) | included | static |
| Pitchfork Best New Album (last 12 months), 50 Best of 2025, 200 Best of the 2010s, decade lists | MB series (verified; volunteers lag ~4 weeks) | included | weekly refresh |
| The Needle Drop Top 50 (yearly) | MB series `788a3664-…` (2024 verified) | included | yearly |
| Guardian 100 Best Albums Ever | MB series `acf170fe-…` (verified) | included | static |
| Grammy Album of the Year (winners + nominees) | MB series `64249380-…` (347 entries, has 2026) + live from Wikipedia (+4 min) | included | yearly + live |
| Mercury Prize shortlist | MB series `b498d904-…` (to 2025) | included | yearly |
| Apple Music Top Albums (US, UK, more) | `rss.marketingtools.apple.com/api/v2/{cc}/music/most-played/100/albums.json` (verified) | Apple ID → MB 45%, title + artist fallback | daily |
| ListenBrainz top albums this week / all time | `api.listenbrainz.org/1/stats/sitewide/release-groups` (verified) | IDs included (dedupe) | weekly |
| 1001 Albums Generator community ratings (closest to RYM) | `1001albumsgenerator.com/api/v1/albums/stats` (verified, unofficial) | Spotify ID → MB ~53%, title fallback | daily |
| UK Official Albums Chart | officialcharts.com HTML with dated archive (verified) | title + artist | weekly, Friday |

## Later, or needs a decision

- Goodreads Choice Awards and Best Books Ever: Goodreads' terms forbid scraping (low-rate HTML works today). Choice winners are also on Wikipedia.
- Amazon Charts: terms and anti-bot.
- Billboard 200: terms restrict reuse; AI crawlers are routed to paid TollBit. Billboard #1 albums are on Wikipedia.
- AniList trending, top, seasonal: AniList's API was switched off on 2026-10-21 ("temporarily disabled due to severe stability issues").
- Song charts (Hot 100, Apple Top Songs): Spine can't list songs; possible only by mapping to "Single" release groups.
- Disney+/Prime/Max top 10s: no official feed; FlixPatrol via MDBList external lists, or FlixPatrol's API from $9.99/month.

## Not possible

RateYourMusic (terms ban automation, robots.txt `Disallow: /`, Cloudflare), Album of the Year, AcclaimedMusic, Metacritic music, Spotify Charts (login wall), SteamDB (FAQ bans scraping), Backloggd (robots blocks `/lists/` and `/api/`). Letterboxd itself bans scraping (terms §6.8, §6.11); we use MDBList, which mirrors it, so if Letterboxd blocks MDBList those lists stop updating.

## Live awards: measured latency

| Award | Best free signal | Latency |
|---|---|---|
| Nobel Literature | official API / RSS | minutes (Wikidata +3, Bluesky +7) |
| Oscars, Globes, Grammys, Emmys, The Game Awards | Wikipedia ceremony page (bold / ‡ / infobox) | +2 to +4 min |
| Booker | Wikipedia (+1 min in 2025), official Bluesky | ~1–3 min |
| National Book Awards | official page (Wikipedia took ~10 h) | unmeasured |
| Pulitzer | pulitzer.org blocks scripts (403); Wikipedia +68 min; AP +29 min | 30–70 min |

Design: match every nominee when nominations come out; poll one batched MediaWiki revisions request every 20–30 s and the official source every 60 s inside each window; publish only when the winner is a pre-matched nominee, one per category, and the edit survives ~60 s without a revert; otherwise queue for review. Vandalism seen on these nights was reverted within 4–20 s.

## Next announcement nights

| Date (2026–27) | Event | Status |
|---|---|---|
| Nov 9 | Booker Prize winner | confirmed |
| Nov 11 | Golden Joysticks | confirmed |
| Nov 16 | Grammy nominations | confirmed |
| ~Nov 16–17 | Game Awards nominees | expected |
| Nov 18 | National Book Awards, 8pm EST | confirmed |
| Dec 7 | Golden Globe nominations, 5am PST | confirmed |
| Dec 10 | The Game Awards, 4:30pm PT | confirmed |
| Jan 10 | Golden Globes, 5pm PST | confirmed |
| Jan 21 | Oscar nominations, ~5:30am PT | confirmed |
| Feb 7 | Grammys | confirmed |
| Mar 14 | Oscars, 4pm PT | confirmed |
| ~May 3 | Pulitzers, 3pm ET | expected |

## Architecture (from the pipeline design)

- `src/lists/featured/`: a Python registry of list definitions, adapters per source family, one resolver per media type (IDs first, strict title search second, `overrides.csv` for manual fixes, Redis-cached), and a writer with guards (exact count, minimum resolve rate, maximum drop vs the previous run, never wipe on a bad fetch).
- Two new tables: `FeaturedListSync` (health: last success, last error, resolve rate, unresolved entries, content fingerprint) and `FeaturedListSnapshot` (one compact row per list per day when content changes, ~10–70 MB a year). No new `CustomList` columns.
- A second Celery worker on a `featured` queue plus a dispatcher every 60 s, because the single existing worker is shared with 6-hour imports. Live windows take priority; daily lists wait.
- The two existing commands become thin wrappers, so the workflow, the deploy script, the new Letterboxd task and their tests keep working.
- Item creation batches external-rating enqueues instead of one task per new Item.
- API: additive fields plus a shelves endpoint; old builds keep seeing today's six lists; batched queries (today's endpoint would run ~600+ queries at 200 lists).
- Health endpoint `GET /api/v1/lists/featured/health/` for staff or a report token, read by the weekly review agent.

## Search page (from the iOS design)

Recommended: shelves. A "Now" strip of chart cards on top (live charts with ranks as text; award nights showing "7 of 23 announced"), then one sideways row per media type of compact tiles (148×162 pt, five overlapping posters, rank chip, "Updated 2h ago" or LIVE badge), "See all" opening that type grouped by kind (live charts, awards, critics and canon, community), and a long-press peek. Choosing one type in the media lens shows that type's grouped view directly. A bolder alternative, a literal bookshelf of spines, is parked for later. Also found: `ProfileListDetailView` shows Edit and Delete on Spine-owned featured lists to every user (the server refuses with a 403).

## Costs

- MDBList: free gives 1 external list refreshed weekly that stops updating after 120 days without a login. Standard €2/month = 10 external lists, Plus €3 = 20, VIP €5 = 30, all refreshed daily and never expiring. Production currently has no MDBList key of its own and runs on upstream Yamtrack's default free key (1,000 calls a day), which also powers external ratings.
- Everything else in the launch set is free. Trakt needs a free app client ID.
