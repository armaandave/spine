"""The featured-list registry: every list Spine keeps current, defined in code.

Each module in ``lists/featured/definitions/`` exports ``DEFINITIONS``, a list of
``FeaturedList``. They are discovered automatically; add a module, don't edit this
one. ``problems()`` runs every check the registry tests enforce.
"""

import hashlib
import importlib
import json
import pkgutil
import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.models import PRIMARY_MEDIA_TYPES

# Bump to make every list re-sync once (for example after a writer change).
ENGINE_VERSION = "1"

KINDS = ("chart", "award", "canon", "community", "curator", "editorial")
LIST_TYPES = ("media", "people")
KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_KEY_LENGTH = 128
LIVE_LOOKAHEAD = timedelta(minutes=20)  # the dispatcher treats a window as live this early
LIVE_DISPLAY_MARGIN = timedelta(hours=24)  # "upcoming"/"done" on cards and in "Now"
MIN_LIVE_INTERVAL = timedelta(seconds=15)

# (first, last, section, media type the section requires or None)
POSITION_RANGES = (
    (1, 6, "headline", None),
    (100, 199, "film", "movie"),
    (200, 299, "tv", "tv"),
    (300, 399, "books", "book"),
    (400, 449, "comics", "comic"),
    (450, 499, "manga", "manga"),
    (500, 599, "games", "game"),
    (600, 649, "anime", "anime"),
    (700, 799, "music", "music"),
    (800, 899, "cross-media", None),
    (900, 10_000, "people", None),
)


class RegistryError(Exception):
    """The registry is inconsistent (duplicate keys or identities)."""


@dataclass(frozen=True)
class LiveWindow:
    """A period (UTC) when a list is polled every ``interval``: an award night."""

    starts_at: datetime
    ends_at: datetime
    interval: timedelta = timedelta(seconds=30)
    label: str = ""

    def status(self, now):
        """Return "upcoming", "live" or "done" near the window, else None."""
        if self.starts_at <= now <= self.ends_at:
            return "live"
        if self.starts_at - LIVE_DISPLAY_MARGIN <= now < self.starts_at:
            return "upcoming"
        if self.ends_at < now <= self.ends_at + LIVE_DISPLAY_MARGIN:
            return "done"
        return None

    def is_active(self, now):
        """Return whether the dispatcher should poll this window now."""
        return self.starts_at - LIVE_LOOKAHEAD <= now <= self.ends_at


def live_window(local_start, tz, duration, *, interval=timedelta(seconds=30), label=""):
    """Build a UTC ``LiveWindow`` from an award's local start time.

    ``live_window("2027-03-14 16:00", "America/Los_Angeles", timedelta(hours=4))``
    """
    start = datetime.fromisoformat(local_start).replace(tzinfo=ZoneInfo(tz))
    starts_at = start.astimezone(UTC)
    return LiveWindow(
        starts_at=starts_at,
        ends_at=starts_at + duration,
        interval=interval,
        label=label,
    )


@dataclass(frozen=True)
class Guards:
    """Writer safety checks; any failure leaves the existing list untouched."""

    require_exact_count: bool = False  # entries must equal FetchResult.expected_count
    min_resolve_rate: float = 0.5  # resolved / matchable entries
    max_drop: float = 0.5  # refuse a shrink larger than this fraction…
    drop_check_min_previous: int = 20  # …once the previous count is at least this
    max_title_searches: int = 50  # strict title searches per run (resolver budget)


