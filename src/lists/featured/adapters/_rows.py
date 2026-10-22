"""Row-count checks for fixed-size charts (private to the chart adapters).

The writer's drop guard only judges lists that held at least
``Guards.drop_check_min_previous`` (20) entries, so a partial or lazily loaded source
could otherwise replace a 10-row chart with a single row. Chart definitions set
``params["min_entries"]`` to the rows a complete chart has (or a sensible floor when
the source filters rows); an adapter that gets fewer raises ``AdapterError``, so the
list stays as it was and the dispatcher retries later. Each adapter's docstring says
what it counts.
"""

from lists.featured.types import AdapterError

PARAM = "min_entries"


def validate(definition, limit=None):
    """Raise ``ValueError`` unless ``min_entries`` is a positive integer within ``limit``."""
    value = definition.params.get(PARAM, 1)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        msg = f"{PARAM} must be a positive integer"
        raise ValueError(msg)
    if limit is not None and value > int(limit):
        msg = f"{PARAM} ({value}) must not exceed the limit ({limit})"
        raise ValueError(msg)


def require(definition, count, what):
    """Raise ``AdapterError`` when ``count`` rows fall short of ``min_entries``."""
    minimum = definition.params.get(PARAM, 1)
    if count < minimum:
        msg = (
            f"{what} has {count} rows, fewer than the {minimum} a complete chart has; "
            "the source may be partial, so the list was left as it is."
        )
        raise AdapterError(msg)
