"""Regression tests for scorer contracts and conservative selection boundaries."""

import ast
import json
import unittest

from comparisons import UNSUPPORTED, changed_comparison, outcome
from patches import UnsupportedPatch, read_patch
from selector import fit_output, schedule


class PatchTests(unittest.TestCase):
    def test_old_coordinates_across_hunks_and_missing_final_newline(self):
        patch = (
            "--- a/example.py\n+++ b/example.py\n@@ -1 +1,2 @@\n a\n+insert\n"
            "@@ -3 +4 @@\n-c\n\\ No newline at end of file\n+changed\n"
            "\\ No newline at end of file\n"
        )
        (change,) = read_patch(patch)
        self.assertEqual(change.apply("a\nb\nc"), "a\ninsert\nb\nchanged")
        self.assertEqual(list(change.edits()), [(2, 0, 1), (3, 1, 1)])

    def test_multiple_files_including_deletion(self):
        changes = read_patch(
            "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
            "--- a/b.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
        )
        self.assertEqual([(c.old, c.new) for c in changes], [("a.py", "a.py"), ("b.txt", None)])
        self.assertEqual(changes[1].apply("old\n"), "")

    def test_mismatch_truncation_and_outside_paths(self):
        (change,) = read_patch("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
        with self.assertRaises(UnsupportedPatch):
            change.apply("other\n")
        for patch in ("--- a/../x\n+++ b/x\n", "--- a/x\n+++ b/x\n@@ -2,4 +2 @@\n-a\n+b\n"):
            with self.assertRaises(UnsupportedPatch):
                read_patch(patch)


class ScheduleTests(unittest.TestCase):
    def test_empty_and_zero_cost(self):
        self.assertEqual(schedule(set(), {}), [])
        self.assertEqual(schedule({8, 3}, {8: 0, 3: 0}), [[3, 8]])

    def test_parallelism_must_pay_its_overhead(self):
        self.assertEqual(len(schedule({1, 2}, {1: 2, 2: 1000})), 1)
        durations = {9: 5900, 10: 3000, 12: 3000}
        plan = schedule(set(durations), durations)
        self.assertEqual(len(plan), 2)
        self.assertEqual(sorted(n for w in plan for n in w), sorted(durations))
        self.assertEqual(max(sum(durations[n] for n in w) for w in plan) + 2000 * len(plan), 10000)

    def test_output_budget_keeps_every_commit(self):
        entries = [("é", [list(range(200))]), ("x", [[3]])]
        rows = fit_output(entries, limit=90)
        self.assertLessEqual(sum(len(row.encode()) + 1 for row in rows), 90)
        self.assertEqual([json.loads(row)["commit"] for row in rows], ["é", "x"])


class ComparisonTests(unittest.TestCase):
    def test_one_operator_may_change(self):
        self.assertIsNotNone(
            changed_comparison(ast.parse("a = (x < 5)"), ast.parse("a = (x <= 5)"))
        )
        self.assertIsNone(changed_comparison(ast.parse("a = (x < 5)"), ast.parse("b = (x <= 5)")))
        self.assertIsNone(
            changed_comparison(ast.parse("x < 5 and y < 2"), ast.parse("x <= 5 and y == 2"))
        )

    def test_values_on_the_boundary_and_unknown_equality(self):
        self.assertFalse(outcome(ast.Lt(), ("int", 5), ("int", 5)))
        self.assertTrue(outcome(ast.LtE(), ("int", 5), ("int", 5)))
        self.assertIs(outcome(ast.Eq(), ("?",), ("none",)), UNSUPPORTED)
        self.assertIs(outcome(ast.Is(), ("plain-nonnull",), ("bool", False)), UNSUPPORTED)

    def test_object_identity_requires_evidence(self):
        self.assertIs(outcome(ast.Is(), ("str", "sql"), ("str", "sql")), UNSUPPORTED)
        self.assertIs(outcome(ast.Is(), ("plain-nonnull",), ("none",)), False)


if __name__ == "__main__":
    unittest.main()
