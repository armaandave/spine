"""Split the Django test suite into N parts of about the same size, for CI's Database
checks, which run the parts on separate machines at the same time (.github/workflows/ci.yml).

The parts are whole test modules: every file Django's test discovery would load from src/
(test*.py inside packages), each in exactly one part, so together they are the full suite.
Each module weighs its number of test functions; the heaviest go first, each to the part
with the least so far. The same files always give the same split.

Usage, from src/: python ../scripts/test_shards.py <part> <parts>   (part counts from 1)
Prints that part's module labels, for `manage.py test`.
"""

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
TEST_FUNCTION = re.compile(r"^\s*(?:async\s+)?def test", re.MULTILINE)
SKIP_DIRS = {".venv", "node_modules", "__pycache__", "static", "staticfiles"}


def test_modules(src=SRC):
    """Every module Django's default discovery loads: test*.py in a package under src."""
    modules = []
    for path in sorted(src.rglob("test*.py")):
        relative = path.relative_to(src)
        if SKIP_DIRS.intersection(relative.parts):
            continue
        package = path.parent
        while package != src and (package / "__init__.py").exists():
            package = package.parent
        if package != src:
            continue  # inside a folder discovery doesn't import
        modules.append(".".join(relative.with_suffix("").parts))
    return modules


def split(parts, src=SRC):
    """The suite's modules in `parts` lists of about equal test counts."""
    weights = {
        module: len(TEST_FUNCTION.findall((src / f"{module.replace('.', '/')}.py").read_text()))
        for module in test_modules(src)
    }
    bins = [[] for _ in range(parts)]
    totals = [0] * parts
    for module in sorted(weights, key=lambda m: (-weights[m], m)):
        lightest = totals.index(min(totals))
        bins[lightest].append(module)
        totals[lightest] += weights[module]
    return [sorted(modules) for modules in bins]


def main(argv):
    if len(argv) != 3 or not argv[1].isdigit() or not argv[2].isdigit():
        sys.exit(__doc__)
    part, parts = int(argv[1]), int(argv[2])
    if not 1 <= part <= parts:
        sys.exit(f"part must be 1 to {parts}")
    print(" ".join(split(parts)[part - 1]))


if __name__ == "__main__":
    main(sys.argv)