@dataclass(frozen=True)
class FeaturedList:
    """One featured list Spine keeps current."""

    key: str  # stable forever, kebab-case, source-prefixed
    name: str
    media_types: tuple[str, ...]  # 1 type: its shelf; 2: both; 3+: "Across Media"
    kind: str  # chart | award | canon | community | curator | editorial
    position: int  # featured_position: order inside a shelf
    adapter: str
    params: Mapping[str, Any]
    cadence: timedelta | None  # None = static (re-sync when definition_hash changes)
    source_name: str = ""
    source_url: str = ""
    identity: tuple[str, str] = ("", "")  # (import_source, import_source_id)
    description: str = ""
    is_ranked: bool = True
    headline: bool = False  # included in the legacy flat endpoint (installed builds)
    featured: bool = True  # False = sync and stage without showing it
    live_windows: tuple[LiveWindow, ...] = ()
    guards: Guards = field(default_factory=Guards)
    soft_time_limit: int = 600
    list_type: str = "media"  # "people" writes PersonListItem rows
    tags: tuple[str, ...] = ()  # CustomList.tags; left alone when empty

    def __post_init__(self):
        """Default the identity to ("spine", key) and freeze sequence fields."""
        if not any(self.identity):
            object.__setattr__(self, "identity", ("spine", self.key))
        object.__setattr__(self, "media_types", tuple(self.media_types))
        object.__setattr__(self, "live_windows", tuple(self.live_windows))
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "params", dict(self.params or {}))

    @property
    def keeps_repeats(self):
        """Return whether a work gets a row per entry rather than one row in all.

        All-time award lists list a work once per win (Succession for 2023, 2022 and
        2020), each row with its own note. Every other list holds an item once: a live
        ceremony joins one film's categories into one row, and people lists can't repeat.
        """
        return self.kind == "award" and not self.live_windows and self.list_type == "media"

    def live_window_at(self, now):
        """Return the window nearest to ``now`` that cards should show, if any."""
        for window in self.live_windows:
            if window.status(now) == "live":
                return window
        nearby = [window for window in self.live_windows if window.status(now)]
        return min(nearby, key=lambda window: abs(window.starts_at - now), default=None)

    def active_live_window(self, now):
        """Return the window the dispatcher is polling now, if any."""
        return next((window for window in self.live_windows if window.is_active(now)), None)


def definition_hash(definition, adapter=None, overrides=()):
    """Return a hash of everything that should trigger a re-sync when it changes."""
    payload = asdict(definition)
    payload["engine_version"] = ENGINE_VERSION
    if definition.keeps_repeats:  # these lists re-synced once into a row per win
        payload["keeps_repeats"] = True
    if adapter is not None and adapter.version is not None:
        payload["adapter_version"] = adapter.version(definition)
    payload["overrides"] = sorted(overrides)
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def cadence_label(definition):
    """Return live, daily, weekly, monthly, yearly or static for the API."""
    cadence = definition.cadence
    if cadence is None:
        return "static"
    if cadence <= timedelta(hours=1):
        return "live"
    if cadence <= timedelta(days=1, hours=1):
        return "daily"
    if cadence <= timedelta(days=8):
        return "weekly"
    if cadence <= timedelta(days=32):
        return "monthly"
    return "yearly"


_definitions = None
_by_key = None
_by_identity = None


def _discover():
    from lists.featured import definitions as package  # noqa: PLC0415

    found = []
    for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{module_info.name}")
        found.extend(getattr(module, "DEFINITIONS", []))
    return found


def _index(definitions):
    by_key, by_identity = {}, {}
    for definition in definitions:
        if definition.key in by_key:
            msg = f"Duplicate featured-list key: {definition.key}"
            raise RegistryError(msg)
        if definition.identity in by_identity:
            msg = (
                f"Duplicate featured-list identity {definition.identity}: "
                f"{by_identity[definition.identity].key} and {definition.key}"
            )
            raise RegistryError(msg)
        by_key[definition.key] = definition
        by_identity[definition.identity] = definition
    return by_key, by_identity


def _load():
    global _definitions, _by_key, _by_identity  # noqa: PLW0603
    if _definitions is None:
        definitions = sorted(_discover(), key=lambda item: (item.position, item.key))
        _by_key, _by_identity = _index(definitions)
        _definitions = definitions
    return _definitions


def all_definitions():
    """Return every definition, ordered by position then key."""
    return list(_load())


def get(key):
    """Return the definition with this key, or None."""
    _load()
    return _by_key.get(key)


def by_identity(source, source_id):
    """Return the definition stored as ``(import_source, import_source_id)``, or None."""
    _load()
    return _by_identity.get((source, str(source_id)))


@contextmanager
def override_definitions(definitions):
    """Use ``definitions`` instead of the discovered registry (tests)."""
    global _definitions, _by_key, _by_identity  # noqa: PLW0603
    saved = (_definitions, _by_key, _by_identity)
    ordered = sorted(definitions, key=lambda item: (item.position, item.key))
    _by_key, _by_identity = _index(ordered)
    _definitions = ordered
    try:
        yield
    finally:
        _definitions, _by_key, _by_identity = saved


