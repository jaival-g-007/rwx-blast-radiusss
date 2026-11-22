"""Extract source locations where consumers can observe dictionary values."""

import ast
from collections import defaultdict
from pathlib import Path
from functools import cache


def symbol(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr


def literal_mapping(node):
    if not isinstance(node, ast.Dict) or not node.keys:
        return False
    try:
        return all(
            isinstance(k, ast.Constant) and type(k.value) is str for k in node.keys
        ) and all(
            isinstance(ast.literal_eval(v), (str, bool, int, tuple))
            for v in node.values
        )
    except (TypeError, ValueError):
        return False


def direct_constructors(tree, root):
    result = {}
    for imp in tree.body:
        if not isinstance(imp, ast.ImportFrom) or not (imp.module or "").startswith(
            "sqlglot."
        ):
            continue
        file = root.joinpath(*imp.module.split(".")).with_suffix(".py")
        if not file.is_file():
            continue
        classes = constructors_in(file)
        for item in imp.names:
            if item.name in classes:
                result[item.asname or item.name] = classes[item.name]
    return result


@cache
def constructors_in(file):
    classes = {}
    for cls in ast.parse(file.read_bytes()).body:
        if (
            not isinstance(cls, ast.ClassDef)
            or cls.bases
            or cls.keywords
            or cls.decorator_list
        ):
            continue
        funcs = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
        init = funcs.get("__init__")
        if not init or init.decorator_list or "__new__" in funcs:
            continue
        classes[cls.name] = {a.arg for a in [*init.args.args, *init.args.kwonlyargs]}
    return classes


def consumers(root):
    aliases = defaultdict(set)
    uses = defaultdict(set)
    unsupported = set()
    fields = set()
    for directory in ("sqlglot", "tests"):
        for path in sorted((Path(root) / directory).rglob("*.py")):
            filename = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_bytes())
            constructors = direct_constructors(tree, root)
            parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
            for n, parent in parents.items():
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(
                    parent, ast.ClassDef
                ):
                    dest = n.targets[0] if isinstance(n, ast.Assign) else n.target
                    if (
                        isinstance(dest, ast.Name)
                        and dest.id.isupper()
                        and literal_mapping(n.value)
                    ):
                        fields.add(dest.id)
                if (
                    isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and n.value.isidentifier()
                ):
                    if isinstance(parent, ast.Call) and symbol(parent.func) in {
                        "getattr",
                        "setattr",
                        "vars",
                    }:
                        unsupported.add(n.value)
                name = symbol(n)
                if not name or not isinstance(n.ctx, ast.Load):
                    continue
                if (
                    isinstance(parent, (ast.Assign, ast.AnnAssign))
                    and parent.value is n
                ):
                    targets = (
                        parent.targets
                        if isinstance(parent, ast.Assign)
                        else [parent.target]
                    )
                    if all(symbol(t) for t in targets):
                        aliases[name].update(symbol(t) for t in targets)
                    else:
                        unsupported.add(name)
                    continue
                if isinstance(parent, ast.keyword) and parent.arg:
                    call = parents[parent]
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and parent.arg in constructors.get(call.func.id, ())
                    ):
                        aliases[name].add(parent.arg)
                    else:
                        unsupported.add(name)
                    continue
                if (
                    isinstance(parent, ast.Compare)
                    and len(parent.ops) == 1
                    and n is parent.comparators[0]
                    and isinstance(parent.ops[0], (ast.In, ast.NotIn))
                ):
                    continue
                test = n
                while isinstance(parents.get(test), ast.BoolOp):
                    test = parents[test]
                outer = parents.get(test)
                if (
                    isinstance(outer, (ast.If, ast.While, ast.IfExp))
                    and outer.test is test
                    or isinstance(outer, ast.UnaryOp)
                    and isinstance(outer.op, ast.Not)
                    or isinstance(outer, ast.Call)
                    and isinstance(outer.func, ast.Name)
                    and outer.func.id in {"bool", "len"}
                    and outer.args == [test]
                ):
                    continue
                container = parent
                while container in parents and not isinstance(
                    container, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    container = parents[container]
                if not isinstance(
                    container, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) or container.name in {"__new__", "__init_subclass__", "__init__"}:
                    unsupported.add(name)
                elif (
                    isinstance(parent, ast.Subscript)
                    and parent.value is n
                    or isinstance(parent, ast.Attribute)
                    and parent.value is n
                    and parent.attr in {"get", "items", "values"}
                    and isinstance(parents.get(parent), ast.Call)
                ):
                    lookup = (
                        parent if isinstance(parent, ast.Subscript) else parents[parent]
                    )
                    col = (
                        lookup.col_offset
                        if (isinstance(parent, ast.Subscript) or parent.attr == "get")
                        and lookup.lineno == n.lineno
                        else -1
                    )
                    uses[name].add((filename, n.lineno, col))
                else:
                    unsupported.add(name)
    result = {}
    for field in fields:
        pending = [field]
        seen = set()
        locations = set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            locations.update(uses[name])
            pending.extend(aliases[name] - seen)
        if not seen & unsupported and locations:
            result[field] = sorted(locations)
    return result
