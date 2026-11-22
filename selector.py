"""Choose and schedule SQLGlot tests, emitting one JSON object per patch."""

from __future__ import annotations

import argparse
import json
import math

from pathlib import Path

from impact import Impact
from patches import UnsupportedPatch, read_patch

HERE = Path(__file__).resolve().parent


def schedule(numbers, durations):
    if not numbers:
        return []
    ordered = sorted(numbers, key=lambda n: (-durations[n], n))
    total = sum(durations[n] for n in ordered)
    longest = durations[ordered[0]]
    best_cost = total + 2000
    best = [ordered]
    for count in range(2, min(8, len(ordered)) + 1):
        lower_bound = max(longest, (total + count - 1) // count) + 2000 * count
        if lower_bound >= best_cost:
            continue
        loads = [0] * count
        workers = [[] for _ in loads]
        for number in ordered:
            i = loads.index(min(loads))
            loads[i] += durations[number]
            workers[i].append(number)
        cost = max(loads) + 2000 * sum(bool(worker) for worker in workers)
        if cost < best_cost:
            best_cost, best = cost, [w for w in workers if w]
    return best


def fit_output(entries, limit=2_000_000):
    """Keep JSON Lines valid even in an unusually large batch of full-suite fallbacks."""
    rows = [
        json.dumps({"commit": name, "workers": workers}, separators=(",", ":"))
        for name, workers in entries
    ]
    size = sum(len(row) + 1 for row in rows)  # json.dumps defaults to ASCII
    if size > limit:
        for i in sorted(range(len(rows)), key=lambda j: len(rows[j]), reverse=True):
            empty = json.dumps(
                {"commit": entries[i][0], "workers": []}, separators=(",", ":")
            )
            size -= len(rows[i]) - len(empty)
            rows[i] = empty
            if size <= limit:
                break
    return rows


def run(data_dir, index_path, source_root):
    tests = json.loads((data_dir / "tests.json").read_bytes())["tests"]
    durations = {t["number"]: math.floor(t["seconds"] * 1000 + 0.5) for t in tests}
    impact = Impact(index_path, source_root.resolve(), tests)
    schedules = {}
    entries = []
    for patch in sorted((data_dir / "commits").glob("*.patch")):
        try:
            changes = read_patch(patch.read_bytes().decode("utf-8"))
            mask = 0
            for change in changes:
                mask |= impact.select_file(change)
        except (
            UnsupportedPatch,
            OSError,
            UnicodeError,
            SyntaxError,
            ValueError,
            RecursionError,
        ):
            mask = impact.all
        if mask not in schedules:
            schedules[mask] = schedule(impact.numbers(mask), durations)
        entries.append((patch.stem, schedules[mask]))
    for row in fit_output(entries):
        print(row)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--index", type=Path, default=HERE / "coverage.json")
    parser.add_argument("--source", type=Path, default=Path("/src"))
    args = parser.parse_args()
    run(args.data, args.index, args.source)
