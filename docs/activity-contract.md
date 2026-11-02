# Activity Contract

Activity is what people see of each other: the home feed now, and for everyone
once Spine has other users. This contract defines it once, for every media type.

## AC-1: Activity is logs and each day's progress

Two things are activity:

- **A diary log.** Imported logs are activity like any other log.
- **A day's progress on a title.** All of a person's progress on one title in one
  day is one item: "watched 4 episodes", "read 15%", "played 2h 30m". A show's
  episodes across seasons count toward the show. Each later change that day updates
  the same item rather than adding another.
- **Starting a title**, on a day with no progress on it: "started reading",
  "started watching", "started playing". Starting is a new reading journey or game
  playthrough, or a show, season, anime, manga, or comic set to In progress.
  Resuming something paused isn't starting it; starting again after finishing or
  dropping is.

Nothing else is activity: not rating a title without a log, not marking something
watched, read, played, or completed without a log, not other status changes,
hearts, follows, or adding to a list.

## AC-2: Progress, told plainly

- A day's progress is its net change. A day that didn't move forward, such as
  unticking an episode, shows nothing.
- It is told in the title's own unit: episodes (TV, anime), chapters (manga),
  issues (comics), pages or percent (books), playtime or percent (games). When a
  game moves in both playtime and percent on one day, playtime tells it.
- Progress that day replaces a start. If the person logs the title that day, the log
  replaces that day's start and progress. For a show, a log of the show or any of
  its seasons or episodes counts.
- Starts are recorded in `social.ProgressChange` as a step of kind `start`; they
  never count as a title's latest progress. Deleting a book journey or a game
  playthrough deletes its start and progress with it.
- The day is the local day the progress was recorded.

## AC-3: Newest date first

Activity is ordered by calendar day, newest first: a log by its log date, a day's
progress by that day. Days are local, so a log at 9pm stays on its own day. Within
one day, the most recently written comes first. When a log was posted doesn't
matter, so backfilling old logs doesn't push them to the top: a log for 2019 sits
at 2019.

Activity shows its date, not when it was posted.

## AC-4: One list in three places

- A profile's Recent Activity is the top of that person's activity, showing each
  title once.
- Home shows your own activity until the feed replaces it.
- The feed (`/api/v1/feed/`) is the activity of the people you follow.

All three are computed from `DiaryEntry` and `social.ProgressChange`, so an edit
or deletion shows everywhere at once.

## AC-5: Visibility

- You see all of your own activity.
- Others see your public logs, and followers-only logs if they follow you. Movie,
  album, book, and game logs are always public.
- Progress is visible to anyone who can see the profile.
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

## Wire shape

`/api/v1/users/<username>/activity/` and `/api/v1/feed/` return cursor pages in
the shape installed apps read. `consumed_at` is the item's calendar day
(`YYYY-MM-DD`) and `created_at` when it was last written.

- A log: `type` is `diary_created`, `id` is the log's id, and `object` is
  `{"type": "diary", "id", "rating", "liked", "is_rewatch"}`.
- A day's progress: `type` is `progress_updated`, `id` is negative (so ids stay
  unique across both kinds), `media` is the title, and `object` is
  `{"type": "progress_day", "id", "summary", "label"}`, for example
  `"watched 4 episodes"` and `"4 episodes"`.

The `social.Activity` rows that tracking, rating, and list code still write are
not read by these endpoints.

`/api/v1/media/<source>/<media_type>/<media_id>/friends/` takes `season_number` and
`episode_number`, and returns `{"count", "results"}`, each result
`{"user", "diary_entry_id", "consumed_at", "rating", "liked", "is_rewatch",
"review_title", "review", "contains_spoilers", "status", "progress_label"}`.
`status` is the tracking status, and `progress_label` says how far they got, for
example `"212 of 476 pages"`, `"45%"`, or `"2h 30m"`, only when they have no log.
