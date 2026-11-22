"""Record actual dictionary lookup keys in the pinned checkout, before any patches."""

import ast
import builtins
import importlib.machinery
import json
import sys
from pathlib import Path

from value_trace import observe


class Lookups(ast.NodeTransformer):
    def __init__(self, filename, lines):
        self.filename = filename
        self.lines = lines

    def wrap(self, node, key):
        site = f"{self.filename}:{node.lineno}:{node.col_offset}:0"
        call = ast.Call(
            ast.Name("_rwx_record_key", ast.Load()),
            [ast.Constant(site), ast.Constant(False), key],
            [],
        )
        return ast.copy_location(call, key)

    def visit_Call(self, node):
        self.generic_visit(node)
        if (
            node.lineno in self.lines
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            node.args[0] = self.wrap(node, node.args[0])
        return node

    def visit_Subscript(self, node):
        self.generic_visit(node)
        if (
            node.lineno in self.lines
            and isinstance(node.ctx, ast.Load)
            and not isinstance(node.slice, ast.Slice)
        ):
            node.slice = self.wrap(node, node.slice)
        return node


class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        path = self.get_filename(fullname)
        relative = Path(path).relative_to(Path.cwd()).as_posix()
        tree = Lookups(relative, lines[relative]).visit(
            ast.parse(self.get_data(path), path)
        )
        return compile(ast.fix_missing_locations(tree), path, "exec", dont_inherit=True)


class Finder:
    def find_spec(self, fullname, path, target=None):
        if fullname.replace(".", "/") + ".py" not in lines:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = Loader(fullname, spec.origin)
        return spec


lines = {}


def enable():
    index = json.loads(
        (Path(__file__).resolve().parents[1] / "coverage.json").read_bytes()
    )
    for locations in index["mapping_uses"].values():
        for path, line, *_ in locations:
            lines.setdefault(path, set()).add(line)
    builtins._rwx_record_key = observe
    sys.meta_path.insert(0, Finder())
