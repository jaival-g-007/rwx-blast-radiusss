"""Run short, self-contained dialect unittests against restricted production edits."""

import ast
import contextlib
import importlib.abc
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path


def shape(tree):
    # Only literal replacements of the same primitive type and boolean or
    # comparison operator replacements pass. Never accept a new call or name.
    if isinstance(tree, ast.Constant) and type(tree.value) in (bool, int):
        return ("literal", type(tree.value))
    if isinstance(tree, (ast.boolop, ast.cmpop)):
        return type(tree).__base__
    if isinstance(tree, ast.AST):
        return type(tree), tuple(shape(v) for _, v in ast.iter_fields(tree))
    if isinstance(tree, list):
        return tuple(map(shape, tree))
    return tree


def refine(change, impact, tests, selected, deadline):
    path = change.old
    if (
        not path
        or path != change.new
        or not path.startswith("sqlglot/")
        or not path.endswith(".py")
        or time.monotonic() >= deadline
    ):
        return selected
    candidates = [
        tests[n]
        for n in selected
        if (
            tests[n]["file"].startswith("tests/dialects/")
            or tests[n]["file"]
            in {
                "tests/test_parser.py",
                "tests/test_expressions.py",
                "tests/test_generator.py",
            }
        )
    ]
    if (
        len(candidates) < 2
        or len(candidates) != len(selected)
        or sum(t["seconds"] for t in candidates) > 1.0
    ):
        return selected
    original = impact.source(path)
    updated = change.apply(original.text)
    try:
        if shape(original.tree) != shape(ast.parse(updated)):
            return selected
    except (ValueError, SyntaxError, RecursionError):
        return selected
    payload = [str(impact.source_root), path, updated, [t["id"] for t in candidates]]
    try:
        result = subprocess.run(
            [sys.executable, "-I", __file__],
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=max(0.05, min(2.0, deadline - time.monotonic())),
            check=True,
        )
        passed = set(json.loads(result.stdout))
    except (OSError, ValueError, subprocess.SubprocessError):
        return selected
    protected = impact.numbers(impact.reads.get(path, 0) | impact.introspection)
    return (selected - {t["number"] for t in candidates if t["id"] in passed}) | (
        selected & protected
    )


def probe(root, path, updated, ids):
    filename = str(Path(root) / path)
    module = path[:-3].replace("/", ".")
    if module.endswith(".__init__"):
        return []

    class Overlay(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path, target=None):
            if fullname == module:
                return importlib.util.spec_from_file_location(
                    fullname, filename, loader=self
                )

        def exec_module(self, obj):
            exec(compile(updated, filename, "exec"), vars(obj))

    sys.meta_path.insert(0, Overlay())
    sys.path.insert(0, root)
    os.chdir(root)
    passed = []

    class Result(unittest.TestResult):
        def addSuccess(self, test):
            passed.append(test.id())

    names = {
        i.replace("/", ".").replace(".py::", ".").replace("::", "."): i for i in ids
    }
    suite = unittest.defaultTestLoader.loadTestsFromNames(sorted(names))
    result = Result()
    suite.run(result)
    # Class/module fixture errors or failed collection can affect other tests.
    if any(case.id() not in names for case, _ in result.errors):
        return []
    return [names[n] for n in passed if n in names]


if __name__ == "__main__":
    with contextlib.redirect_stdout(sys.stderr):
        output = probe(*json.load(sys.stdin))
    print(json.dumps(output))
