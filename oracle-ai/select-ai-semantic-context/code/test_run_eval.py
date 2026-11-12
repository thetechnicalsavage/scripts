#!/usr/bin/env python3
# v1.1 2026-11-11 - Codex r3: a fraction must match the percentage in percent units (0.2 is not 15.1%); outside the
#                    percentage question numbers must agree at 2 decimals (1.0 is not 1.04); approximate matching
#                    must not depend on row order (bipartite matching).
# v1.0 2026-11-11 - offline tests of run_eval.py's result matcher (Codex r2 finding 1). Run: python3 -m unittest test_run_eval
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_eval as R  # noqa: E402


class Matcher(unittest.TestCase):
    def test_count_is_not_a_percentage(self):
        self.assertFalse(R.rows_match([(1.0,)], [(100.0,)])[0])
        self.assertFalse(R.rows_match([(100.0,)], [(1.0,)])[0])     # v1.0 matched this (gold = generated x 100)

    def test_percentage_question_accepts_a_fraction(self):
        self.assertTrue(R.rows_match([(15.1,)], [(0.151,)], pct=True)[0])
        self.assertTrue(R.rows_match([(15.1,)], [(15.06,)], pct=True)[0])

    def test_extra_generated_column_allowed(self):
        self.assertTrue(R.rows_match([('CENTRAL',)], [('CENTRAL', 7.0)])[0])

    def test_one_column_cannot_serve_two_gold_columns(self):
        self.assertFalse(R.rows_match([(5.0, 5.0)], [(5.0, 6.0)])[0])

    def test_rows_must_line_up(self):
        self.assertFalse(R.rows_match([('EAST', 2.0), ('WEST', 1.0)], [('EAST', 1.0), ('WEST', 2.0)])[0])
        self.assertTrue(R.rows_match([('EAST', 2.0), ('WEST', 1.0)], [('WEST', 1.0), ('EAST', 2.0)])[0])

    def test_row_count_must_match(self):
        self.assertFalse(R.rows_match([(242.0,)], [(1.0,)] * 213)[0])

    def test_fraction_is_compared_in_percent_units(self):
        self.assertFalse(R.rows_match([(15.1,)], [(0.2,)], pct=True)[0])
        self.assertFalse(R.rows_match([(15.1,)], [(0.16,)], pct=True)[0])
        self.assertTrue(R.rows_match([(15.1,)], [(0.1506,)], pct=True)[0])

    def test_two_decimals_outside_percentages(self):
        self.assertFalse(R.rows_match([(1.0,)], [(1.04,)])[0])

    def test_matching_does_not_depend_on_order(self):
        gold, gen = [(1.000,), (1.008,)], [(1.004,), (0.996,)]
        self.assertTrue(R.rows_match(gold, gen)[0])
        self.assertTrue(R.rows_match(list(reversed(gold)), gen)[0])

    def test_rounding(self):
        self.assertTrue(R.rows_match([(17434.524577,)], [(17434.52,)])[0])
        self.assertFalse(R.rows_match([(17434.52,)], [(17434.60,)])[0])


if __name__ == '__main__':
    unittest.main()
