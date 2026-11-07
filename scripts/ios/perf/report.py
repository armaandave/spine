#!/usr/bin/env python3
"""Summarise perf-harness results as markdown tables.

    report.py <result dir>                 one run (xcodebuild.log + probe.txt written by perf-harness.sh test)
    report.py --ab <session dir> [--json]  an A/B session written by scripts/ios/perf-ab.sh

Per measured window of each HomePerfTests scenario:
  - duration: XCTClockMetric's value (xcodebuild log);
  - hitch ms per s, hitches, longest frame: the app's PerfProbe frame sampler (CADisplayLink; a frame later
    than 1.5x the display's frame time is a hitch, its overshoot is the hitch time);
  - CPU %: the app's CPU time over the window / the window (one core = 100%);
  - peak / end footprint: the app's phys_footprint at the window's samples;
  - counters: for every PerfCounter that changed inside the window (probe `C` lines), how much it grew.
Each row is median / p90 (linear interpolation) / min / max over the iterations.

--ab: the session directory holds leg directories named A-r1-<stamp>, B-r1-<stamp>, A-r2-<stamp>, ... (A is the
frozen baseline app, B the app under test), each a normal result directory. For every scenario and metric the table
shows the per-leg medians, the A median (median of A's leg medians), the A spread (max minus min of A's leg medians),
the B median, delta = B - A, the tolerance and a verdict:
  FAIL   delta is larger than max(tolerance, A spread)
  NOISY  not FAIL, but A's own spread is larger than the tolerance (the comparison cannot tell)
  PASS   otherwise
  info   a metric with no tolerance (counters, hitch count): listed only
  n/a    no A or no B leg has the scenario (a scenario the baseline does not have)
Lower is better for every metric, so only an increase fails.

CAPS are absolute sanity limits from the plan's perf guardrails: B's median must not exceed them, whatever A did. A
metric with a cap and no A leg (a scenario the baseline does not have) is judged by the cap alone. WORST_FRAME caps the
single longest frame of any B iteration. COUNTER_RULES are the deterministic counter assertions, listed after the table.
"""
import json
import math
import re
import sys
from pathlib import Path

SCENARIOS = {
    "testA_ColdLaunchToFirstCard": "a) Cold launch to first feed card",
    "testB_FeedScroll": "b) Feed scroll (3 fast + 3 default swipes)",
    "testB2_FeedScrollColdImages": "b2) Feed scroll, cold image caches (fresh process)",
    "testC1_OpenPost": "c1) Open post page from a card",
    "testC2_ClosePost": "c2) Back from post page",
    "testD_OpenUpdateSheet": "d) Open In progress update sheet",
    "testE_MemoryAfterScrollingFeed": "e) Memory: fresh launch + 10 fast swipes",
    "testF_TileExpandSwipeCollapse": "f) Tile: expand, swipe, collapse",
    "testF2_ExpandThenFastScroll": "f2) Tile: expand, then fast scroll",
    "testG_IdleAfterTiles": "g) Idle after tiles",
    "testG2_IdleWithOpenTile": "g2) Idle with an open tile",
}

# Short names used by the tolerance tables.
KEYS = {
    "testA_ColdLaunchToFirstCard": "a",
    "testB_FeedScroll": "b",
    "testB2_FeedScrollColdImages": "b2",
    "testC1_OpenPost": "c1",
    "testC2_ClosePost": "c2",
    "testD_OpenUpdateSheet": "d",
    "testE_MemoryAfterScrollingFeed": "e",
    "testF_TileExpandSwipeCollapse": "f",
    "testF2_ExpandThenFastScroll": "f2",
    "testG_IdleAfterTiles": "g",
    "testG2_IdleWithOpenTile": "g2",
}

# --ab tolerances: the most B may exceed A before a leg pair fails. A scenario or metric missing here has none (info).
TOLERANCES = {
    "hitch_time": {"b": 4, "b2": 4, "e": 4, "c1": 15, "c2": 15, "d": 25},  # ms per s
    "longest": {"b": 25, "b2": 25, "e": 25, "c1": 30, "c2": 30, "d": 30},  # ms
    "cpu": {"default": 3},  # points of one core
    "peak": {"default": 20},  # MB
    "end": {"default": 20},  # MB
}
DURATION_PERCENT = {"default": 10, "a": 15}  # of A's median

