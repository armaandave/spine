"""Definitions for curated CSV lists, one per row of every ``static/manifest-*.csv``.

Manifest columns: ``key,name,media_types,kind,position,description,source_name,
source_url,is_ranked``. ``media_types`` is ``|``-separated. Each list's rows live in
``static/<key>.csv``. Positions 900+ are people lists.
"""

import csv
from pathlib import Path

from lists.featured.registry import FeaturedList

DIRECTORY = Path(__file__).resolve().parent.parent / "static"
COLUMNS = [
    "key",
    "name",
    "media_types",
    "kind",
    "position",
    "description",
    "source_name",
    "source_url",
    "is_ranked",
]
PEOPLE_POSITIONS_START = 900
TRUE_VALUES = {"1", "true", "yes", "y"}
FALSE_VALUES = {"0", "false", "no", "n", ""}


def _bool(value, where):
    normalized = (value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    msg = f"{where}: is_ranked must be true or false"
    raise ValueError(msg)


def definitions_from_manifest(path):
    """Return the definitions in one manifest file."""
    found = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            msg = f"{Path(path).name}: header must be {','.join(COLUMNS)}"
            raise ValueError(msg)
        for line, row in enumerate(reader, start=2):
            where = f"{Path(path).name}:{line}"
            key = (row["key"] or "").strip()
            try:
                position = int(row["position"])
            except (TypeError, ValueError):
                msg = f"{where}: position must be a whole number"
                raise ValueError(msg) from None
            found.append(
                FeaturedList(
                    key=key,
                    name=(row["name"] or "").strip(),
                    media_types=tuple(
                        value.strip() for value in (row["media_types"] or "").split("|") if value.strip()
                    ),
                    kind=(row["kind"] or "").strip(),
                    position=position,
                    adapter="static",
                    params={"file": f"{key}.csv"},
                    cadence=None,
                    source_name=(row["source_name"] or "").strip(),
                    source_url=(row["source_url"] or "").strip(),
                    description=(row["description"] or "").strip(),
                    is_ranked=_bool(row["is_ranked"], where),
                    list_type="people" if position >= PEOPLE_POSITIONS_START else "media",
                ),
            )
    return found


def load(directory=DIRECTORY):
    """Return the definitions from every manifest in ``directory``."""
    found = []
    for path in sorted(Path(directory).glob("manifest-*.csv")):
        found.extend(definitions_from_manifest(path))
    return found


DEFINITIONS = load()
