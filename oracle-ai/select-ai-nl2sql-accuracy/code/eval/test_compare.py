#!/usr/bin/env python3
# v1.2 - unit tests for the result comparison in eval_nl2sql.py. No database needed.
#        v1.2: quoted database link form rejected.
#        v1.1: numeric strings, ordered questions, FOR UPDATE and dblink guards.
# Run: python3 -m unittest test_compare.py
import datetime as dt
import decimal
import unittest

from eval_nl2sql import clean_sql, matches, norm


def rows(*rs):
    return [tuple(norm(v) for v in r) for r in rs]


class Compare(unittest.TestCase):
    def test_same_rows_different_order(self):
        self.assertTrue(matches(rows(("a", 1), ("b", 2)), rows(("b", 2), ("a", 1))))

    def test_columns_swapped_and_extra_column(self):
        gold = rows(("Electronics", 10.5), ("Toys", 3))
        got = rows((3, "TOYS", "x"), (decimal.Decimal("10.50"), "electronics", "y"))
        self.assertTrue(matches(gold, got))

    def test_scalar_rounding(self):
        self.assertTrue(matches(rows((357829730,)), rows((decimal.Decimal("357829730.004"),))))

    def test_wrong_value_fails(self):
        self.assertFalse(matches(rows((357829730,)), rows((397491996.25,))))

    def test_extra_row_fails(self):
        self.assertFalse(matches(rows(("a", 1)), rows(("a", 1), ("b", 2))))

    def test_missing_column_fails(self):
        self.assertFalse(matches(rows(("a", 1)), rows((1,))))

    def test_columns_must_come_from_same_rows(self):
        # each column matches on its own, but the pairing is wrong
        gold = rows(("a", 1), ("b", 2))
        got = rows(("a", 2), ("b", 1))
        self.assertFalse(matches(gold, got))

    def test_dates_normalised(self):
        self.assertTrue(matches(rows((dt.date(2025, 4, 1),)), rows((dt.datetime(2025, 4, 1),))))

    def test_empty_results(self):
        self.assertTrue(matches([], []))
        self.assertFalse(matches(rows((1,)), []))


    def test_number_returned_as_text(self):
        self.assertTrue(matches(rows((600,)), rows(("600",))))

    def test_ordered_right_order(self):
        gold = rows(("A", 30), ("B", 20), ("C", 10))
        self.assertTrue(matches(gold, rows(("A", 30, 1), ("B", 20, 2), ("C", 10, 3)), ordered=True))

    def test_ordered_wrong_order_fails(self):
        gold = rows(("A", 30), ("B", 20), ("C", 10))
        got = rows(("C", 10), ("A", 30), ("B", 20))
        self.assertTrue(matches(gold, got))                   # unordered: fine
        self.assertFalse(matches(gold, got, ordered=True))    # ordered: rejected


class Guard(unittest.TestCase):
    def test_select_allowed(self):
        self.assertEqual(clean_sql("SELECT 1 FROM dual;"), "SELECT 1 FROM dual")

    def test_with_allowed_and_fence_removed(self):
        self.assertTrue(clean_sql("```sql\nWITH x AS (SELECT 1 a FROM dual) SELECT a FROM x\n```").startswith("WITH"))

    def test_dml_rejected(self):
        with self.assertRaises(ValueError):
            clean_sql("DELETE FROM ord_hdr")

    def test_two_statements_rejected(self):
        with self.assertRaises(ValueError):
            clean_sql("SELECT 1 FROM dual; DROP TABLE ord_hdr")

    def test_for_update_rejected(self):
        with self.assertRaises(ValueError):
            clean_sql("SELECT * FROM ord_hdr FOR UPDATE")

    def test_dblink_rejected(self):
        with self.assertRaises(ValueError):
            clean_sql("SELECT * FROM ord_hdr@remote_db")

    def test_quoted_dblink_rejected(self):
        with self.assertRaises(ValueError):
            clean_sql('SELECT * FROM ord_hdr@"REMOTE"')

    def test_at_sign_in_literal_allowed(self):
        self.assertTrue(clean_sql("SELECT 'a@b.com' FROM dual"))

    def test_semicolon_inside_literal_allowed(self):
        self.assertTrue(clean_sql("SELECT 'a;b' FROM dual"))


if __name__ == "__main__":
    unittest.main()
