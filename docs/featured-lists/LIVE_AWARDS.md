# Live award nights

How award-night lists work, and how to add a ceremony's nominees and window. The code is `src/lists/featured/adapters/award_page.py` (polling and publishing) and `src/lists/featured/wikitext.py` (reading a page); the definitions are in `src/lists/featured/definitions/awards_live.py`.

## How a night works

1. **Before:** the list is the nominees from `static/nominees/<key>.csv`, in file order, each noted "Nominee · <Category>". The card shows `0 of M announced`, where M is the number of categories.
2. **During the window:** every 30 s the dispatcher queues a poll. A poll makes one batched MediaWiki request (`prop=revisions|info`, up to 50 titles) for the ceremony's pages. If a page changed, it fetches the new revisions (up to 10) and reads each one's winner markers:
   - an entry that starts in bold (`'''…'''`);
   - a double dagger (`‡`, `{{double-dagger}}`) or `{{won}}`;
   - a row or cell shaded in a colour the page's `{{legend|<colour>|… winner}}` declares (the Booker);
   - an entry in a table column headed "Winner" (the Golden Joysticks, the National Book Awards);
   - an infobox `awardN_type`/`awardN_winner` pair, or a field named in the `infobox` param.
3. **Publishing:** a winner is published only when all three hold:
   - it is a nominee in the file;
   - it is the only one marked in its category, unless the page marks a tie ("(tie)", `{{tie}}`, "joint winners");
   - it has been marked, with no break, for 60 s, counted from the timestamp of the revision that first marked it.

   Published winners rise to the top in category order, noted "Winner · <Category>", and the card says `N of M announced`. A vandal edit reverted within seconds never publishes. A published winner stays until another nominee has been marked for 60 s; a category whose marker disappears keeps its winner.
4. **Review:** a marked entry that isn't a nominee, or two marked winners without a tie, publishes nothing for that category. It goes to the health report's `unresolved` with the reason, under the note "Review · <Category>". Nominees without a ref go there too.
5. **After the window:** the list is re-read every 3 hours, which catches late edits.

The state for all this, per list, sits in the cache under `featured:award:<key>`. If the cache is lost, the next poll rebuilds it from the page's last 10 revisions.

## Adding a ceremony

### When nominations are announced

1. **Write the nominee file.** Fill `static/nominees/<key>.csv` with the header `category,rank,title,year,creator,ref,note`.
   - `category`: the category as the ceremony page will label it, in the order the page lists categories. Include only categories whose nominees are Spine items of the list's media types. A performance category's item is the film or game; put the performer in `note`.
   - `rank`: the nominee's position within its category, in the official order.
   - `title`: the title as Wikipedia will most likely write it. Matching ignores case, punctuation and accents. It accepts the full title or the part before a subtitle, and Roman sequel numbers equal digits ("Slay the Spire II" matches "Slay the Spire 2").
   - `ref`: the Spine ref (`tmdb:movie:…`, `igdb:game:…`, `hardcover:book:…`, `musicbrainz:music:<release group>`). Verify every one by title, creator and year; leave it blank rather than guess. A blank ref keeps the row a nominee, but it can't appear in the list.
   - `note`: optional detail shown after the category ("Jennifer English as Maelle", "Translated by …").
   - A nominee in several categories gets one row per category with the same ref. The list shows it once.
2. **Check the definition's params in `definitions/awards_live.py`:**
   - `pages`: the page titles to watch. Redirects are never followed: "The Game Awards 2026" redirects to the general article until the ceremony page exists, and reading that article would find past winners. When the title isn't certain, list every likely one ("84th Golden Globes", "84th Golden Globe Awards").
   - `{"title": …, "section": …}`: reads one section only. The Golden Joysticks article keeps every year on one page, under a heading per year.
   - `aliases`: `{category: [page label, …]}`, for when the page labels a category differently from the file. Known labels:

     | Ceremony | File category | Wikipedia label |
     |---|---|---|
     | Oscars | Best Director | Best Directing |
     | Oscars | Best Actor | Best Actor in a Leading Role |
     | Oscars | Best Original Song | Best Music (Original Song) |
     | Oscars | Best Animated Feature | Best Animated Feature Film |
     | Golden Joysticks | Still Playing - Mobile | Still Playing Award (Mobile) |
     | Booker | The Booker Prize | Nominees (the section heading) |

     Category names are compared the same way as titles, so dashes and "&" vs "and" don't matter.
   - `titles`: `{nominee title: [other title, …]}`, for when Wikipedia may use a different name ("GTA Online" vs "Grand Theft Auto Online").
   - `infobox`: `{field: category}`, for infobox fields beyond the `awardN` pairs, such as the Oscars' `best_picture`.
