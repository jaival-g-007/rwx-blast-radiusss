"""Index baseline operand observations by source location and pytest ID."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def build(traces, destination):
    catalog = json.loads((traces / "catalog.json").read_bytes())
    ids = sorted(catalog["ids"])
    groups = defaultdict(int)
    for i, name in enumerate(ids):
        parts = name.split("::")
        for j in range(1, len(parts) + 1):
            groups["::".join(parts[:j])] |= 1 << i
        groups["*"] |= 1 << i
        for parent in Path(parts[0]).parents:
            groups[parent.as_posix()] |= 1 << i
    values = defaultdict(lambda: defaultdict(int))
    for path in sorted(traces.glob("*.jsonl")):
        with path.open() as log:
            for row in log:
                observation = json.loads(row)
                group = groups[observation["name"]]
                for site, atoms in observation["values"].items():
                    for atom in atoms:
                        value = json.dumps(atom, separators=(",", ":"))
                        values[site][value] |= group

    masks = sorted({m for variants in values.values() for m in variants.values()})
    mask_idx = {mask: i for i, mask in enumerate(masks)}
    atoms = sorted({a for variants in values.values() for a in variants})
    atom_idx = {atom: i for i, atom in enumerate(atoms)}
    data = {
        "tests": ids,
        "incomplete": catalog["incomplete"],
        "atoms": [json.loads(a) for a in atoms],
        "masks": [format(mask, "x") for mask in masks],
        "sites": {
            key: [[atom_idx[a], mask_idx[m]] for a, m in sorted(variants.items())]
            for key, variants in sorted(values.items())
        },
    }
    destination.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print(
        f"{len(values)} operand sites; {len(masks)} distinct sets; {destination.stat().st_size:,} bytes"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traces", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build(args.traces, args.destination)
