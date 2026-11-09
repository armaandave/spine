"""Home's Up next: dated episodes that haven't aired yet (docs/up-next-contract.md).

Read only. Everything comes from what tracking already stores (TV-207): each row's
status, up next and next airing, worked out by every write and by the hourly jobs, plus
the shows' stored episode lists. No provider is called.
"""

from app import exposure, tv_listing, user_calendar
from app.models import TV, Anime, MediaTypes, Status

EPISODE = "episode"
SEASON_PREMIERE = "season_premiere"
SERIES_PREMIERE = "series_premiere"

LISTED_STATUSES = (Status.IN_PROGRESS.value, Status.COMPLETED.value, Status.PLANNING.value)


def rows_for(user):
    """Return the user's Up next rows, soonest first (UN-001).

    Each row is (tracking row, airing dict); the airing dict has kind, season_number,
    episode_number and air_date (a date).
    """
    enabled = set(exposure.filter_media_types(user.get_enabled_media_types()))
    today = user_calendar.today(user)
    rows = []
    if MediaTypes.TV.value in enabled:
        rows.extend(_tv_rows(user, today))
    if MediaTypes.ANIME.value in enabled:
        rows.extend(_anime_rows(user, today))
    rows.sort(key=_order)
    return rows


def _order(row):
    """Soonest first; on the same day, the most recently watched first (UN-001)."""
    media, airing = row
    watched = media.last_activity_at.timestamp() if media.last_activity_at else float("-inf")
    return (airing["air_date"], -watched, media.item.shown_title.casefold())


def _candidates(model, user, today):
    # On its air day a row keeps its date until the hourly job sees the episode air and
    # moves it to In progress, so a row never drops out of both (UN-001).
    return (
        model.objects.filter(
            user=user,
            status__in=LISTED_STATUSES,
            reconciled_at__isnull=False,
            next_air_date__gte=today,
        )
        .select_related("item")
        .order_by("pk")
    )


def _tv_rows(user, today):
    shows = list(_candidates(TV, user, today).filter(legacy_pending=False))
    listings = tv_listing.stored_listings([show.item for show in shows])
    rows = []
    for show in shows:
        listing = listings.get(show.item_id)
        if listing is None:
            continue
        latest = listing.latest_aired_season(today=today)
        if latest is None:
            kind = SERIES_PREMIERE
        elif show.next_air_season > latest:
            kind = SEASON_PREMIERE
        else:
            kind = EPISODE
        if show.status == Status.PLANNING.value:
            # Planning: only a show's own premiere (UN-004).
            listed = kind == SERIES_PREMIERE
        else:
            # Caught up, or up next in the latest season that aired (UN-002, UN-003).
            listed = show.up_next_season is None or show.up_next_season == latest
        if listed:
            rows.append(
                (
                    show,
                    {
                        "kind": kind,
                        "season_number": show.next_air_season,
                        "episode_number": show.next_air_episode,
                        "air_date": show.next_air_date,
                    },
                ),
            )
    return rows


def _anime_rows(user, today):
    rows = []
    for title in _candidates(Anime, user, today).filter(next_air_episode__isnull=False):
        kind = EPISODE if title.aired_count else SERIES_PREMIERE
        # Planning: only the title's premiere (UN-005).
        if title.status == Status.PLANNING.value and kind != SERIES_PREMIERE:
            continue
        rows.append(
            (
                title,
                {
                    "kind": kind,
                    "season_number": None,
                    "episode_number": title.next_air_episode,
                    "air_date": title.next_air_date,
                },
            ),
        )
    return rows