3. **Turn the list on:** set `featured=True` (the default) once the file has nominees. The registry test refuses `featured=True` with an empty file.
4. **Dry-run it** from the worktree:

   ```sh
   cd src && uv run --locked python manage.py sync_featured_list <key> --dry-run
   ```

   Every nominee should resolve. A resolved row is `[ref]`; a transient provider error on a new Item ("metadata lookup failed … 429") retries on the next poll. No row should be a winner before the ceremony.
5. **Run `scripts/check.sh`**, then ship before the window opens.

### The window

Build the window from the ceremony's local time with `live_window`. It converts to UTC, daylight saving included: the 2027 Oscars fall on the day US daylight saving starts, so 4pm PT is 23:00 UTC. Verify the date and start time from the organiser, or reliable press when the organiser hasn't published it, and say which in a comment.

```python
window=("2027-03-14 15:00", "America/Los_Angeles", timedelta(hours=7)),
```

The window should start an hour before the first award: the pre-show for The Game Awards, the Premiere Ceremony for the Grammys. It should end a few hours after the last. The dispatcher starts polling 20 minutes before the window, and cards show "upcoming" or "done" for 24 hours either side.

## This season

| List | Window (UTC) | Local | Nominees | Status |
|---|---|---|---|---|
| `booker-prize-2026` | Nov 9 19:30 – Nov 10 00:30 | Mon 19:30–00:30 GMT; winner ~21:45–22:00 | 6/6 resolved | featured |
| `golden-joystick-awards-2026` | Nov 11 16:30–22:30 | Wed 16:30–22:30 GMT; show 17:30 | 144 rows (109 games), 144 resolved | featured; Ultimate GOTY shortlist due Oct 26 |
| `national-book-awards-2026` | Nov 19 00:00–06:00 | Wed Nov 18 19:00–01:00 EST; ceremony 20:00 | 25/25 resolved | featured |
| `the-game-awards-2026` | Dec 10 23:30 – Dec 11 06:00 | Thu 15:30–22:00 PST; pre-show 16:30 | due ~Nov 16 | `featured=False`, empty file |
| `golden-globes-2027-film` / `-tv` | Jan 11 00:00–06:00 | Sun Jan 10 16:00–22:00 PST; show 17:00 | due Dec 7 | `featured=False`, empty files |
| `grammy-awards-2027` | Feb 7 19:30 – Feb 8 06:30 | Sun 11:30–22:30 PST; 2026's premiere was 12:30, telecast 17:00 | due Nov 16 | `featured=False`, empty file (album categories only) |
| `oscars-2027` | Mar 14 22:00 – Mar 15 05:00 | Sun 15:00–22:00 PDT; show 16:00 | due Jan 21 | `featured=False`, empty file |

Latency: ceremony pages named winners 2–4 minutes after the announcement for the Oscars, Globes, Grammys, Emmys and The Game Awards, and about 1 minute for the 2025 Booker. Spine adds 60–90 s for the hold. In the 2025 replay, Game of the Year published 79 s after Wikipedia named it.

The National Book Awards are the slow one. In 2025 Wikipedia's "National Book Award for Fiction" list had the winner about 9 hours after the ceremony, and the "2025 National Book Awards" page was created the next afternoon. The 3-hourly re-read after the window picks up such late pages.

## Reviewing

The health report (`manage.py featured_lists_report`, or `GET /api/v1/lists/featured/health/`) lists each problem as an unresolved row. Its key is `t:<category: reason>`, and its reason explains it:

- **"marked entry is not a pre-matched nominee: …":** the page marked something the file doesn't know.
  - If it's a real nominee under another name, add it to `titles`, or fix the file.
  - If it's vandalism, it disappears on the next poll.
- **"N nominees are marked as winner and the page marks no tie":** wait for the page to settle. Editors usually mark a real tie ("(tie)") within minutes; until the page does, the category stays unannounced.
- **"nominee has no Spine match" / "winner has no Spine match":** add the ref once the item exists on its provider.

Editing a nominee file re-syncs its list and resets its live state.
