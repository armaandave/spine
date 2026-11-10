# Activity Contract

Activity is what people see of each other: the home feed, a profile's Activity list
and a friend's post pages. This contract defines it once, for every media type. All of
them read the one calculation in `api.services.activity_rows` (docs/stats-contract.md),
so a list adds up to the number that opened it and a fixed typo fixes the old row.

## AC-1: Activity is logs, each day's progress and status moves

These are activity:

- **A diary log.** Imported logs are activity like any other log.
- **A day's progress on a title.** All of a person's progress on one title in one
  day is one item: "watched 4 episodes", "read 15%", "played 2h 30m". A show's
  episodes across seasons count toward the show. Each later change that day updates
  the same item rather than adding another.
- **A status move** the person chose: paused, stopped (Did Not Finish or Dropped) or
  picked back up (decision 21). It is a row of its own with no amount, so "read 40 pages,
  paused" on a day with progress and "stopped" on a day without. It adds nothing to any
  number. Anime records them too (decision 63).
- **Starting a title**, on a day with no progress on it: "started reading",
  "started watching", "started playing". Starting is a new reading journey, manga or
  comic attempt, or game playthrough, or a show, season, or anime set to In progress.
  Resuming something paused isn't starting it (it reads "picked back up"); starting
  again after finishing or dropping is, and says only "started" (decision 44 for a game:
  a new playthrough). Carrying on with a finished manga or comic isn't (Keep Reading, or a
  comic's new issue: MC-200, MC-202).
