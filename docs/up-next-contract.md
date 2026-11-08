# Up next

Home's Up next section lists episodes of your shows and anime that have a date and haven't aired yet.
In progress lists what's out and waiting for you; when an episode airs, Spine moves its title there.
Agreed with the maintainer on 2026-11-08.

TV terms (aired, up next, caught up, regular episode) mean what `docs/tv-tracking-contract.md` says; anime terms what `docs/anime-tracking-contract.md` says.

## UN-001: What Up next lists

- One row per title: the next regular episode to air, with its date.
- Only dated episodes. A season announced with no date, or a show the provider only calls "returning", isn't listed.
- Paused and Dropped titles are never listed.
- TV and anime only, and only types the user has turned on.
- Soonest first. On the same day, the title watched most recently comes first, then by title.
- A row stays until Spine sees its episode air, within about an hour (TV-206, AN-202), which is when the title joins In progress. Its next dated episode then takes its place, if there is one.
- A title can be in In progress and Up next at once: behind in a season that's airing, the next unwatched episode is in In progress and the next one to air is in Up next.

## UN-002: TV you're watching or finished: the season that's airing

A show in Watching or Completed is listed with the next episode of the season that's airing when you're in that season: caught up, or with up next in it.

On S2 E3 with S2 E8 out Thursday, it's listed. On S1 E5, it isn't.

## UN-003: TV you're watching or finished: the next season

A show in Watching or Completed is listed with the first episode of its next season when you're on the latest season that has aired, or have watched everything that has aired.

S3 premieres Mar 12: on S2 E6 or finished, it's listed. Still on S1, it isn't.

UN-002 and UN-003 are one test: you have no up next, or up next is in the latest season with an aired episode.

## UN-004: TV in Planning

A show in Planning is listed only for its own premiere, while nothing of it has aired.
A Planning show that has already aired isn't listed for a new season.

## UN-005: Anime

- **Watching or Completed:** the next episode of a title that's airing, whether you're caught up or behind. A title is one season, so you're always on the newest one.
- **Planning:** only the title's premiere, while nothing of it has aired. People plan specific seasons, so a planned season 2 is listed when it premieres.
- **New seasons** (the next title in MAL's order, AN-500) aren't listed yet.

## UN-006: How it's read

`GET /api/v1/tracking/up-next/` returns `{"results": [{"media": <media summary>, "airing": {...}}]}`, in UN-001's order.
`airing` holds `kind` (`episode`, `season_premiere` or `series_premiere`), `season_number` (null for anime), `episode_number` (null when the provider names a season with no episode list yet) and `air_date` (a day on the user's calendar).

It reads only what tracking stores (TV-207): each title's status, up next and next airing, and the shows' stored episode lists. It never calls a provider, and its query count doesn't grow with the number of titles.
