import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dividend_calc as dc  # noqa: E402

DETAIL_FIELDS = ["股票代號", "股票名稱", "(每股配發現金股利)除息", "(增資配股) 除權",
                 "A. 按普通股股東持股比例每千股無償配股", "B. 員工紅利轉增資", "C. (有償) 現金增資", "每股認購金額",
                 "a. 公開承銷", "b. 員工認購", " c. 原股東認購", "按股東持股比例每千股認購"]


def detail(cash, stock):
    return {"stat": "ok", "fields": DETAIL_FIELDS,
            "data": [["1773  ", "勝一            ", cash, "", stock, "0 股", "0 股", "0 元／股", "0 股", "0 股", "0 股",
                      "0.00000000 股"]]}


class SplitTwse(unittest.TestCase):
    def test_cash_only(self):
        self.assertEqual(dc.split_twse("息", 1065, 1060.49, 4.500020), (4.50002, 0))

    def test_stock_only(self):
        self.assertEqual(dc.split_twse("權", 22.0, 20.0, 2.0), (0, 100))

    def test_both_needs_detail(self):
        self.assertEqual(dc.split_twse("權息", 151, 123.75, 27.25), (None, None))

    def test_missing_values(self):
        self.assertEqual(dc.split_twse("權", None, 20.0, 2.0), (None, None))


class ParseDetail(unittest.TestCase):
    def test_cash_and_stock(self):
        self.assertEqual(dc.parse_detail(detail("2.5 元／股", "200 股")), (2.5, 200))

    def test_empty_cash_is_zero(self):
        self.assertEqual(dc.parse_detail(detail("", "200 股")), (0, 200))

    def test_not_found(self):
        self.assertEqual(dc.parse_detail({"stat": "很抱歉，沒有符合條件的資料!"}), (None, None))

    def test_missing_field(self):
        r = detail("2.5 元／股", "200 股")
        r["fields"] = r["fields"][:3]
        self.assertEqual(dc.parse_detail(r), (None, None))


class TwseRow(unittest.TestCase):
    def test_both_with_detail(self):
        self.assertEqual(dc.twse_row("權息", 151, 123.75, 27.25, (2.5, 200)), ("2.5", "200"))

    def test_both_without_detail(self):
        self.assertEqual(dc.twse_row("權息", 151, 123.75, 27.25, None), ("", ""))

    def test_cash_only(self):
        self.assertEqual(dc.twse_row("息", 1065, 1060.49, 4.500020, None), ("4.50002", "0"))


class Annual(unittest.TestCase):
    def test_annual_cash_sums_by_year(self):
        ev = [("20250612", 4.5), ("20250916", 5.0), ("20240613", 3.5), ("20240101", None)]
        self.assertEqual(dc.annual_cash(ev), {2025: (9.5, 2), 2024: (3.5, 1)})

    def test_cash_ttm_window(self):
        ev = [("20251124", 1.0), ("20251125", 2.0), ("20261124", 3.0), ("20261125", 9.0)]
        self.assertEqual(dc.cash_ttm(ev, "20261124"), 5.0)

    def test_yearly_stats_gap(self):
        st = dc.yearly_stats({2021: (1, 1), 2022: (1, 1), 2024: (2, 1), 2025: (2, 1)}, 2025)
        self.assertEqual(st["paid_years"], 2)
        self.assertAlmostEqual(st["cash_avg5"], 1.2)
        self.assertEqual(st["up_years5"], 4)

    def test_yearly_stats_empty(self):
        st = dc.yearly_stats({}, 2025)
        self.assertIsNone(st["cash_cv5"])
        self.assertEqual(st["paid_years"], 0)

    def test_yearly_stats_constant(self):
        st = dc.yearly_stats({y: (5, 1) for y in range(2019, 2026)}, 2025)
        self.assertEqual(st["cash_cv5"], 0)
        self.assertEqual(st["paid_years"], 7)


class Stats(unittest.TestCase):
    def test_percentile_rank(self):
        self.assertEqual(dc.percentile_rank([1, 2, 3, 4], 3), 75)

    def test_percentile_rank_empty(self):
        self.assertIsNone(dc.percentile_rank([], 3))

    def test_quantiles(self):
        self.assertEqual(dc.quantiles([5, 1, 3, 2, 4], (0, 50, 100)), [1, 3, 5])
        self.assertEqual(dc.quantiles([1, 2], (25,)), [1.25])

    def test_quantiles_empty(self):
        self.assertEqual(dc.quantiles([], (10, 50)), [None, None])

    def test_weekly_last(self):
        rows = [("20261117",), ("20261120",), ("20261123",), ("20261124",)]
        self.assertEqual(dc.weekly_last(rows), [("20261120",), ("20261124",)])


if __name__ == "__main__":
    unittest.main()
