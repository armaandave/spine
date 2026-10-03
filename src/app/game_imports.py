"""Import dated game history without replacing current user tracking."""

from django.core.exceptions import ValidationError
from django.db import transaction

from app import game_tracking, single_weight
from app.models import DiaryEntry


@transaction.atomic
def import_logs(user, item, rows, *, source):
    """Reconcile source records using shared opinion rules and game chronology."""
    if not game_tracking.supports(item) or not source:
        raise ValidationError("Game diary imports require a game and a source.")
    game = game_tracking._locked_game(user, item)
    preserve_rating = bool(game and game.score is not None and game.rating_source_id is None)
    preserve_like = bool(game and game.like_is_independent)
    ordered = sorted(
        enumerate(rows),
        key=lambda pair: (
            single_weight.calendar_date(single_weight.calendar_datetime(pair[1]["consumed_at"])),
            pair[1].get("source_order") if pair[1].get("source_order") is not None else pair[0],
        ),
    )
    created_entries = []
    for order, row in ordered:
        source_id = str(row["source_id"]) if row.get("source_id") is not None else ""
        if not source_id:
            raise ValidationError("Every imported diary row requires its source record ID.")
        existing = DiaryEntry.objects.filter(
            user=user, import_source=source, import_source_id=source_id,
        ).first()
        if existing:
            if existing.item_id != item.pk:
                raise ValidationError("This imported source record belongs to another game.")
            continue
        progress = {
            field: row[field]
            for field in ("total_minutes", "percentage", "start_date")
            if field in row
        }
        _, entry = game_tracking.complete(
            user,
            item,
            completion_date=row["consumed_at"],
            rating=row.get("rating"),
            liked=row.get("liked", False),
            is_rewatch=row.get("is_rewatch"),
            review=row.get("review", ""),
            review_title=row.get("review_title", ""),
            contains_spoilers=row.get("contains_spoilers", False),
            tags=row.get("tags", []),
            import_source=source,
            import_source_id=source_id,
            import_source_order=row.get("source_order") if row.get("source_order") is not None else order,
            emit_activity=False,
            update_current=False,
            force_direct=True,
            **progress,
        )
        created_entries.append(entry)
    game = game_tracking._locked_game(user, item)
    if game and created_entries:
        single_weight._reconcile_imported_current_state(
            game,
            created_entries,
            preserve_rating=preserve_rating,
            preserve_like=preserve_like,
        )
        game_tracking._sync_state(game)
    return created_entries
