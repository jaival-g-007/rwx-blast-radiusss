"""Convert baseline pytest observations into a compact, portable coverage index."""

from __future__ import annotations

import argparse
import ast
import sys
import json
from collections import defaultdict
from pathlib import Path

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def symbol_coverage(root, lines, types):
    """Find direct references and configuration reads in executed source."""
    sys.path.insert(0, str(root.resolve()))
    from sqlglot import Dialect, exp
    from sqlglot.parser import Parser
    from sqlglot.generator import Generator
    from sqlglot.tokens import Tokenizer

    classes = [exp.Expr, Parser, Generator, Tokenizer, Dialect, *Dialect.classes.values()]
    seen = set()
    while classes:
        cls = classes.pop()
        if cls in seen:
            continue
        seen.add(cls)
        types.setdefault(f"{cls.__module__}.{cls.__qualname__}", 0)
        classes.extend(cls.__subclasses__())

    symbols = defaultdict(int)
    derived = set()
    class_names = {name.rsplit(".", 1)[-1] for name in types}
    for base in ("sqlglot", "tests"):
        for path in sorted((root / base).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            trace = lines.get(relative, {})
            tree = ast.parse(path.read_bytes())
            parents = {
                child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    name = node.attr
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    name = node.id
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in ("getattr", "hasattr")
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                ):
                    name = node.args[1].value
                else:
                    continue
                if not name.isupper() and name not in class_names:
                    continue
                use = trace.get(node.lineno, 0)
                parent = node
                while parent in parents and not isinstance(parent, ast.stmt):
                    parent = parents[parent]
                use |= trace.get(getattr(parent, "lineno", -1), 0)
                symbols[name] |= use
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    symbols[node.value.id + "." + name] |= use
                if name.isupper() and base == "sqlglot":
                    while parent in parents and not isinstance(
                        parent, (ast.FunctionDef, ast.Lambda, ast.AsyncFunctionDef)
                    ):
                        parent = parents[parent]
                    if isinstance(parent, ast.Module) or getattr(parent, "name", "") in (
                        "__new__",
                        "__init_subclass__",
                        "__set_name__",
                        "__init__",
                    ):
                        derived.add(name)
    return symbols, sorted(derived)


def compact(traces, destination, source):
    catalog = json.loads((traces / "catalog.json").read_text())
    ids = sorted(catalog["ids"])
    groups = defaultdict(int)
    for i, name in enumerate(ids):
        bit = 1 << i
        pieces = name.split("::")
        for j in range(1, len(pieces) + 1):
            groups["::".join(pieces[:j])] |= bit
        path = Path(pieces[0])
        for parent in path.parents:
            groups[parent.as_posix()] |= bit
        groups["*"] |= bit

    lines = defaultdict(lambda: defaultdict(int))
    cold = defaultdict(lambda: defaultdict(int))
    imports = defaultdict(int)
    reads = defaultdict(int)
    types = defaultdict(int)
    for path in sorted(traces.glob("*.jsonl")):
        with path.open() as log:
            for row in log:
                obs = json.loads(row)
                mask = groups[obs["name"]]
                if not mask:
                    raise ValueError(f"unrecognized pytest context {obs['name']!r}")
                for filename, numbers in obs["lines"].items():
                    by_line = (cold if "imports" in obs else lines)[filename]
                    for number in numbers:
                        by_line[number] |= mask
                for filename in obs["reads"]:
                    reads[filename] |= mask
                for filename in obs.get("imports", ()):
                    imports[filename] |= mask
                for typename in obs["types"]:
                    types[typename] |= mask

    # Many consecutive lines have the same test population. Intern the sets
    # once across the entire checkout and run-length encode their references.
    symbols, derived = symbol_coverage(source, lines, types)
    masks = sorted(
        {
            0,
            *reads.values(),
            *types.values(),
            *symbols.values(),
            *imports.values(),
            *(mask for table in (lines, cold) for f in table.values() for mask in f.values()),
        }
    )
    ref = {mask: i for i, mask in enumerate(masks)}

    def encode_ranges(table):
        ranges = {}
        for filename, by_line in sorted(table.items()):
            spans = []
            for number, mask in sorted(by_line.items()):
                i = ref[mask]
                if spans and spans[-1][1] == number - 1 and spans[-1][2] == i:
                    spans[-1][1] = number
                else:
                    spans.append([number, number, i])
            ranges[filename] = spans
        return ranges

    ranges = encode_ranges(lines)
    result = {
        "revision": REVISION,
        "tests": ids,
        "incomplete": catalog["incomplete"],
        "masks": [format(m, "x") for m in masks],
        "lines": ranges,
        "reads": {p: ref[m] for p, m in sorted(reads.items())},
        "types": {p: ref[m] for p, m in sorted(types.items())},
        "cold": encode_ranges(cold),
        "imports": {p: ref[m] for p, m in sorted(imports.items())},
        "symbols": {p: ref[m] for p, m in sorted(symbols.items())},
        "derived": derived,
    }
    destination.write_text(json.dumps(result, separators=(",", ":")) + "\n")
    print(
        f"{len(ids)} test IDs; {len(ranges)} traced files; {len(masks)} distinct sets; "
        f"{destination.stat().st_size:,} bytes"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traces", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    compact(args.traces, args.destination, args.source)