# Absolute caps on B's median (metric ids as in METRICS), from the plan's perf guardrails.
CAPS = {
    "a": {"duration": 5500, "end": 88},  # ms; MB of footprint at the first card
    "b": {"hitch_time": 26, "hitches": 18, "longest": 80, "cpu": 18, "peak": 112},
    "b2": {"longest": 120},
    "c1": {"duration": 2000, "hitch_time": 110, "longest": 80},
    "c2": {"duration": 3800, "hitch_time": 75, "longest": 160},
    "d": {"duration": 2900, "hitch_time": 215, "longest": 215},
    "e": {"hitch_time": 26, "peak": 115, "end": 110},
    "f": {"hitch_time": 100, "longest": 100, "cpu": 35},
    "f2": {"longest": 150},
    "g": {"cpu": 3},
    "g2": {"cpu": 3},
}
# No single frame of any B iteration may be longer than this (ms).
WORST_FRAME = {"b2": 150, "f": 150}

# id, label, unit, digits
METRICS = [
    ("duration", "duration", "ms", 0),
    ("hitch_time", "hitch time", "ms per s", 1),
    ("hitches", "hitches", "count", 0),
    ("longest", "longest frame", "ms", 0),
    ("cpu", "CPU", "% of one core", 0),
    ("peak", "peak footprint", "MB", 0),
    ("end", "footprint at end", "MB", 0),
]


def percentile(values, q):
    values = sorted(values)
    if not values:
        return float("nan")
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def median(values):
    return percentile(values, 0.5)


