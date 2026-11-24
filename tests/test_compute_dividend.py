import csv
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = "date,code,name,close_before,reference,kind,cash_dividend,stock_per_1000\n"
VAL = "date,code,name,pe,pb,yield_pct,dividend_year,fiscal,dps\n"
DAY = "date,code,name,open,high,low,close,volume,value,transactions\n"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


class ComputeDividend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = cls.tmp.name
        vals = {"20261120": ("10", "1"), "20261123": ("", "1"), "20261124": ("12.5", "1.25")}
        for ds, (pe, pb) in vals.items():
            write(f"{d}/twse/{ds}.csv", DAY + f"{ds},9999,測試,100,100,100,100,300000,30000000,10\n")
            write(f"{d}/tpex/{ds}.csv", DAY)
            write(f"{d}/valuation/twse/{ds}.csv", VAL + f"{ds},9999,測試,{pe},{pb},5,114,115/3,\n")
            write(f"{d}/valuation/tpex/{ds}.csv", VAL)
        for y in range(2020, 2026):
            write(f"{d}/exright/twse_{y}.csv", EX + f"{y}0701,9999,測試,105,100,息,5,0\n")
            write(f"{d}/exright/tpex_{y}.csv", EX)
        write(f"{d}/exright/twse_2026.csv", EX)
        write(f"{d}/exright/tpex_2026.csv", EX)
        cls.run_out = subprocess.run([sys.executable, os.path.join(ROOT, "compute_dividend.py"), "--data", d,
                                      "--min-history", "1"], capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out(self, name):
        return os.path.join(self.d, "output", name)

    def test_runs(self):
        self.assertEqual(self.run_out.returncode, 0, self.run_out.stderr)

    def test_metrics(self):
        r = {x["code"]: x for x in rows(self.out("dividend_20261124.csv"))}["9999"]
        self.assertEqual(float(r["eps_ttm"]), 8)
        self.assertEqual(float(r["bvps"]), 80)
        self.assertEqual(float(r["yield_avg5"]), 5)
        self.assertEqual(int(r["paid_years"]), 6)
        self.assertEqual(float(r["cash_cv5"]), 0)
        self.assertEqual(float(r["pe_pctile"]), 100)

    def test_short_history_has_no_percentile(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "compute_dividend.py"), "--data", self.d],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = {x["code"]: x for x in rows(self.out("dividend_20261124.csv"))}["9999"]
        self.assertEqual((r["pe_pctile"], r["yield_pctile"], r["pb_q50"]), ("", "", ""))
        subprocess.run([sys.executable, os.path.join(ROOT, "compute_dividend.py"), "--data", self.d,
                        "--min-history", "1"], capture_output=True, text=True)

    def test_river_weekly(self):
        self.assertEqual([x["date"] for x in rows(self.out("river/9999.csv"))], ["20261120", "20261124"])

    def test_lists_and_history(self):
        self.assertIn("9999", [x["code"] for x in rows(self.out("dividend_list_G_20261124.csv"))])
        hist = [x for x in rows(self.out("dividend_history.csv")) if x["code"] == "9999"]
        self.assertEqual(sorted(x["year"] for x in hist), [str(y) for y in range(2020, 2026)])
        self.assertTrue(all(x["cash_adj"] == x["cash"] for x in hist))

    def test_summary_written(self):
        self.assertTrue(os.path.exists(self.out("dividend_summary_20261124.md")))


if __name__ == "__main__":
    unittest.main()
