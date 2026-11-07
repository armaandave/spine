"""Recording that someone added a title to Planning (docs/activity-contract.md, AC-1).

Planning is a status on the tracking row, so an app add, a website add and an import
leave the same trace. Only a request that says Planning, from a person, is an add: it
is recorded where the request lands, so imports, which write tracking rows directly,
can never make one (SW-500). Shows and anime record theirs where they choose a status
(app.tv_tracking._record_status_move), so they aren't handled here.
"""

from app.models import MediaTypes, Status

# The types that record their own Planning adds.
RECORDED_ELSEWHERE = (
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
    MediaTypes.ANIME.value,
)


def status_of(tracking):
    """Return a tracking row's status, or None when there is no row yet."""
    return None if tracking is None or tracking.pk is None else tracking.status


def record_if_added(user, item, *, asked, before, after):
    """Record an add: Planning was asked for, the title now sits there, and didn't before.

    `asked` is the status the request carried, `before` and `after` the tracking row's
    status around the write (None for no row). A book with an open journey refuses
    Planning and a movie with a log ends Completed, so neither adds.
    """
    from social.models import ProgressChange  # noqa: PLC0415

    planning = Status.PLANNING.value
    if item.media_type in RECORDED_ELSEWHERE:
        return
    if asked == planning and after == planning and before != planning:
        ProgressChange.record_planning(user, item)