def isnum(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def row(label, values, unit, digits=1):
    if not values:
        return f"| {label} | n/a | n/a | n/a | n/a | 0 |"
    f = f"{{:.{digits}f}}"
    cells = [f.format(percentile(values, 0.5)), f.format(percentile(values, 0.9)), f.format(min(values)), f.format(max(values))]
    return f"| {label} ({unit}) | " + " | ".join(cells) + f" | {len(values)} |"


# ---------------------------------------------------------------- reading one result directory

def load_probe(d):
    """(S samples, C counter lines), both sorted by time. Unknown line types and torn lines are ignored."""
    samples, counters = [], []
    for line in (d / "probe.txt").read_text(errors="replace").splitlines():
        p = line.split()
        try:
            if len(p) == 9 and p[0] == "S":
                samples.append((float(p[2]), int(p[1]), int(p[3]), int(p[4]), float(p[5]), float(p[6]), int(p[7]), int(p[8])))
            elif len(p) == 5 and p[0] == "C":
                counters.append((float(p[2]), int(p[1]), p[3], int(p[4])))
        except ValueError:
            continue
    samples.sort()
    counters.sort()
    return samples, counters


def load_log(d):
    """({test: [(start|stop, epoch)]}, {test: [clock seconds per iteration]})"""
    log = (d / "xcodebuild.log").read_text(errors="replace")
    marks = {}
    seen = set()
    for m in re.finditer(r"PERFMARK -\[\S+ (\w+)\] (start|stop) ([\d.]+)", log):
        key = m.groups()
        if key in seen:
            continue
        seen.add(key)
        marks.setdefault(m.group(1), []).append((m.group(2), float(m.group(3))))
    clocks = {}
    for m in re.finditer(r"Test Case '-\[\S+ (\w+)\]' measured \[Clock Monotonic Time, s\].*?values: \[([^\]]*)\]", log):
        clocks[m.group(1)] = [float(x) for x in m.group(2).split(",") if x.strip()]
    return marks, clocks


def counter_deltas(counters, pid, a, b):
    """How much each counter grew inside the window (a, b + 0.1] in process `pid`. Counters are cumulative per process
    and only written when they change, so the value before the window is the last line at or before `a` (else 0)."""
    before, last = {}, {}
    for epoch, p, name, value in counters:
        if p != pid:
            continue
        if epoch <= a:
            before[name] = value
        elif epoch <= b + 0.1:
            last[name] = value
    return {name: value - before.get(name, 0) for name, value in last.items() if value != before.get(name, 0)}


def measure(d):
    """{test: {"metrics": {id: [value per iteration]}, "counters": {name: [delta per iteration]}}} for one result dir."""
    d = Path(d)
    probe, counters = load_probe(d)
    marks, clocks = load_log(d)
    result = {}
    for test in SCENARIOS:
        if test not in marks and test not in clocks:
            continue
        windows = []
        start = None
        for kind, t in marks.get(test, []):
            if kind == "start":
                start = t
            elif start is not None:
                windows.append((start, t))
                start = None
        # XCTest runs a measure block PERF_ITERATIONS + 1 times and reports the last PERF_ITERATIONS: the first run is
        # a warm-up. Its probe window is dropped too.
        windows = windows[-len(clocks[test]):] if test in clocks else windows
        durations = clocks[test] if test in clocks else [b - a for a, b in windows]
        m = {name: [] for name, _, _, _ in METRICS}
        m["duration"] = [x * 1000 for x in durations]
        per_window = []
        for a, b in windows:
            samples = [s for s in probe if a < s[0] <= b + 0.1]
            if not samples:
                continue
            dur = max(b - a, 0.001)
            before = [s for s in probe if s[0] <= a and s[1] == samples[0][1]]
            cpu0 = before[-1][7] if before else samples[0][7]
            m["hitch_time"].append(sum(s[4] for s in samples) / dur)
            m["hitches"].append(sum(s[3] for s in samples))
            m["longest"].append(max(s[5] for s in samples))
            m["cpu"].append((samples[-1][7] - cpu0) / (dur * 1000) * 100)
            m["peak"].append(max(s[6] for s in samples) / 1024)
            m["end"].append(samples[-1][6] / 1024)
            per_window.append(counter_deltas(counters, samples[0][1], a, b))
        names = sorted({n for w in per_window for n in w})
        result[test] = {
            "metrics": m,
            "counters": {n: [w.get(n, 0) for w in per_window] for n in names},
        }
    return result


# ---------------------------------------------------------------- one run

def main(directory):
    d = Path(directory)
    results = measure(d)
    out = [f"# Perf results: {d.name}", ""]
    out.append("Simulator frame numbers come from the app's own CADisplayLink sampler (XCTHitchMetric and the other")
    out.append("XCTMetrics are rejected by the simulator). Compare runs from the same machine state only.")
    load = d / "load.txt"
    if load.exists():
        out.append("Machine load (uptime, before / after the run): " + " / ".join(
            x.strip().split("load averages:")[-1].strip() for x in load.read_text().splitlines() if x.strip()))
    out.append("")
    out.append("| Scenario / metric | median | p90 | min | max | n |")
    out.append("|---|---|---|---|---|---|")
    for test, title in SCENARIOS.items():
        if test not in results:
            continue
        out.append(f"| **{title}** | | | | | |")
        for metric, label, unit, digits in METRICS:
            out.append(row(label, results[test]["metrics"][metric], unit, digits))
        for name, values in results[test]["counters"].items():
            out.append(row(f"counter {name}", values, "count per window", 0))
    print("\n".join(out))


# ---------------------------------------------------------------- A/B session

def find_legs(session):
    """{"A": [(round, dir)], "B": [...]}: finished legs only (a harness failure leaves a FAILED file)."""
    legs = {"A": [], "B": []}
    for child in sorted(Path(session).iterdir()):
        m = re.match(r"([AB])-r(\d+)-", child.name)
        if not m or not child.is_dir():
            continue
        if (child / "FAILED").exists() or not (child / "xcodebuild.log").exists() or not (child / "probe.txt").exists():
            continue
        legs[m.group(1)].append((int(m.group(2)), child))
    for side in legs:
        legs[side].sort(key=lambda x: (x[0], x[1].name))
    return legs


def tolerance_for(metric, key, a_median):
    if metric == "duration":
        percent = DURATION_PERCENT.get(key, DURATION_PERCENT["default"])
        return a_median * percent / 100 if isnum(a_median) else None
    table = TOLERANCES.get(metric, {})
    return table.get(key, table.get("default"))


def verdict_for(tolerance, a_median, a_spread, b_median):
    if not isnum(a_median) or not isnum(b_median):
        return "n/a"
    if tolerance is None:
        return "info"
    delta = b_median - a_median
    if delta > max(tolerance, a_spread):
        return "FAIL"
    if a_spread > tolerance:
        return "NOISY"
    return "PASS"


def combine_verdict(ab_verdict, cap, b_median):
    """The A/B verdict plus the absolute cap: over the cap fails, and a cap alone judges a scenario with no A leg."""
    over = cap is not None and isnum(b_median) and b_median > cap
    if over:
        return "FAIL"
    if cap is not None and isnum(b_median) and ab_verdict in ("n/a", "info"):
        return "PASS"
    return ab_verdict


def windows_of(result, name):
    """One counter's delta per measured window of one leg's scenario (0 for a window where it never changed)."""
    return result["counters"].get(name, [0] * len(result["metrics"]["hitch_time"]))


def counter_checks(key, b_results):
    """The plan's counter assertions for scenario `key` over every B window: [(assertion, observed, "PASS"|"FAIL"|"n/a")]."""
    if not b_results or not any(r["metrics"]["hitch_time"] for r in b_results):
        return []

    def values(name):
        return [v for r in b_results for v in windows_of(r, name)]

    def zero(name):
        vals = values(name)
        return (f"{name} delta is 0", f"max {max(vals)} over {len(vals)} windows", "PASS" if max(vals) == 0 else "FAIL")

    checks = []
    if key in ("b", "b2"):
        checks += [zero("homeBody"), zero("tilesBody")]
        body, appear = values("feedCardBody"), values("feedCardAppear")
        ratios = [f"{b}/{a}" for a, b in zip(appear, body)]
        ok = all(b <= 1.5 * a for a, b in zip(appear, body))
        checks.append(("feedCardBody <= 1.5 x feedCardAppear", "per window: " + " ".join(ratios), "PASS" if ok else "FAIL"))
    elif key == "f":
        checks += [zero("homeBody"), zero("feedCardBody")]
    elif key == "f2":
        checks += [zero("homeBody")]
    elif key in ("g", "g2"):
        for name in ("homeBody", "feedCardBody", "feedCardAppear", "tilesBody", "glowBody"):
            vals = values(name)
            checks.append((f"{name} changes by at most 2", f"max {max(vals)} over {len(vals)} windows",
                           "PASS" if max(vals) <= 2 else "FAIL"))
    return checks


def compare(session):
    """The A/B comparison as plain data (the table and --json both print it)."""
    legs = find_legs(session)
    measured = {side: [(n, dir_.name, measure(dir_)) for n, dir_ in legs[side]] for side in legs}
    scenarios = []
    for test, title in SCENARIOS.items():
        sides = {side: [(name, r[test]) for _, name, r in measured[side] if test in r] for side in measured}
        if not sides["A"] and not sides["B"]:
            continue
        key = KEYS[test]
        entries = []
        names = set()
        for side in sides:
            for _, r in sides[side]:
                names.update(r["counters"])
        rows = [(m, label, unit, digits, False) for m, label, unit, digits in METRICS]
        rows += [("counter:" + n, "counter " + n, "count per window", 0, True) for n in sorted(names)]
        for metric, label, unit, digits, is_counter in rows:
            def leg_medians(side):
                values = []
                for _, r in sides[side]:
                    series = r["counters"].get(metric[8:], [0]) if is_counter else r["metrics"][metric]
                    if series:
                        values.append(median(series))
                return values
            a_legs, b_legs = leg_medians("A"), leg_medians("B")
            a_median = median(a_legs) if a_legs else float("nan")
            b_median = median(b_legs) if b_legs else float("nan")
            a_spread = (max(a_legs) - min(a_legs)) if a_legs else float("nan")
            tol = None if is_counter else tolerance_for(metric, key, a_median)
            cap = None if is_counter else CAPS.get(key, {}).get(metric)
            ab_verdict = "info" if is_counter and a_legs and b_legs else verdict_for(
                tol, a_median, a_spread if isnum(a_spread) else 0.0, b_median)
            verdict = combine_verdict(ab_verdict, cap, b_median)
            entries.append({
                "metric": metric, "label": label, "unit": unit, "digits": digits,
                "a_legs": a_legs, "b_legs": b_legs, "a_median": a_median, "a_spread": a_spread,
                "b_median": b_median,
                "delta": b_median - a_median if isnum(a_median) and isnum(b_median) else float("nan"),
                "tolerance": tol, "cap": cap, "ab_verdict": ab_verdict, "verdict": verdict,
            })
        b_results = [r for _, r in sides["B"]]
        worst_cap = WORST_FRAME.get(key)
        if worst_cap is not None and b_results:
            worst = max((x for r in b_results for x in r["metrics"]["longest"]), default=float("nan"))
            if isnum(worst):
                entries.append({
                    "metric": "longest_worst", "label": "longest frame, worst iteration", "unit": "ms", "digits": 0,
                    "a_legs": [], "b_legs": [], "a_median": float("nan"), "a_spread": float("nan"), "b_median": worst,
                    "delta": float("nan"), "tolerance": None, "cap": worst_cap, "ab_verdict": "n/a",
                    "verdict": "PASS" if worst <= worst_cap else "FAIL",
                })
        checks = [{"assertion": a, "observed": o, "verdict": v} for a, o, v in counter_checks(key, b_results)]
        scenarios.append({"test": test, "key": key, "title": title, "metrics": entries, "counter_checks": checks})
    verdicts = [e["verdict"] for s in scenarios for e in s["metrics"]] + [c["verdict"] for s in scenarios for c in s["counter_checks"]]
    overall = "FAIL" if "FAIL" in verdicts else "NOISY" if "NOISY" in verdicts else "PASS" if "PASS" in verdicts else "n/a"
    return {
        "session": str(session),
        "legs": {side: [dir_.name for _, dir_ in legs[side]] for side in legs},
        "overall": overall,
        "counts": {v: verdicts.count(v) for v in ("PASS", "FAIL", "NOISY", "info", "n/a")},
        "scenarios": scenarios,
    }


def clean(x):
    """JSON has no NaN."""
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return x


def cell(x, digits):
    return f"{x:.{digits}f}" if isnum(x) else "n/a"


def ab(session, as_json):
    result = compare(session)
    if as_json:
        print(json.dumps(clean(result), indent=2))
        return
    out = [f"# Perf A/B: {Path(session).name}", ""]
    out.append("A = frozen baseline app, B = this branch's app, measured in alternating legs in one session.")
    out.append("A legs: " + (", ".join(result["legs"]["A"]) or "none"))
    out.append("B legs: " + (", ".join(result["legs"]["B"]) or "none"))
    out.append("Lower is better. FAIL: delta (B - A) > max(tolerance, A spread), or B's median over the cap. NOISY: A's spread")
    out.append("alone exceeds the tolerance. A scenario with no A legs is judged by its cap alone. * = A's median is over the cap too.")
    out.append("")
    out.append("| Scenario / metric | A legs | A median | A spread | B legs | B median | delta | tolerance | cap | verdict |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in result["scenarios"]:
        out.append(f"| **{s['title']}** | | | | | | | | | |")
        for e in s["metrics"]:
            d = e["digits"]
            a_legs = " / ".join(cell(x, d) for x in e["a_legs"]) or "n/a"
            b_legs = " / ".join(cell(x, d) for x in e["b_legs"]) or "n/a"
            tol = "-" if e["tolerance"] is None else cell(e["tolerance"], max(d, 0))
            cap = "-" if e["cap"] is None else cell(e["cap"], max(d, 0))
            if e["cap"] is not None and isnum(e["a_median"]) and e["a_median"] > e["cap"]:
                cap += " *"
            out.append(
                f"| {e['label']} ({e['unit']}) | {a_legs} | {cell(e['a_median'], d)} | {cell(e['a_spread'], d)} | "
                f"{b_legs} | {cell(e['b_median'], d)} | {cell(e['delta'], d)} | {tol} | {cap} | {e['verdict']} |"
            )
    if any(s["counter_checks"] for s in result["scenarios"]):
        out.append("")
        out.append("Counter assertions (B windows, deterministic):")
        out.append("")
        out.append("| Scenario | Assertion | Observed | Verdict |")
        out.append("|---|---|---|---|")
        for s in result["scenarios"]:
            for chk in s["counter_checks"]:
                out.append(f"| {s['title']} | {chk['assertion']} | {chk['observed']} | {chk['verdict']} |")
    c = result["counts"]
    out.append("")
    out.append(f"Overall: **{result['overall']}** ({c['PASS']} pass, {c['FAIL']} fail, {c['NOISY']} noisy, "
               f"{c['info']} listed only, {c['n/a']} without both sides)")
    print("\n".join(out))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = len(args) != len(sys.argv) - 1
    if len(args) == 1 and not args[0].startswith("-") and not as_json:
        main(args[0])
    elif len(args) == 2 and args[0] == "--ab":
        ab(args[1], as_json)
    else:
        sys.exit(__doc__)
