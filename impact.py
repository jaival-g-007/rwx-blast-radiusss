"""Project a patch onto baseline line coverage, file reads and class usage."""

from __future__ import annotations

import ast
import json
from bisect import bisect_left
from collections import defaultdict
from functools import cache

from comparisons import Comparisons
from patches import UnsupportedPatch

FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE = (*FUNCTION, ast.ClassDef, ast.Lambda)


def first_line(node):
    decorators = getattr(node, "decorator_list", ())
    return min([node.lineno] + [expr.lineno for expr in decorators])


class Source:
    def __init__(self, text):
        self.text = text
        self.tree = ast.parse(text)
        self.parent = {
            child: node for node in ast.walk(self.tree) for child in ast.iter_child_nodes(node)
        }
        self.at = {}
        # Breadth-first visitation lets children replace their containing nodes.
        for node in ast.walk(self.tree):
            if isinstance(
                node, (ast.stmt, ast.expr, ast.ExceptHandler, ast.match_case)
            ) and hasattr(node, "lineno"):
                for line in range(first_line(node), node.end_lineno + 1):
                    self.at[line] = node

    def ancestors(self, line):
        node = self.at.get(line)
        while node is not None:
            yield node
            node = self.parent.get(node)


class FileLines:
    def __init__(self, ranges, masks):
        self.ends = [end for _, end, _ in ranges]
        self.ranges = [(start, masks[ref]) for start, _, ref in ranges]

    def hits(self, first, last):
        mask = 0
        i = bisect_left(self.ends, first)
        while i < len(self.ranges):
            start, bits = self.ranges[i]
            if start > last:
                break
            mask |= bits
            i += 1
        return mask


