"""Inspection calculation tests — client sheet (Equipment Register Rev02) formulas.

Run from backend/:  python -m unittest discover -s tests -v

Sheet method:
  ST loss = Tp - Tf, LT loss = Ti - Tf
  ST CR = ST loss / ((d2 - d1)/365),  LT CR = LT loss / ((d2 - d0)/365)
  Controlling CR = MAX(ST CR, LT CR)
  Remaining life = (Tf - t_min) / Controlling CR   (tank bottom: CR x 2, API 653)
t_min is always the value stored in the software field (not a hardcoded sheet number).
"""
import os
import random
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.crud.cml import _compute_readings, calculate_api510_intervals  # noqa: E402

D0, D1, D2 = datetime(2020, 1, 1), datetime(2023, 2, 6), datetime(2026, 8, 8)


def vals(lst):
    return [D(str(x)) for x in lst] + [None] * (6 - len(lst))


def sheet_point(t, t1, t2, d, d1, d2, tmin):
    st = (t1 - t2) / ((d2 - d1).days / 365)
    lt = (t - t2) / ((d2 - d).days / 365)
    cr = max(st, lt)
    return st, lt, cr, (t2 - tmin) / cr


class SheetFormulaEquivalence(unittest.TestCase):
    def test_random_points_match_sheet(self):
        rnd = random.Random(7)
        worst = 0.0
        for _ in range(500):
            d = datetime(2018, 1, 1) + timedelta(days=rnd.randint(0, 800))
            d1 = d + timedelta(days=rnd.randint(200, 1200))
            d2 = d1 + timedelta(days=rnd.randint(200, 1500))
            t = round(rnd.uniform(8, 16), 2)
            t1 = round(t - rnd.uniform(0.05, 1.5), 2)
            t2 = round(t1 - rnd.uniform(0.02, 1.5), 2)
            tmin = round(rnd.uniform(3, max(3.1, t2 - 0.3)), 2)
            st, lt, cr, rl = sheet_point(t, t1, t2, d, d1, d2, tmin)
            r = _compute_readings(vals([t2]), vals([t]), vals([t1]), d, d1, d2, D(str(tmin)))
            worst = max(worst, abs(float(r[3][0]) - st), abs(float(r[4][0]) - lt),
                        abs(float(r[1][0]) - cr), abs(float(r[2][0]) - rl))
        self.assertLess(worst, 0.011)


class SheetCases(unittest.TestCase):
    def test_vessel_00_V_15_shell1(self):
        r = _compute_readings(vals([11.97, 11.95, 11.86, 11.17]),
                              vals([12.1, 12.11, 12.12, 12.09]),
                              vals([11.65, 11.94, 11.86, 12.03]), D0, D1, D2, D("9.5"))
        self.assertEqual([float(x) for x in r[2][:4]], [125.50, 101.15, 59.96, 6.80])
        self.assertAlmostEqual(float(r[6]), 6.80, places=2)
        self.assertEqual(r[8], 3)  # governing point index (270 deg)

    def test_heat_exchanger_00_E_12(self):
        shell = _compute_readings(vals([9.86, 9.88, 9.46, 9.52]), vals([10.06, 10.08, 10.08, 10.06]),
                                  vals([9.96, 9.98, 9.56, 9.54]), D0, D1, D2, D("8"))
        self.assertAlmostEqual(float(shell[6]), 15.55, places=2)
        tube = _compute_readings(vals([1.95]), vals([2.11]), vals([2.01]), D0, D1, D2, D("0.8"))
        self.assertEqual(tube[2][0], "47.48")

    def test_tank_bottom_x2_factor(self):
        r = _compute_readings(vals([5.79, 5.93, 5.91]), vals([6.15, 6.12, 6.08]),
                              vals([6.01, 6.03, 5.98]), D0, D1, D2, D("2.54"), is_tank_bottom=True)
        self.assertAlmostEqual(float(r[6]), 25.88, places=2)

    def test_tie_on_remaining_life_reports_the_fastest_corroding_point(self):
        # every point is already below t_min (life 0); the governing point must be the one with the highest CR
        d0, d2 = datetime(2020, 1, 1), datetime(2022, 1, 1)
        r = _compute_readings(vals([6.97, 5.08, 5.81, 5.97]), vals([6.98, 6.73, 5.98, 5.83]),
                              vals([6.98, 6.73, 5.98, 5.83]), d0, d0, d2, D("8"))
        self.assertEqual(r[2][:3], ["0.00", "0.00", "0.00"])
        self.assertEqual(r[8], 1)                                   # point S: loss 1.65 mm -> highest CR
        self.assertGreater(float(r[7]), 0.8)

    def test_no_tmin_gives_no_life(self):
        r = _compute_readings(vals([5.0]), vals([6.0]), vals([5.5]), D0, D1, D2, None)
        self.assertEqual(r[2][0], "-")


class Intervals(unittest.TestCase):
    def days(self, rl):
        n, e = calculate_api510_intervals(D(str(rl)), D2)
        return (n - D2).days, (e - D2).days

    def test_rules(self):
        self.assertEqual(self.days(1.0), (365, 365))          # RL < 2 -> full RL
        self.assertEqual(self.days(3.0), (730, 730))          # 2..4 -> 2 years
        self.assertEqual(self.days(8.0), (1460, 1460))        # >4 -> RL/2, external min(5, .)
        self.assertEqual(self.days(30.0), (3650, 1825))       # capped 10y, external 5y


if __name__ == "__main__":
    unittest.main()
