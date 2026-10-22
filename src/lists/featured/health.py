"""Health report for every featured list: the API endpoint and the command share it."""

from datetime import timedelta

from django.utils import timezone

from app.providers import quota
from lists.featured import dispatch, engine, ratings, registry
from lists.models import FeaturedListSync

STALE_SLACK = timedelta(hours=1)


def _iso(value):
    return value.isoformat() if value else None


def is_stale(definition, sync, now):
    """Return whether a list has missed more than two cadences, or never synced."""
    if sync is None or sync.last_success_at is None:
        return True
    if definition.cadence is None:
        return False
    return now - sync.last_success_at > definition.cadence * 2 + STALE_SLACK


def _row(definition, sync, now):
    decision = dispatch.due(definition, sync, now, definition_hash=engine.compute_hash(definition))
    window = definition.live_window_at(now)
    entry_count = sync.entry_count if sync else 0
    resolved_count = sync.resolved_count if sync else 0
    return {
        "key": definition.key,
        "name": definition.name,
        "adapter": definition.adapter,
        "kind": definition.kind,
        "media_types": list(definition.media_types),
        "position": definition.position,
        "headline": definition.headline,
        "featured": definition.featured,
        "cadence": registry.cadence_label(definition),
        "list_id": sync.custom_list_id if sync else None,
        "status": (sync.last_status or "never") if sync else "never",
        "stale": is_stale(definition, sync, now),
        "due": decision.due,
        "due_reason": decision.reason,
        "leased": dispatch.is_leased(sync, now),
        "live": window.status(now) if window else None,
        "last_attempt_at": _iso(sync.last_attempt_at if sync else None),
        "last_success_at": _iso(sync.last_success_at if sync else None),
        "content_changed_at": _iso(sync.content_changed_at if sync else None),
        "consecutive_failures": sync.consecutive_failures if sync else 0,
        "deferred_until": _iso(sync.deferred_until if sync else None),
        "last_error": sync.last_error if sync else "",
        "edition": sync.edition if sync else "",
        "entry_count": entry_count,
        "resolved_count": resolved_count,
        "resolve_rate": round(resolved_count / entry_count, 3) if entry_count else None,
        "unresolved": list(sync.unresolved) if sync else [],
    }


def report(now=None):
    """Return the full health report as JSON-safe data."""
    now = now or timezone.now()
    definitions = registry.all_definitions()
    syncs = {sync.key: sync for sync in FeaturedListSync.objects.all()}
    rows = [_row(definition, syncs.get(definition.key), now) for definition in definitions]
    keys = {definition.key for definition in definitions}
    orphans = [
        {"key": sync.key, "list_id": sync.custom_list_id, "last_success_at": _iso(sync.last_success_at)}
        for key, sync in sorted(syncs.items())
        if key not in keys
    ]
    return {
        "generated_at": now.isoformat(),
        "totals": {
            "definitions": len(rows),
            "healthy": sum(1 for row in rows if row["status"] in ("ok", "unchanged") and not row["stale"]),
            "failing": sum(1 for row in rows if row["consecutive_failures"]),
            "stale": sum(1 for row in rows if row["stale"]),
            "never_synced": sum(1 for row in rows if row["status"] == "never"),
            "deferred": sum(1 for row in rows if row["status"] == FeaturedListSync.Status.DEFERRED),
            "due": sum(1 for row in rows if row["due"]),
            "live": sum(1 for row in rows if row["live"] == "live"),
            "unresolved_entries": sum(row["entry_count"] - row["resolved_count"] for row in rows),
        },
        "problems": registry.problems(definitions),
        "quota": {
            provider: {
                "budget": budget,
                "used_today": quota.used_today(engine.QUOTA_SCOPE, provider, now),
                "blocked_until": _iso(quota.blocked_until(provider, now)),
            }
            for provider, budget in engine.provider_budgets().items()
        },
        "ratings_pending": ratings.pending_count(),
        "lists": rows,
        "orphans": orphans,
    }


def _age(value, now):
    if not value:
        return "never"
    seconds = int((now - value).total_seconds())
    if seconds < 3600:  # noqa: PLR2004
        return f"{seconds // 60}m ago"
    if seconds < 86400 * 2:  # noqa: PLR2004
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def format_text(data, *, unresolved_per_list=5):
    """Return the report as plain text for ``manage.py featured_lists_report``."""
    from django.utils.dateparse import parse_datetime  # noqa: PLC0415

    now = parse_datetime(data["generated_at"])
    totals = data["totals"]
    lines = [
        f"Featured lists at {data['generated_at']}: {totals['definitions']} defined, "
        f"{totals['healthy']} healthy, {totals['failing']} failing, {totals['stale']} stale, "
        f"{totals['never_synced']} never synced, {totals['due']} due, "
        f"{totals['unresolved_entries']} unresolved entries.",
    ]
    lines += [f"PROBLEM {problem}" for problem in data["problems"]]
    for row in data["lists"]:
        success = parse_datetime(row["last_success_at"]) if row["last_success_at"] else None
        flags = " ".join(
            flag
            for flag, on in (("STALE", row["stale"]), ("DUE", row["due"]), (f"LIVE:{row['live']}", row["live"]))
            if on
        )
        lines.append(
            f"{row['status']:<9} {row['key']:<48} {row['resolved_count']:>4}/{row['entry_count']:<4} "
            f"{_age(success, now):>9}  {flags}".rstrip(),
        )
        if row["last_error"]:
            lines.append(f"          error: {row['last_error']}")
        for missing in row["unresolved"][:unresolved_per_list]:
            lines.append(
                f"          unresolved #{missing.get('rank')}: {missing.get('title')} "
                f"({missing.get('year') or '?'}) [{missing.get('key')}] {missing.get('reason', '')}".rstrip(),
            )
    lines += [f"ORPHAN {orphan['key']} (list {orphan['list_id']})" for orphan in data["orphans"]]
    return "\n".join(lines)
