"""Manual match fixes, loaded from every ``overrides/*.csv`` in this directory.

Columns: ``scope,entry_key,ref,note``.

- ``scope``: a list key (``letterboxd-top-500``), an adapter key (``nyt``) or ``*``.
  The most specific scope wins.
- ``entry_key``: an ``Entry.key`` exactly as the health report or
  ``manage.py sync_featured_list <key> --dry-run`` prints it.
- ``ref``: ``<source>:<media_type>:<media_id>`` (see ``resolve.parse_ref``), or ``-``
  to skip the entry entirely.
"""

import csv
from functools import cache
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
COLUMNS = ["scope", "entry_key", "ref", "note"]
SKIP = "-"


@cache
def _load(directory=DIRECTORY):
    rows = []
    for path in sorted(Path(directory).glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != COLUMNS:
                rows.append({"file": path.name, "error": f"header must be {','.join(COLUMNS)}"})
                continue
            for line, row in enumerate(reader, start=2):
                rows.append(
                    {
                        "file": path.name,
                        "line": line,
                        "scope": (row["scope"] or "").strip(),
                        "entry_key": (row["entry_key"] or "").strip(),
                        "ref": (row["ref"] or "").strip(),
                        "note": (row["note"] or "").strip(),
                    },
                )
    return tuple(rows)


def rows():
    """Return every parsed override row."""
    return [row for row in _load() if "error" not in row]


def table():
    """Return ``{(scope, entry_key): ref}``."""
    return {(row["scope"], row["entry_key"]): row["ref"] for row in rows()}


def for_definition(definition):
    """Return ``{entry_key: ref}`` for one definition, most specific scope last."""
    found = {}
    for scope in ("*", definition.adapter, definition.key):
        for (row_scope, entry_key), ref in table().items():
            if row_scope == scope:
                found[entry_key] = ref
    return found


def problems(*, list_keys, adapter_keys):
    """Return every invalid or conflicting override row."""
    from lists.featured.resolve import parse_ref  # noqa: PLC0415

    found = []
    seen = {}
    for row in _load():
        where = f"overrides/{row['file']}" + (f":{row['line']}" if "line" in row else "")
        if "error" in row:
            found.append(f"{where}: {row['error']}")
            continue
        if row["scope"] != "*" and row["scope"] not in list_keys | adapter_keys:
            found.append(f"{where}: unknown scope {row['scope']!r}")
        if not row["entry_key"]:
            found.append(f"{where}: entry_key is required")
        if row["ref"] != SKIP:
            try:
                parse_ref(row["ref"])
            except ValueError as error:
                found.append(f"{where}: {error}")
        identity = (row["scope"], row["entry_key"])
        if identity in seen and seen[identity] != row["ref"]:
            found.append(f"{where}: conflicts with an earlier override for {identity}")
        seen[identity] = row["ref"]
    return found


def clear_cache():
    """Forget loaded CSVs (tests that write temporary override files)."""
    _load.cache_clear()
