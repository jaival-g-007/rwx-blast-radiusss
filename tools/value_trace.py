"""Record bounded operand coverage on the original revision, without reading patches.

A wrapped operand is returned unchanged. Python still evaluates the original
comparison, including rich comparisons, at its original source location.
"""

from __future__ import annotations

import ast
import builtins
import enum
import importlib.machinery
import math
import sys
from functools import cache
from pathlib import Path

current = None
MAX_VALUES = 12
UNKNOWN = ("?",)
SCALARS = {int, float, str, bool}
SIMPLE_EQUALITY = {
    object.__eq__,
    int.__eq__,
    float.__eq__,
    str.__eq__,
    bytes.__eq__,
    tuple.__eq__,
    list.__eq__,
    set.__eq__,
    frozenset.__eq__,
    dict.__eq__,
}


@cache
def simple_none_equality(cls):
    return any(cls.__eq__ is method for method in SIMPLE_EQUALITY) and cls.__ne__ in (
        object.__ne__,
        int.__ne__,
        float.__ne__,
        str.__ne__,
        bytes.__ne__,
        tuple.__ne__,
        list.__ne__,
        set.__ne__,
        frozenset.__ne__,
        dict.__ne__,
    )


def atom(value):
    cls = type(value)
    if value is None:
        return ("none",)
    if cls in SCALARS:
        if cls is str and len(value) > 64 or cls is int and value.bit_length() > 63:
            return UNKNOWN
        if cls is float and not math.isfinite(value):
            return UNKNOWN
        return (cls.__name__, value)
    if cls is tuple and len(value) <= 6 and all(type(v) in (str, int, bool) for v in value):
        items = tuple(atom(v) for v in value)
        if UNKNOWN not in items:
            return ("tuple", *items)
    if isinstance(value, enum.Enum):
        # Identity and equality coincide for ordinary Enum members of the
        # same class with unique int/string values and standard comparators.
        raw = value._value_
        if (
            type(raw) in (str, int)
            and cls.__eq__ in (object.__eq__, str.__eq__, int.__eq__)
            and cls.__ne__ in (object.__ne__, str.__ne__, int.__ne__)
        ):
            return ("enum", cls.__module__ + "." + cls.__qualname__, value._name_, atom(raw))
    return UNKNOWN


def observe(site, none_only, value):
    if current is None:
        return value
    bucket = current.setdefault(site, set())
    if UNKNOWN in bucket:
        return value
    if none_only:
        key = (
            ("none",)
            if value is None
            else ("plain-nonnull",)
            if simple_none_equality(type(value))
            else UNKNOWN
        )
    else:
        key = atom(value)
    if key == UNKNOWN or key not in bucket and len(bucket) >= MAX_VALUES:
        bucket.clear()
        bucket.add(UNKNOWN)
    else:
        bucket.add(key)
    return value


def fixed_operand(node):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.attr.isupper():
        return "member"
    try:
        literal = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None
    return "literal" if atom(literal) != UNKNOWN else None


class Observe(ast.NodeTransformer):
    def __init__(self, filename):
        self.filename = filename
        self.depth = 0

    def visit_FunctionDef(self, node):
        self.depth += 1
        node.body = [self.visit(child) for child in node.body]
        self.depth -= 1
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        # Some tests compare lambda bytecode and constants for equivalence.
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        if (
            not self.depth
            or len(node.ops) != 1
            or not isinstance(
                node.ops[0],
                (ast.Is, ast.IsNot, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE),
            )
        ):
            return node
        operands = [node.left, node.comparators[0]]
        kinds = [fixed_operand(value) for value in operands]
        if not any(kinds):
            return node
        none_only = any(isinstance(v, ast.Constant) and v.value is None for v in operands)
        for side, value in enumerate(operands):
            if kinds[side] == "literal":
                continue
            site = f"{self.filename}:{node.lineno}:{node.col_offset}:{side}"
            call = ast.Call(
                ast.Name("_rwx_observe_operand", ast.Load()),
                [ast.Constant(site), ast.Constant(none_only), value],
                [],
            )
            operands[side] = ast.copy_location(call, value)
        node.left, node.comparators[0] = operands
        return node


class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        filename = self.get_filename(fullname)
        relative = Path(filename).relative_to(Path.cwd()).as_posix()
        source = self.get_data(filename)
        tree = Observe(relative).visit(ast.parse(source, filename))
        return compile(ast.fix_missing_locations(tree), filename, "exec", dont_inherit=True)


class Finder:
    def find_spec(self, fullname, path, target=None):
        if fullname != "sqlglot" and not fullname.startswith("sqlglot."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = Loader(fullname, spec.origin)
        return spec


def enable():
    builtins._rwx_observe_operand = observe
    sys.meta_path.insert(0, Finder())
