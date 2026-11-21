"""Generate observations from unmodified SQLGlot tests (CPython 3.13)."""

from __future__ import annotations

import json
import os
import pickle
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from multiprocessing.reduction import ForkingPickler
from pathlib import Path

import pytest

import value_trace

VALUES = os.environ.get("RWX_PROFILE_VALUES") == "1"

monitor = sys.monitoring
tool_id = monitor.COVERAGE_ID
root = Path.cwd()
prefix = str(root) + "/"
output = Path(os.environ.get("RWX_TRACE_DIR", "/out"))
active = None
observations = {}
installed = False
role_by_code = {}


class Observation:
    def __init__(self):
        self.lines = defaultdict(set)
        self.reads = set()
        self.types = set()
        self.values = {}

    def save(self, name, path):
        data = {
            "name": name,
            "lines": {key: sorted(value) for key, value in self.lines.items()},
            "reads": sorted(self.reads),
            "values": {site: sorted(values, key=repr) for site, values in self.values.items()},
            "types": sorted({name for cls in self.types for name in class_names(cls)}),
        }
        with path.open("a") as handle:
            handle.write(json.dumps(data, separators=(",", ":")) + "\n")


@cache
def relative(filename):
    if filename.startswith(prefix):
        return filename[len(prefix) :]
    return None


def on_line(code, line):
    if active is not None:
        filename = relative(code.co_filename)
        if filename:
            active.lines[filename].add(line)
    return monitor.DISABLE


def on_open(event, args):
    if active is None or event != "open" or not isinstance(args[0], str):
        return
    name = args[0]
    if not name.startswith("/"):
        name = os.path.abspath(name)
    path = relative(name)
    if not path or "__pycache__/" in path or path.endswith(".pyc"):
        return
    if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
        return
    # Imports are already represented by coverage and module analysis. Keep
    # explicit source reads such as tests using inspect, ast or pathlib.
    if path.endswith(".py") and sys._getframe(1).f_code.co_filename.startswith("<frozen importlib"):
        return
    active.reads.add(path)


@cache
def class_names(cls):
    return tuple(
        f"{base.__module__}.{base.__qualname__}"
        for base in cls.__mro__
        if base.__module__.startswith("sqlglot")
    )


def on_factory(code, offset):
    if active is None:
        return
    dialect = sys._getframe(1).f_locals.get("self")
    if dialect is None:
        return
    active.types.add(type(dialect))
    field = role_by_code[code]
    if field:
        component = getattr(dialect, field)
        active.types.add(component)


def install():
    global installed
    if installed or VALUES:
        return
    from sqlglot import exp
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.generator import Generator
    from sqlglot.parser import Parser
    from sqlglot.tokens import Tokenizer

    monitor.use_tool_id(tool_id, "rwx-baseline")
    monitor.register_callback(tool_id, monitor.events.LINE, on_line)
    monitor.register_callback(tool_id, monitor.events.PY_START, on_factory)
    monitor.set_events(tool_id, monitor.events.LINE)
    for name in ("__init__", "generator", "parser", "tokenizer", "jsonpath_tokenizer"):
        code = getattr(Dialect, name).__code__
        role_by_code[code] = None if name == "__init__" else name + "_class"
        monitor.set_local_events(tool_id, code, monitor.events.PY_START)
    for cls in (exp.Expr, Parser, Generator, Tokenizer):
        code = cls.__init__.__code__
        role_by_code[code] = None
        monitor.set_local_events(tool_id, code, monitor.events.PY_START)
    sys.addaudithook(on_open)
    installed = True


def switch(name):
    global active
    active = observations.setdefault(name, Observation()) if name else None
    if VALUES:
        value_trace.current = active.values if active else None
    else:
        monitor.restart_events()


class EncodedResult:
    """Restore the real return value in the pool's normal result receiver."""

    def __init__(self, payload):
        self.payload = payload

    def __reduce__(self):
        return pickle.loads, (self.payload,)


def remote_call(name, payload):
    """Record work a test delegates to a spawned or reused process."""
    install()
    switch(name)
    try:
        # Put both IPC conversions inside the parent's test context. SQLGlot
        # expressions execute their own Python code during pickling/unpickling.
        func, args, kwargs = pickle.loads(payload)
        result = func(*args, **kwargs)
        return EncodedResult(bytes(ForkingPickler.dumps(result)))
    finally:
        observation = observations.pop(name)
        switch(None)
        observation.save(name, output / f"child-{os.getpid()}.jsonl")


class Plugin:
    def __init__(self):
        self.name = None
        self.flat = []
        self.failed = set()
        self.submit = ProcessPoolExecutor.submit

    def pytest_collection_finish(self, session):
        self.flat = [item.nodeid for item in session.items]
        install()
        original = self.submit

        def submit(executor, fn, /, *args, **kwargs):
            if self.name:
                payload = bytes(ForkingPickler.dumps((fn, args, kwargs)))
                return original(executor, remote_call, self.name, payload)
            return original(executor, fn, *args, **kwargs)

        ProcessPoolExecutor.submit = submit

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.name = item.nodeid
        switch(self.name)
        try:
            return (yield)
        finally:
            switch(None)
            self.name = None

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_fixture_setup(self, fixturedef, request):
        previous = self.name
        if previous and fixturedef.scope != "function":
            self.name = request.node.nodeid or "*"
            switch(self.name)
        try:
            return (yield)
        finally:
            if self.name != previous:
                self.name = previous
                switch(previous)

    def pytest_runtest_logreport(self, report):
        if report.failed or report.skipped:
            self.failed.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if installed or VALUES:
            switch(None)
        ProcessPoolExecutor.submit = self.submit
        with (output / "catalog.json").open("w") as handle:
            json.dump({"ids": self.flat, "incomplete": sorted(self.failed)}, handle)
        path = output / "parent.jsonl"
        for name, obs in observations.items():
            obs.save(name, path)


def run():
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob("*.jsonl")):
        raise SystemExit("Use an empty trace directory for each new run.")
    return pytest.main(["tests", "-q", "--tb=short", "-p", "no:cacheprovider"], plugins=[Plugin()])


if __name__ == "__main__":
    # Import by a real module name so spawn workers can unpickle remote_call.
    from trace_tests import run

    raise SystemExit(run())
