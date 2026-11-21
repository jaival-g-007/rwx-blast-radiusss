"""Use baseline operand coverage to check a changed comparison at selection time."""

from __future__ import annotations

import ast
import itertools
import json
import operator

ORDERING = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
EQUALITY = (ast.Eq, ast.NotEq)
IDENTITY = (ast.Is, ast.IsNot)
UNKNOWN = ("?",)
UNSUPPORTED = object()


def freeze(value):
    return tuple(freeze(v) for v in value) if isinstance(value, list) else value


def static_atom(node):
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None

    def encode(obj):
        if obj is None:
            return ("none",)
        if type(obj) in (bool, int, str, float):
            return (type(obj).__name__, obj)
        if isinstance(obj, tuple):
            parts = tuple(encode(i) for i in obj)
            if None not in parts:
                return ("tuple", *parts)
        return None

    return encode(value)


def scalar(atom):
    if atom[0] == "none":
        return None
    if atom[0] in ("int", "str", "bool", "float"):
        return atom[1]
    if atom[0] == "tuple":
        parts = tuple(scalar(part) for part in atom[1:])
        if UNSUPPORTED not in parts:
            return parts
    return UNSUPPORTED


def outcome(op, left, right):
    if UNKNOWN in (left, right):
        return UNSUPPORTED
    if "plain-nonnull" in (left[0], right[0]) and "none" not in (left[0], right[0]):
        return UNSUPPORTED
    if isinstance(op, IDENTITY):
        if left[0] == "none" or right[0] == "none":
            same = left[0] == right[0]
        elif left[0] == "enum" and right[0] == "enum":
            same = left[1:3] == right[1:3]
        elif left[0] == "bool" or right[0] == "bool":
            same = left == right
        else:
            return UNSUPPORTED
        return not same if isinstance(op, ast.IsNot) else same
    if isinstance(op, EQUALITY):
        if left[0] == "none" or right[0] == "none":
            equal = left[0] == right[0]
        elif left[0] == "enum" and right[0] == "enum" and left[1] == right[1]:
            equal = left[2] == right[2]
        else:
            a, b = scalar(left), scalar(right)
            if UNSUPPORTED in (a, b):
                return UNSUPPORTED
            equal = a == b
        return not equal if isinstance(op, ast.NotEq) else equal
    compare = ORDERING.get(type(op))
    a, b = scalar(left), scalar(right)
    if compare and UNSUPPORTED not in (a, b):
        try:
            return compare(a, b)
        except TypeError:
            pass
    return UNSUPPORTED


def changed_comparison(old, new):
    """Accept exactly one modified binary comparison in an otherwise identical AST."""
    difference = None
    stack = [(old, new)]
    while stack:
        a, b = stack.pop()
        if type(a) is not type(b):
            return None
        if isinstance(a, ast.Compare) and ast.dump(a) != ast.dump(b):
            if difference is not None or len(a.ops) != 1 or len(b.ops) != 1:
                return None
            difference = a, b
        elif isinstance(a, ast.AST):
            stack.extend((value, getattr(b, key)) for key, value in ast.iter_fields(a))
        elif isinstance(a, list):
            if len(a) != len(b):
                return None
            stack.extend(zip(a, b))
        elif a != b:
            return None
    return difference


class Comparisons:
    def __init__(self, path, names, all_mask):
        data = json.loads(path.read_bytes())
        if data["tests"] != names:
            raise ValueError("operand index and coverage index use different test populations")
        self.atoms = [freeze(value) for value in data["atoms"]]
        self.masks = [int(bits, 16) & all_mask for bits in data["masks"]]
        self.sites = data["sites"]
        self.site_lines = {}
        for key in self.sites:
            filename, line, _, _ = key.rsplit(":", 3)
            self.site_lines.setdefault(filename, set()).add(int(line))
        incomplete = set(data["incomplete"])
        self.complete = all_mask & ~sum(
            1 << i for i, name in enumerate(names) if name in incomplete
        )

    def has_sites(self, filename, change):
        lines = self.site_lines.get(filename, ())
        return bool(lines) and any(
            line in lines
            for start, deleted, _ in change.edits()
            for line in range(start, start + deleted)
        )

    def variants(self, filename, comparison, side, old, new, mask):
        before, after = static_atom(old), static_atom(new)
        if before is not None and after is not None:
            return [(before, after, mask)]
        if ast.dump(old) != ast.dump(new):
            return []
        key = f"{filename}:{comparison.lineno}:{comparison.col_offset}:{side}"
        return [
            (self.atoms[i], self.atoms[i], mask & self.masks[j])
            for i, j in self.sites.get(key, ())
            if mask & self.masks[j]
        ]

    def refine(self, filename, previous, current, mask):
        pair = changed_comparison(previous, current)
        if pair is None:
            return mask
        old, new = pair
        left = self.variants(filename, old, 0, old.left, new.left, mask)
        right = self.variants(filename, old, 1, old.comparators[0], new.comparators[0], mask)
        if not left or not right or len(left) * len(right) > 4096:
            return mask
        observed = unsafe = 0
        for (a, next_a, group_a), (b, next_b, group_b) in itertools.product(left, right):
            group = group_a & group_b
            if not group:
                continue
            observed |= group
            before = outcome(old.ops[0], a, b)
            after = outcome(new.ops[0], next_a, next_b)
            if UNSUPPORTED in (before, after) or before != after:
                unsafe |= group
        # Missing observations, truncated domains and uncertain Python rich
        # comparisons always retain the test. Cross-products overapproximate
        # the actual pairs when both sides were profiled.
        return mask & ~(self.complete & observed & ~unsafe)
