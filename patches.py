"""Read Git unified diffs in base-revision coordinates. No patch is executed."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
NO_NEWLINE = "\\ No newline at end of file"


class UnsupportedPatch(ValueError):
    pass


def path_from_header(value):
    raw = value.rstrip("\n").split("\t", 1)[0]
    if raw == "/dev/null":
        return None
    if raw.startswith('"'):
        try:
            raw = ast.literal_eval("b" + raw).decode("utf-8", "surrogateescape")
        except (SyntaxError, ValueError) as error:
            raise UnsupportedPatch("invalid quoted path") from error
    if not raw.startswith(("a/", "b/")):
        raise UnsupportedPatch("missing Git path prefix")
    name = raw[2:]
    if not name or PurePosixPath(name).is_absolute() or ".." in name.split("/") or "\x00" in name:
        raise UnsupportedPatch("invalid source path")
    return name


@dataclass
class Hunk:
    start: int  # zero-based start in the original file
    rows: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Change:
    old: str | None
    new: str | None
    hunks: list[Hunk] = field(default_factory=list)

    def edits(self):
        """Yield (one-based old start, deleted line count, inserted line count)."""
        for hunk in self.hunks:
            line = hunk.start + 1
            start = line
            deleted = added = 0
            for kind, _ in hunk.rows:
                if kind == " ":
                    if deleted or added:
                        yield start, deleted, added
                    line += 1
                    start, deleted, added = line, 0, 0
                elif kind == "-":
                    deleted += 1
                    line += 1
                else:
                    added += 1
            if deleted or added:
                yield start, deleted, added

    def without_trivia(self):
        if sum(1 for _ in self.edits()) < 2:
            return self
        hunks = []
        changed = False
        for hunk in self.hunks:
            rows = []
            i = 0
            while i < len(hunk.rows):
                if hunk.rows[i][0] == " ":
                    rows.append(hunk.rows[i])
                    i += 1
                    continue
                start = i
                while i < len(hunk.rows) and hunk.rows[i][0] != " ":
                    i += 1
                block = hunk.rows[start:i]
                if all(not text.strip() or text.lstrip().startswith("#") for _, text in block):
                    rows.extend((" ", text) for kind, text in block if kind == "-")
                    changed = True
                else:
                    rows.extend(block)
            hunks.append(Hunk(hunk.start, rows))
        return Change(self.old, self.new, hunks) if changed else self

    def apply(self, source):
        # Split on LF only. A form feed in Python source is not a Git line break.
        lines = [line + "\n" for line in source.split("\n")]
        lines[-1] = lines[-1][:-1]
        if not lines[-1]:
            lines.pop()
        out = []
        cursor = 0
        for hunk in self.hunks:
            if not cursor <= hunk.start <= len(lines):
                raise UnsupportedPatch("invalid or overlapping hunk")
            out.extend(lines[cursor : hunk.start])
            cursor = hunk.start
            for kind, line in hunk.rows:
                if kind != "+":
                    if cursor >= len(lines) or line != lines[cursor]:
                        raise UnsupportedPatch("context differs from pinned source")
                    cursor += 1
                if kind != "-":
                    out.append(line)
        out.extend(lines[cursor:])
        return "".join(out)


def read_patch(text):
    lines = [line + "\n" for line in text.split("\n")]
    lines[-1] = lines[-1][:-1]
    i = 0
    changes = []
    change = None
    pending = False
    while i < len(lines):
        row = lines[i]
        if row.startswith("diff --git "):
            if pending:
                raise UnsupportedPatch("metadata-only or binary change")
            pending = True
            change = None
        elif row.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            change = Change(path_from_header(row[4:]), path_from_header(lines[i + 1][4:]))
            if not (change.old or change.new):
                raise UnsupportedPatch("missing both paths")
            changes.append(change)
            pending = False
            i += 1
        elif row.startswith("@@"):
            m = HUNK.match(row)
            if m is None or change is None:
                raise UnsupportedPatch("invalid hunk header")
            old_start, old_count, _, new_count = m.groups()
            deleted = int(old_count) if old_count is not None else 1
            added = int(new_count) if new_count is not None else 1
            hunk = Hunk(int(old_start) - int(deleted != 0))
            change.hunks.append(hunk)
            while deleted or added:
                i += 1
                if i >= len(lines) or lines[i][:1] not in (" ", "+", "-"):
                    raise UnsupportedPatch("truncated hunk")
                kind, content = lines[i][0], lines[i][1:]
                if i + 1 < len(lines) and lines[i + 1].startswith(NO_NEWLINE):
                    content = content.removesuffix("\n")
                    i += 1
                hunk.rows.append((kind, content))
                deleted -= kind != "+"
                added -= kind != "-"
                if deleted < 0 or added < 0:
                    raise UnsupportedPatch("invalid hunk length")
        i += 1
    if pending or not changes or any(not c.hunks for c in changes):
        raise UnsupportedPatch("unsupported file metadata or missing edits")
    return changes