class Impact:
    def __init__(self, index_path, source_root, tests):
        data = json.loads(index_path.read_bytes())
        self.source_root = source_root
        self.names = data["tests"]
        self.by_name = {name: i for i, name in enumerate(self.names)}
        self.test_files = defaultdict(int)
        self.number_by_bit = {}
        self.always = set()
        self.all = 0
        incomplete = set(data["incomplete"])
        for test in tests:
            name, number = test["id"], test["number"]
            if name not in self.by_name or name in incomplete:
                self.always.add(number)
            else:
                i = self.by_name[name]
                self.all |= 1 << i
                self.number_by_bit[i] = number
                self.test_files[test["file"]] |= 1 << i
        masks = [int(value, 16) & self.all for value in data["masks"]]
        self.lines = {path: FileLines(ranges, masks) for path, ranges in data["lines"].items()}
        self.cold = {path: FileLines(ranges, masks) for path, ranges in data["cold"].items()}
        self.imports = {path: masks[ref] for path, ref in data["imports"].items()}
        self.reads = {path: masks[ref] for path, ref in data["reads"].items()}
        self.symbols = {key: masks[ref] for key, ref in data["symbols"].items()}
        self.derived = set(data["derived"])
        self.types = {key: masks[ref] for key, ref in data["types"].items()}
        self.comparisons = Comparisons(index_path.with_name("operands.json"), self.names, self.all)

    @cache
    def source(self, name):
        path = (self.source_root / name).resolve()
        if not path.is_relative_to(self.source_root):
            raise UnsupportedPatch("source outside checkout")
        return Source(path.read_bytes().decode("utf-8"))

    def hits(self, filename, first, last):
        lines = self.lines.get(filename)
        return lines.hits(first, last) if lines else 0

    def file_users(self, path):
        return self.hits(path, 1, 2**31) | self.reads.get(path, 0) | self.test_files.get(path, 0)

    def broad(self, filename):
        if filename.startswith("tests/") and filename in self.imports:
            return self.imports[filename] | self.file_users(filename)
        return self.all

    def declaring_class(self, filename, chain):
        parts = [n.name for n in reversed(chain) if isinstance(n, ast.ClassDef)]
        module = filename.removesuffix(".py").replace("/", ".")
        return ".".join([module, *parts])

    def at_line(self, filename, source, line, insertion):
        chain = list(source.ancestors(line))
        scope = next((n for n in chain if isinstance(n, SCOPE)), None)
        readers = self.reads.get(filename, 0)
        if scope is None:
            return self.broad(filename)
        if isinstance(scope, (*FUNCTION, ast.ClassDef)) and line < scope.body[0].lineno:
            return self.broad(filename)
        if isinstance(scope, ast.ClassDef):
            # CPython executes class-body assignments during import. Tests may
            # later consume an inherited configuration without re-running it.
            typename = self.declaring_class(filename, chain)
            consumers = self.types.get(typename)
            if consumers is None:
                return self.broad(filename)
            owner = ".".join(n.name for n in reversed(chain) if isinstance(n, ast.ClassDef))
            consumers |= self.symbols.get(owner, 0)
            assignment = next(
                (n for n in chain if isinstance(n, (ast.Assign, ast.AnnAssign))), None
            )
            if assignment is not None:
                targets = (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
                if len(targets) == 1 and isinstance(targets[0], ast.Name):
                    key = targets[0].id
                    if key.isupper() and key in self.symbols and key not in self.derived:
                        consumers &= self.symbols[key]
            return consumers | readers | self.test_files.get(filename, 0)
        if isinstance(scope, ast.Lambda):
            # A lambda in a class-level dispatch table also runs at call time.
            # The key on its definition line can affect callers of any entry.
            if any(isinstance(n, ast.Dict) for n in chain) and not any(
                isinstance(n, FUNCTION) for n in chain
            ):
                outer = next((n for n in chain if isinstance(n, ast.ClassDef)), None)
                if outer:
                    typename = self.declaring_class(filename, chain)
                    return self.types.get(typename, self.all) | readers
                return self.file_users(filename) or self.all
        if insertion or line <= scope.lineno:
            hits = self.hits(filename, first_line(scope), scope.end_lineno)
            if line <= scope.lineno and not hits:
                return self.file_users(filename) or self.all
            return hits | readers
        hits = self.hits(filename, line, line)
        # A changed short-circuit operator may admit a path which never
        # reached the old right operand. Include evaluation of the condition.
        for node in chain:
            if isinstance(node, (ast.BoolOp, ast.Compare, ast.IfExp)):
                hits |= self.hits(filename, node.lineno, node.lineno)
            if isinstance(node, ast.stmt):
                hits |= self.hits(filename, node.lineno, node.lineno)
                break
        return hits | readers

    def select_file(self, change):
        filename = change.old or change.new
        if change.old != change.new:
            # File creation, deletion or rename can alter discovery/imports.
            return self.all
        if not filename.endswith(".py"):
            if filename in self.reads:
                return self.reads[filename]
            return self.all
        source = self.source(filename)
        patched = change.apply(source.text)
        try:
            tree = ast.parse(patched)
            compile(tree, filename, "exec", dont_inherit=True)
        except (SyntaxError, ValueError):
            return self.broad(filename)
        # Ordinary comment and layout edits can still affect tests that
        # read source text or line locations. Keep their explicit readers.
        if ast.dump(tree) == ast.dump(source.tree):
            return self.reads.get(filename, 0)
        # Import paths, renamed definitions and decorators can break collection
        # before any test body runs. A module-level exception has the same risk.
        headers = ("import ", "from ", "class ", "def ", "async def ", "@")
        changed_text = [text for hunk in change.hunks for kind, text in hunk.rows if kind != " "]
        if any(text.lstrip().startswith(headers) for text in changed_text):
            return self.broad(filename)
        edits = list(change.edits())
        mask = self.reads.get(filename, 0)
        protected = mask
        cold = self.cold.get(filename)
        for start, deleted, _ in edits:
            if cold:
                protected |= cold.hits(max(1, start - (not deleted)), start + max(1, deleted) - 1)
            if deleted:
                selected = range(start, start + deleted)
            else:
                selected = range(max(1, start - 1), start + 1)
            for line in selected:
                mask |= self.at_line(filename, source, line, deleted == 0)
        if mask and self.comparisons.has_sites(filename, change):
            mask = self.comparisons.refine(filename, source.tree, tree, mask)
        return mask | protected

    def numbers(self, mask):
        result = set(self.always)
        while mask:
            bit = mask & -mask
            result.add(self.number_by_bit[bit.bit_length() - 1])
            mask ^= bit
        return result