def section_for(position):
    """Return the position range a position falls in, or None."""
    for first, last, section, required in POSITION_RANGES:
        if first <= position <= last:
            return section, required
    return None


def _position_problems(definition):
    found = section_for(definition.position)
    if found is None:
        return [f"position {definition.position} is outside every range"]
    section, required = found
    problems = []
    if section == "headline" and not definition.headline:
        problems.append("positions 1-6 are reserved for headline lists")
    if definition.headline and section != "headline":
        problems.append("headline lists must use positions 1-6")
    if required and required not in definition.media_types:
        problems.append(f"position {definition.position} is in the {section} range")
    if section == "people" and definition.list_type != "people":
        problems.append("positions 900+ are for people lists")
    return problems


def _window_problems(definition):
    problems = []
    for window in definition.live_windows:
        if window.starts_at.tzinfo is None or window.ends_at.tzinfo is None:
            problems.append("live windows must be timezone-aware")
            continue
        if window.starts_at.utcoffset() != timedelta(0):
            problems.append("live windows must be stored in UTC")
        if window.ends_at <= window.starts_at:
            problems.append("a live window must end after it starts")
        if window.interval < MIN_LIVE_INTERVAL:
            problems.append("a live window interval must be at least 15 seconds")
    return problems


def _guard_problems(guards):
    problems = []
    if not 0 <= guards.min_resolve_rate <= 1:
        problems.append("min_resolve_rate must be between 0 and 1")
    if not 0 <= guards.max_drop <= 1:
        problems.append("max_drop must be between 0 and 1")
    if guards.drop_check_min_previous < 0 or guards.max_title_searches < 0:
        problems.append("guard counts must not be negative")
    return problems


def definition_problems(definition, adapters=None):
    """Return every problem with one definition (empty when it is valid)."""
    if adapters is None:
        from lists.featured.adapters import ADAPTERS as adapters  # noqa: PLC0415, N811

    problems = []
    if not KEY_PATTERN.match(definition.key) or len(definition.key) > MAX_KEY_LENGTH:
        problems.append("key must be kebab-case and at most 128 characters")
    if not definition.name.strip():
        problems.append("name is required")
    if not definition.media_types:
        problems.append("media_types is required")
    invalid_types = [value for value in definition.media_types if value not in PRIMARY_MEDIA_TYPES]
    if invalid_types:
        problems.append(f"unknown media types: {invalid_types}")
    if len(set(definition.media_types)) != len(definition.media_types):
        problems.append("media_types has duplicates")
    if definition.kind not in KINDS:
        problems.append(f"kind must be one of {KINDS}")
    if definition.list_type not in LIST_TYPES:
        problems.append(f"list_type must be one of {LIST_TYPES}")
    if len(definition.identity) != 2 or not all(definition.identity):  # noqa: PLR2004
        problems.append("identity must be two non-empty strings")
    if definition.cadence is not None and definition.cadence <= timedelta(0):
        problems.append("cadence must be positive")
    if not 10 <= definition.soft_time_limit <= 3600:  # noqa: PLR2004
        problems.append("soft_time_limit must be between 10 and 3600 seconds")
    problems += _position_problems(definition)
    problems += _window_problems(definition)
    problems += _guard_problems(definition.guards)
    adapter = adapters.get(definition.adapter)
    if adapter is None:
        problems.append(f"unknown adapter {definition.adapter!r}")
    elif adapter.validate is not None:
        try:
            adapter.validate(definition)
        except (ValueError, KeyError, TypeError, OSError) as error:
            problems.append(f"invalid params: {error}")
    return [f"{definition.key}: {problem}" for problem in problems]


def problems(definitions=None):
    """Return every registry problem: per-definition checks plus uniqueness."""
    from lists.featured import overrides  # noqa: PLC0415
    from lists.featured.adapters import ADAPTERS  # noqa: PLC0415

    definitions = _discover() if definitions is None else list(definitions)
    found = []
    try:
        _index(definitions)
    except RegistryError as error:
        found.append(str(error))
    for definition in definitions:
        found += definition_problems(definition, ADAPTERS)
    found += overrides.problems(
        list_keys={definition.key for definition in definitions},
        adapter_keys=set(ADAPTERS),
    )
    return found
