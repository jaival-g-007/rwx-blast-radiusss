"""Measure function execution and explicit file reads during cold test imports."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def single(test_file):
    prefix = str(Path.cwd()) + "/"
    lines = defaultdict(set)
    reads = set()
    monitor = sys.monitoring

    def on_line(code, line):
        # Class and module initialization statements are handled structurally.
        # A normal function called during import is also a real dependency of
        # every test in the imported file, even if its body never calls it.
        if code.co_flags & 1 and code.co_filename.startswith(prefix):
            lines[code.co_filename[len(prefix) :]].add(line)
        return monitor.DISABLE

    def on_open(event, args):
        if event == "open" and isinstance(args[0], str):
            path = args[0]
            if path.startswith(prefix) and not path.endswith((".pyc", ".py")):
                reads.add(path[len(prefix) :])

    monitor.use_tool_id(monitor.COVERAGE_ID, "cold-import")
    monitor.register_callback(monitor.COVERAGE_ID, monitor.events.LINE, on_line)
    monitor.set_events(monitor.COVERAGE_ID, monitor.events.LINE)
    sys.addaudithook(on_open)
    importlib.import_module(test_file.removesuffix(".py").replace("/", "."))
    monitor.set_events(monitor.COVERAGE_ID, 0)
    imports = {
        path[len(prefix) :]
        for module in sys.modules.copy().values()
        if isinstance(path := getattr(module, "__file__", None), str) and path.startswith(prefix)
    }
    print(
        json.dumps(
            {
                "name": test_file,
                "imports": sorted(imports),
                "lines": {k: sorted(v) for k, v in lines.items()},
                "reads": sorted(reads),
                "types": [],
            },
            separators=(",", ":"),
        )
    )


def run(directory):
    names = json.loads((directory / "catalog.json").read_bytes())["ids"]
    files = sorted({name.split("::")[0] for name in names})
    with (directory / "imports.jsonl").open("w") as output:
        for path in files:
            subprocess.run([sys.executable, __file__, "--file", path], stdout=output, check=True)
    print(f"Observed {len(files)} separate cold test-module imports.")


if __name__ == "__main__":
    if sys.argv[1] == "--file":
        single(sys.argv[2])
    else:
        run(Path(sys.argv[1]))
