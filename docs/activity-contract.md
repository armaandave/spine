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
  "started watching", "started playing". Starting is a new reading journey, manga or
  comic attempt, or game playthrough, or a show, season, or anime set to In progress.
  Resuming something paused isn't starting it; starting again after finishing or
  dropping is. Carrying on with a finished manga or comic isn't (Keep Reading, or a
  comic's new issue: MC-200, MC-202).

Nothing else is activity: not rating a title without a log, not marking something
watched, read, played, or completed without a log, not other status changes,
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
  or `picked back up`. It's on a book, game, show, manga or comic, never anime, and
  never from an import, a mark, a restore or Restart. It adds nothing to any number,
  and nothing shows it yet (AC-1). A book's Restart writes `began_over` on its start
  row, and so does a reread, which opens over a finished read: deleting that log later
  can't change where it began. A pick-up with nothing before it to carry on from, a
  deleted attempt or an imported place, still isn't a start.
- The day is the phone's date when the progress was saved, stored as `saved_on`, and it
  never moves with the time zone of whoever reads it. A row from before that column
  reads its moment on the server's zone.
- Anime has no steps: its history rows are its saves. So each one says what it was in
  its change reason: `day:<date>`, the date it counts on; `log:<id>`, written by that
  log; or `mark`, which counts on no day. Only the comparison reads these yet.

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
- Home shows the feed: `/api/v1/feed/`, the activity of the people you follow.

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
  `{"type": "diary", "id", "rating", "liked", "is_rewatch"}`. It also has
  `review_title`, `review`, and `contains_spoilers`.
- A day's progress: `type` is `progress_updated`, `id` is negative (so ids stay
  unique across both kinds), `media` is the title, and `object` is
  `{"type": "progress_day", "id", "summary", "label"}`, for example
  `"watched 4 episodes"` and `"4 episodes"`.
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

## AC-7: Likes and comments

- Anyone who can see an item can like it and comment on it. Comments have no replies.
- A comment's author, the item's owner, and admins can delete a comment. Nobody across
  a block from you shows up in the comments you see.
- Likes and comments on a log belong to the log. Those on a day's progress belong to
  that person, title, and day, in a `social.ProgressPost` saved at the first like or
  comment, so later progress that day keeps them.
- `object.id` of a day's progress addresses it: `/api/v1/progress/<id>/` is its page,
  with `like/` and `comments/` under it. A log uses `/api/v1/diary/<id>/like/` and
  `comments/`. `DELETE /api/v1/comments/<id>/` removes a comment.
- Deleting a journey, attempt, playthrough or season's progress deletes the likes and
  comments of each day it leaves with no progress. Removing a title deletes all of that
  person's progress, likes and comments on it. Neither comes back on a later save that
  day, and each post removed writes a `SocialAuditLog` row.

## Review status

AC-2's status moves, `began_over`, `saved_on` and anime's change reasons, and AC-7's
deletion of the likes and comments a removed journey, season or title leaves behind, are
provisional until the phone test of Activity's first phase accepts them.
