"""Source adapters, discovered automatically.

Each module here exports ``ADAPTER = Adapter(key=..., fetch=..., validate=...)``.
``ADAPTERS`` maps every adapter key to its ``Adapter``.
"""

import importlib
import pkgutil

from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

__all__ = ["ADAPTERS", "Adapter", "AdapterError", "Entry", "FetchResult", "get"]


def _discover():
    found = {}
    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        adapter = getattr(module, "ADAPTER", None)
        if adapter is None:
            continue
        if adapter.key in found:
            msg = f"Duplicate featured-list adapter key: {adapter.key}"
            raise RuntimeError(msg)
        found[adapter.key] = adapter
    return found


ADAPTERS = _discover()


def get(key):
    """Return the adapter for ``key`` or raise ``KeyError``."""
    return ADAPTERS[key]