- **Adding titles to Planning** (Phase 5): a person's day of adds is one row,
  "Added 3 shows to Planning" (AC-8). It is recorded when a request from the person
  says Planning and the title wasn't in Planning before: the app, the website and the
  API (`social.ProgressChange` kind `planning`, `saved_on` the owner's calendar day).
  An import never records one, even a watchlist that puts titles in Planning (SW-500),
  and neither does a webhook or a job. Leaving Planning and coming back is a second
  add. Heart, stars or a note that make a Planning row as a side effect aren't an add.
  A Planning row adds nothing to any Stats number and isn't an active day.

Nothing else is activity: not rating a title without a log, not marking something
watched, read, played, or completed without a log (the eye, a heart, stars, Mark
watched up to here, an undated import: they only reach All Time's undated pile), not
hearts, follows, or adding to a list.

## AC-2: Progress, told plainly

- A day's progress is its net change. A day that didn't move forward, such as
  unticking an episode, shows nothing.
- It is told in the title's own unit: episodes (TV, anime), chapters (manga),
  issues (comics), pages or percent (books), playtime or percent (games). When a
  game moves in both playtime and percent on one day, playtime tells it.
- Progress that day replaces a start, as the same item. A log doesn't replace either:
  a log and that day's progress or start on the same title are separate items.
- Starts are recorded in `social.ProgressChange` as a step of kind `start`; they
  never count as a title's latest progress. Deleting a book journey, a manga or comic
  attempt, or a game playthrough deletes its start and progress with it.
- A status move the person chooses is recorded the same way, as a step of kind
  `status` with value 0 in previous and current and its `move`: `paused`, `stopped`
  or `picked back up`. It's on a book, game, show, anime, manga or comic, and never
  from an import, a mark, a restore or Restart. It adds nothing to any number, and
  shows as a row of its own (AC-1). A book's Restart writes `began_over` on its start
  row, and so does a reread, which opens over a finished read: deleting that log later
  can't change where it began. A pick-up with nothing before it to carry on from, a
  deleted attempt or an imported place, still isn't a start.
- A title added to Planning is recorded the same way (AC-1), as a step of kind `planning`
  with value 0 in previous and current, written by `ProgressChange.record_planning` from
  the request that adds it. It is never a title's latest progress and no number reads it.
- The day is the phone's date when the progress was saved, stored as `saved_on`, and it
  never moves with the time zone of whoever reads it. A save sent with a date (the date
  picker, on books, manga, comics and games) is on that day, under BK-306's rules: a day
  before the start is refused, a day after today too, and the fold reads saves in the
  order they were made, so a save dated earlier than an earlier-made one adds its rise
  on its own day. A row from before that column
  reads its moment on the server's zone.
- Anime keeps its own rules (docs/anime-tracking-contract.md, AN-703): only ticks move
  its progress steps, and choosing Watching, a tick that puts it in Watching or starting
  a rewatch is its start, each with its `saved_on`. The calculation reads its viewings,
  as Stats did (AN-704): a tick on its day, a log's fills on the log's calendar date, a
  mark's fills and a stored number with no viewings in the undated pile. Each log is its
  own row, so a log that fills nothing still counts its day.
- From Phase 3 the calculation reads TV, movies and music too. A TV row covers the whole
  show: a tick on its day, a log's fills and an episode log's own viewing on the log's
  date, a mark's fills in the pile. A film or album log is its own row, its full length
  on its date, and a marked one is in the pile once. TV and anime follow STAT-104's
  switch rules, worked out when read: a Rewatch-off first log over a mark dates it, a
  Rewatch-on season or show log counts what it covers again, and a backdated log takes
  the ticks it describes. A mark still never makes a row; the log that dates it does.

## AC-3: Newest date first

Activity is ordered by calendar day, newest first: a log by its log date, a day's
progress by the day it was saved on (AC-2). Days are local, so a log at 9pm stays on its
own day, and a save reads the same day for every viewer in every zone. Within
one day, the most recently written comes first. When a log was posted doesn't
matter, so backfilling old logs doesn't push them to the top: a log for 2019 sits
at 2019.

Activity shows its date, not when it was posted.

## AC-4: One list in three places

- A profile's Recent Activity is the top of that person's activity, showing each
  title once.
- A profile's Activity list is all of it, newest day first, and opens from the numbers
  on Stats (the Activity list, below).
- Home shows the feed: `/api/v1/feed/`, the activity of the people you follow.

All three are computed from the Activity calculation (`DiaryEntry`, the saves and the
viewings behind it), so an edit or deletion shows everywhere at once. A log's day is
its owner's calendar day on Home as on the owner's list, whoever is looking. A dated
import is a row on its real date, so an old play sits in the past, not on top.

## AC-5: Visibility

- You see all of your own activity.
- Others see your public logs, and followers-only logs if they follow you. Movie,
  album, book, and game logs are always public.
- Progress, and a day of Planning adds (AC-8), are visible to anyone who can see the
  profile. A log a viewer can't see adds
  nothing and moves nothing for them, on the list, on Home, on post pages and in the
  numbers beside them.
- A private profile shows nothing to someone who doesn't follow it. A block in
  either direction shows nothing.

## AC-6: Friends on a title's page

A title's page shows the people you follow who've done something with it, newest
first. It says where each person stands, so it reaches past activity: a rating or a
heart without a log counts, and so does a title marked finished, paused, or stopped.
Wanting to get to a title doesn't count.

- Someone with a log you can see (AC-5) shows it: their newest, or their newest with a
  review when one has one.
- Someone without one shows their status and how far they got.
- Someone with a private log shows their status and progress, and whatever their
  visible logs say, but not the title's rating or heart, since a private log can set
  both.
- The count is how many people show.
- From three people, the section opens a page listing them all, under a chart of
  the ratings they show.

## AC-8: The Planning row

A person's titles added to Planning in one day (AC-1) are one row: "Added 3 shows to
Planning", or "Added 6 titles to Planning" across types. Books, manga and comics say
Planning too, not "To Read".

- **One row per person per day**, on the owner's calendar day. A title added twice that
  day counts once; leaving Planning and coming back on a later day is a new add on that
  day. Only titles the server exposes and the person still tracks count: removing a title
  deletes its Planning rows (AC-2's receiver) and the read checks it again, so the row
  says 5, then 4 when one is removed.
- **Its face** is the day's earliest add: that title's poster and name, so the row
  doesn't change as more are added. On Home with a `media_type`, only that type's titles
  count.
- **Visibility** is progress's (AC-5). It has no likes or comments, so `like_count` and
  `comment_count` are 0, there is no post page (`/api/v1/progress/<id>/` is 404 for its
  id), and no push (AC-7).
- **It is no number.** It is not in the calculation (`activity_rows`): no Stats number,
  no active day, no pile and no row on a list opened from a number sees it. Marks still
  never make a row.
- **Only builds that ask get it.** `include=planning` on `/api/v1/feed/` and on
  `/api/v1/users/<username>/activity/`. Without it both return exactly what they did
  before, cursors included, because installed builds print an unknown row as "shared
  activity". On a profile it is also ignored when the request names a number (a type,
  dates, `counts`): a list always adds up to its number. The profile rail and the profile
  row's `counts.activity` don't ask.
- **Opening it:** `GET /api/v1/planning/<object.id>/` returns the titles added that day,
  oldest add first: `{"actor", "consumed_at", "summary", "count", "results"}`, `results`
  being media summaries. `media_type` narrows it to the type the row counted. It needs a
  signed-in viewer who can see the profile, else 404.

## AC-9: Fixing a row and taking one back

On a person's own book, manga, comic or game progress row (decision 52): the page offers
Edit, and a trash on the newest row only. A TV or anime row has neither (unticking fixes
TV). They are for a read that is still going, reading or paused: a finished read's pages
belong to its log, and a stopped read's rows are fixed by reading on. The server says
what is offered (the post page's `viewer`, below); the app holds no rule of its own.

- **Delete** (`DELETE /api/v1/progress/<id>/`, 204): the owner, or an admin (which writes
  a `SocialAuditLog` row, `progress_delete`). Anyone else who can see the row gets 403,
  someone who can't see it 404, a row with nothing to take back 400. It removes that
  day's saves on the title and puts the place back to where it stood before them (a first
  save goes back to nothing). The day's start and status moves stay, so a day that
  started stays as "Started reading". The day's post, likes and comments go when the day
  has no row left (AC-7). Only the newest row can be deleted: newest means nothing was
  saved on the read after it, decided when the request arrives, so a save from another
  device since the page loaded gives 409. Stats move at once (`bump_user`).
- **Fix an older row** (`PATCH /api/v1/progress/<id>/` with `{"value": N, "unit"?}`;
  200 with the row's page, or 204 when the fix left the day with no row): the owner only
  (403 for anyone else, an admin too). The day's last save is rewritten in place, and the
  next save's "from" with it, so nothing else moves and the place stays: 100, 120, 200
  with the second fixed to 110 reads 100, 10, 90, still 200 (decision 58). `N` stays
  between the saves either side (`edit.min` to `edit.max`, inclusive), else 400; equal
  to the number before empties the day. Whole numbers for pages, counts and minutes; a
  percent has up to two decimals. `unit` picks which number a game's day edits when it
  saved both (`edit.unit`).
- **Edit on the newest row** has no route of its own: the app opens the normal sheet with
  `edit.value` and `edit.date` and saves the number again with that date. A drop comes
  off the newest amounts first, which is that row. `PATCH` on it is 400.
- **What the page says**, in `viewer` of `GET /api/v1/progress/<id>/` and nowhere else
  (the feed and the lists never carry it), to the owner, and to an admin for
  `can_delete`: `can_edit`, `can_delete` and `edit`, null when there is no Edit:
  `{"mode": "save" | "number", "value", "unit", "kind", "date", "min", "max"}`. In `save`
  mode `min` is null and `max` is the title's length, if it has one.
- It adds nothing to the calculation: the rows are the saves, read as ever.

## Wire shape

`/api/v1/users/<username>/activity/` and `/api/v1/feed/` return cursor pages in
the shape installed apps read. `consumed_at` is the item's calendar day
(`YYYY-MM-DD`) and `created_at` when it was last written.

- A log: `type` is `diary_created`, `id` is the log's id, and `object` is
  `{"type": "diary", "id", "rating", "liked", "is_rewatch"}`. It also has
  `review_title`, `review`, and `contains_spoilers`.
- A day's progress: `type` is `progress_updated`, `id` is negative (so ids stay
  unique across both kinds), `media` is the title, and `object` is
  `{"type": "progress_day", "id", "summary", "label"}`, for example
  `"watched 4 episodes"` and `"4 episodes"`. Since Phase 4 `object` also has `amount`
  (a number in the tab's unit: minutes on TV, anime, games, movies and albums, else
  pages, chapters or issues), `unit`, `moves` (`paused`, `stopped`, `picked_back_up`),
  `percent` (a percent told with no length) and `time_label` ("2h 15m", on TV, anime,
  movies and albums). `label` is null on a day with only a status move. `summary` is
  lower case and carries the moves: "read 40 pages, paused", "picked back up".
  `object.place` is where the read stood at the end of that day, from its last save, by
  unit: `{"pages": 893}`, `{"percent": 45.5}`, `{"chapters": 41}`, `{"issues": 12}`, or
  a game's `{"minutes": 840, "percent": 80}`. A log that finished the read afterwards
  took the rest on its own row, so `place` can be short of where the person stands now;
  the feed card's bar and "p. 893" read it. It is null on TV and anime days and on a day
  with no number saved (a start or a status move alone).
- A log's `object` also has `amount`, `unit`, `time_label` and `label`, what the log
  added in the units it was told in with no plus sign ("200 pages", "3 episodes", "2h");
  movie and album logs send no `label`.
- A day of Planning adds (AC-8), only when asked: `type` is `planning_added`, `id` is
  negative like a progress day's, `media` is the day's earliest add, and `object` is
  `{"type": "planning", "id", "summary", "label", "count", "media_type"}`: `summary`
  "added 3 shows to Planning" (lower case, as the others), `label` null, `count` the
  titles, `media_type` the one type they share or null. `id` is
  `(person << 44) | day ordinal`, at or above 2^44, an address with no Item (no Item has
  id 0), so it can't be a progress day's. `like_count` and `comment_count` are 0.
- On a progress day's own page (`GET /api/v1/progress/<id>/`) the owner's `viewer` also
  has `can_edit`, `can_delete` and `edit` (AC-9), and an admin's `can_delete`.
- Both have `like_count`, `comment_count`, and `viewer.has_liked`. `object.liked` is
  the person's own heart on the title, which is not a like of the post.

The `social.Activity` rows that tracking, rating, and list code still write are
not read by these endpoints.

`/api/v1/media/<source>/<media_type>/<media_id>/friends/` takes `season_number` and
`episode_number`, and returns `{"count", "results"}`, each result
`{"user", "diary_entry_id", "consumed_at", "rating", "liked", "is_rewatch",
"review_title", "review", "contains_spoilers", "status", "progress_label"}`.
`status` is the tracking status, and `progress_label` says how far they got, for
example `"212 of 476 pages"`, `"45%"`, or `"2h 30m"`, only when they have no log.

## The Activity list

`GET /api/v1/users/<username>/activity/` is the same endpoint the profile rail reads. A
request with any of these is a list opened from a number, and reads as the Diary does (a
log row carries no review text, and its `media` has `show_title` and `episode_title`):

- `media_type`: one type (a show's seasons and episodes are the `tv` type). An unexposed
  type is a 404.
- `start_date`, `end_date`: inclusive days on the person's calendar. A year is Jan 1 to
  Dec 31; All Time sends neither. A bad date is a 400.
- `counts`: `titles`, `days`, `weeks`, `multi_title_days`, `episodes` or `hours`, else a
  400. `titles` gives one row per title, on its newest day in range that has an amount, a
  told number or a log (decision 40). `days`, `weeks` and `hours` leave out status-only
  rows (`weeks` lists the same rows as `days`). `episodes` keeps rows with episodes
  told. A plain measure gives every day's row.
- `view=list` makes a plain request read as a list too.
- `include=planning` adds the Planning row (AC-8) to a plain list only: it is ignored
  when the request has a type, dates or `counts`, because a Planning row is no part of
  any number.

A signed-in viewer of a list with a type, dates or `counts` also gets, beside `results`:
`top_line`, the number tapped in words ("360 pages", "5 books", "11 active days",
"3 weeks", "2 hours (2h 15m)", "≈ 86 hours"); with one type, `total`
(`value`, `unit`, `known`, `total`, `approximate`, `time_label`), the number Stats prints;
and `undated` on All Time's first page, when the number includes marks
(`amount`, `unit`, `titles`, `told`, `label`: "Includes 200 books and 48,829 pages with
no date"). Never `undated` for `days`, `weeks` or `multi_title_days`. A logged-out caller
gets public rows and none of the three. Rows plus the line are the number.

A row's `id` and `object.id` address its day: a book, game, manga or comic day with a
saved change keeps the lowest change id; any other day (TV, anime, or no change) is
`(person << 44) | (Item << 20) | day ordinal`, at or above 2^44, a signed 63-bit number.

## AC-7: Likes and comments

- Anyone who can see an item can like it and comment on it, or reply to a comment. Replies go one level deep: a reply to a reply joins the same thread. Replies count in the item's comment count. A deleted comment with replies stays as "Comment deleted" above them, and doesn't count.
- A comment's author, the item's owner, and admins can delete a comment. Nobody across
  a block from you shows up in the comments you see.
- Likes and comments on a log belong to the log. Those on a day's progress belong to
  that person, title, and day, in a `social.ProgressPost` saved at the first like or
  comment, so later progress that day keeps them.
- `object.id` of a day's progress addresses it: `/api/v1/progress/<id>/` is its page,
  with `like/` and `comments/` under it. A log uses `/api/v1/diary/<id>/like/` and
  `comments/`. `DELETE /api/v1/comments/<id>/` removes a comment.
- `object.id` addresses a day: an id from before (a change id) still opens its day. A
  TV or anime day's id is no change id: it names the person, the show and the day.
- A day with no row left for a viewer, because a fix emptied it or a backdated log took
  it, is not found for that viewer: its page, like and comments return 404 until the
  day has a row again. Its likes and comments stay stored. Only a day with no row in the
  viewer-independent view (no log visible, so none moves a row) is deleted with its
  journey, attempt, playthrough or season.
- A Planning row has no likes or comments (AC-8). Deleting the newest row (AC-9) deletes
  its post, likes and comments, unless the day still has a row, such as a start.
- Deleting a journey, attempt, playthrough or season's progress deletes the likes and
  comments of each day it leaves with no progress. Removing a title deletes all of that
  person's progress, likes and comments on it. Neither comes back on a later save that
  day, and each post removed writes a `SocialAuditLog` row.

## Review status

AC-8, AC-9 and AC-1's Planning adds are provisional until the phone test of Activity's
fifth phase accepts them.

AC-2's status moves, `began_over` and `saved_on`, and AC-7's
deletion of the likes and comments a removed journey, season or title leaves behind, are
provisional until the phone test of Activity's first phase accepts them.
