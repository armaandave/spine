#!/usr/bin/env python3
"""Summarise a perf-harness result directory as a markdown table.

    report.py <result dir>        (xcodebuild.log + probe.txt written by perf-harness.sh test)

Per measured window of each HomePerfTests scenario:
  - duration: XCTClockMetric's value (xcodebuild log);
  - hitch ms per s, hitches, longest frame: the app's PerfProbe frame sampler (CADisplayLink; a frame later
    than 1.5x the display's frame time is a hitch, its overshoot is the hitch time);
  - CPU %: the app's CPU time over the window / the window (one core = 100%);
  - peak / end footprint: the app's phys_footprint at the window's samples.
Each row is median / p90 (linear interpolation) / min / max over the iterations.
"""
import re
import sys
from pathlib import Path

SCENARIOS = {
    "testA_ColdLaunchToFirstCard": "a) Cold launch to first feed card",
    "testB_FeedScroll": "b) Feed scroll (3 fast + 3 default swipes)",
    "testC1_OpenPost": "c1) Open post page from a card",
    "testC2_ClosePost": "c2) Back from post page",
    "testD_OpenUpdateSheet": "d) Open In progress update sheet",
    "testE_MemoryAfterScrollingFeed": "e) Memory: fresh launch + 10 fast swipes",
}


def percentile(values, q):
    values = sorted(values)
    if not values:
        return float("nan")
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def row(label, values, unit, digits=1):
    if not values:
        return f"| {label} | n/a | n/a | n/a | n/a | 0 |"
    f = f"{{:.{digits}f}}"
    cells = [f.format(percentile(values, 0.5)), f.format(percentile(values, 0.9)), f.format(min(values)), f.format(max(values))]
    return f"| {label} ({unit}) | " + " | ".join(cells) + f" | {len(values)} |"


def main(directory):
    d = Path(directory)
    log = (d / "xcodebuild.log").read_text(errors="replace")
    probe = []
    for line in (d / "probe.txt").read_text(errors="replace").splitlines():
        p = line.split()
        if len(p) == 9 and p[0] == "S":
            probe.append((float(p[2]), int(p[1]), int(p[3]), int(p[4]), float(p[5]), float(p[6]), int(p[7]), int(p[8])))
    probe.sort()

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
        if test not in marks and test not in clocks:
            continue
        out.append(f"| **{title}** | | | | | |")
        windows = []
        events = marks.get(test, [])
        start = None
        for kind, t in events:
            if kind == "start":
                start = t
            elif start is not None:
                windows.append((start, t))
                start = None
        # XCTest runs a measure block PERF_ITERATIONS + 1 times and reports the last PERF_ITERATIONS: the first run is
        # a warm-up. Its probe window is dropped too.
        windows = windows[-len(clocks[test]):] if test in clocks else windows
        durations = clocks[test] if test in clocks else [b - a for a, b in windows]
        out.append(row("duration", [x * 1000 for x in durations], "ms", 0))
        hitch_ratio, hitches, longest, cpu, peak, end = [], [], [], [], [], []
        for a, b in windows:
            samples = [s for s in probe if a < s[0] <= b + 0.1]
            if not samples:
                continue
            dur = max(b - a, 0.001)
            before = [s for s in probe if s[0] <= a and s[1] == samples[0][1]]
            cpu0 = before[-1][7] if before else samples[0][7]
            hitch_ratio.append(sum(s[4] for s in samples) / dur)
            hitches.append(sum(s[3] for s in samples))
            longest.append(max(s[5] for s in samples))
            cpu.append((samples[-1][7] - cpu0) / (dur * 1000) * 100)
            peak.append(max(s[6] for s in samples) / 1024)
            end.append(samples[-1][6] / 1024)
        out.append(row("hitch time", hitch_ratio, "ms per s", 1))
        out.append(row("hitches", hitches, "count", 0))
        out.append(row("longest frame", longest, "ms", 0))
        out.append(row("CPU", cpu, "% of one core", 0))
        out.append(row("peak footprint", peak, "MB", 0))
        out.append(row("footprint at end", end, "MB", 0))
    print("\n".join(out))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
