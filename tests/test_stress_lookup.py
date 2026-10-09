"""Allowable-stress lookup tests (ASME II-D Table 1A, extracted from the client's PDF).

Read-only: queries the existing asme_material_stress table of the configured database.
Reference values: ASME BPVC 2023 II-D Table 1A line 45 (SA-516 Gr.70 plate), ksi:
  100-500F 20.0 | 600F 19.4 | 650F 18.8 | 700F 18.1 | 750F 14.8 | 800F 12.0 | 850F 9.3 | 900F 6.7
Run from backend/:  python -m unittest tests.test_stress_lookup -v
"""
import os
import sys
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.asme import AsmeMaterialStress  # noqa: E402
from app.crud.asme_calculator import get_materials_list, get_stress_lookup, calculate_tmin  # noqa: E402
from app.schemas.asme import TminCalculateRequest  # noqa: E402

REFERENCE = {100: 20.0, 200: 20.0, 300: 20.0, 400: 20.0, 500: 20.0, 600: 19.4, 650: 18.8,
             700: 18.1, 750: 14.8, 800: 12.0, 850: 9.3, 900: 6.7}


# (spec, grade, PDF page, line) -> {design temperature C: ksi shown by the software}.
# No interpolation (client requirement): 150 C = 302 F uses the 400 F column, 330 C = 626 F uses the 650 F column.
NEXT_HIGHER = {
    ("SA-516", "70", 68, 45): {150: "20.0", 330: "18.8"},
    ("SA-285", "C", 60, 4): {150: "15.7", 330: "14.8"},
    ("SA-106", "B", 64, 16): {150: "17.1", 330: "17.1"},
    ("SA-312", "TP304L", 136, 15): {150: "11.7", 330: "10.2"},
    ("SA-240", "316L", 120, 34): {150: "15.7", 330: "13.7"},
    ("SA-240", "316L", 120, 35): {150: "11.7", 330: "10.2"},
    ("SA-302", "C", 108, 8): {150: "22.9", 330: "22.9"},
}


class StressLookup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        if cls.db.query(func.count(AsmeMaterialStress.id)).scalar() == 0:
            raise unittest.SkipTest("asme_material_stress is empty")
        cls.mat = next(m for m in get_materials_list(cls.db) if (m.clean_spec, m.clean_grade, m.uns_no) == ("SA-516", "70", "K02700"))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def look(self, t, unit="fahrenheit"):
        m = self.mat
        return get_stress_lookup(self.db, m.spec_number, m.grade, m.product_form, D(t), unit)

    def test_table_values_match_pdf(self):
        for temp_f, ksi in REFERENCE.items():
            r = self.look(temp_f)
            self.assertEqual(r.status, "ok")
            self.assertAlmostEqual(float(r.stress_ksi), ksi, places=2, msg=f"{temp_f}F")

    def test_330_celsius_is_626_fahrenheit(self):
        a = self.look(330, "celsius")
        b = self.look(626, "fahrenheit")
        self.assertEqual((a.temperature_f, a.stress_mpa), (b.temperature_f, b.stress_mpa))
        self.assertEqual(int(a.temperature_f), 626)
        wrong = self.look(330, "fahrenheit")                     # 330 F is a different (cooler) row
        self.assertNotEqual(wrong.temperature_f, a.temperature_f)
        self.assertGreater(float(wrong.stress_ksi), float(a.stress_ksi))      # SA-516 70: 20.0 at 330 F, 18.8 at 626 F (650 F column)

    def test_between_columns_the_next_higher_column_is_used(self):
        r = self.look(625)                       # between 600F (19.4) and 650F (18.8): 650F is used, no interpolation
        self.assertFalse(r.interpolated)
        self.assertEqual(r.table_temperature_f, 650)
        self.assertEqual(r.stress_ksi, D("18.800"))
        self.assertEqual(r.source, "ASME II-D 2023, Table 1A (45) · design 625°F → 650°F column")

    def test_all_units_come_from_the_column_value(self):
        r = self.look(626)
        self.assertEqual((r.stress_ksi, r.stress_psi), (D("18.800"), D("18800.00")))
        self.assertEqual(r.stress_mpa, D("129.621"))
        self.assertEqual(r.stress_bar, D("1296.2149"))

    def test_table_columns_are_not_marked(self):
        r = self.look(650)
        self.assertEqual((r.table_temperature_f, r.stress_ksi), (650, D("18.800")))
        self.assertEqual(r.source, "ASME II-D 2023, Table 1A (45) · 650°F")

    def test_just_above_a_column_goes_to_the_next_one(self):
        self.assertEqual(self.look(601).table_temperature_f, 650)
        self.assertEqual(self.look(600).table_temperature_f, 600)
        self.assertEqual(self.look(151).table_temperature_f, 200)     # SA-516 70 has a 150 and a 200 F column

    def test_blank_cell_in_the_book_is_skipped(self):
        # SA-240 316L line 34 has no 150 F and no 250 F value in the book: 160 F -> 200 F, 260 F -> 300 F
        m = next(x for x in get_materials_list(self.db) if (x.clean_spec, x.clean_grade, x.line_no) == ("SA-240", "316L", 34))
        g = lambda t: get_stress_lookup(self.db, m.spec_number, m.grade, m.product_form, D(t), "fahrenheit").table_temperature_f
        self.assertEqual((g(120), g(160), g(260)), (200, 200, 300))

    def test_tmin_uses_the_next_higher_column(self):
        m = self.mat
        res = calculate_tmin(self.db, TminCalculateRequest(
            component_type="cylindrical_shell", design_pressure=D("10"), pressure_unit="bar", inside_diameter=D("1000"),
            dimension_unit="mm", design_temperature=D("330"), temperature_unit="celsius", joint_efficiency=D("1"),
            spec_number=m.spec_number, grade=m.grade, product_form=m.product_form))
        # 626 F -> 650 F column: S = 18.8 ksi = 1296.21 bar -> t = 10*500 / (1296.2149 - 6)
        self.assertAlmostEqual(float(res.t_min_mm), 3.8753, places=4)
        self.assertEqual(res.stress_source, "ASME II-D 2023, Table 1A (45) · design 626°F → 650°F column")

    def test_seven_materials_next_higher_column(self):
        mats = get_materials_list(self.db)
        for (spec, grade, page, line), expected in NEXT_HIGHER.items():
            m = next(x for x in mats if (x.clean_spec, x.clean_grade, x.pdf_page, x.line_no) == (spec, grade, page, line))
            for t_c, ksi in expected.items():
                r = get_stress_lookup(self.db, m.spec_number, m.grade, m.product_form, D(t_c), "celsius")
                self.assertEqual(r.stress_ksi, D(ksi), f"{spec} {grade} line {line} @ {t_c}C")
                self.assertEqual(r.stress_bar, (D(ksi) * D("68.9476")).quantize(D("0.0001")))

    def test_below_table_range_uses_first_column(self):
        self.assertAlmostEqual(float(self.look(-20).stress_ksi), 20.0, places=2)

    def test_above_max_temperature_is_blocked(self):
        r = self.look(1200)
        self.assertEqual(r.status, "above_max_temperature")
        self.assertIsNone(r.stress_mpa)
        m = self.mat
        req = TminCalculateRequest(component_type="cylindrical_shell", design_pressure=D("10"),
                                   pressure_unit="bar", inside_diameter=D("1000"), dimension_unit="mm",
                                   design_temperature=D("1200"), temperature_unit="fahrenheit",
                                   joint_efficiency=D("1"), spec_number=m.spec_number, grade=m.grade,
                                   product_form=m.product_form)
        with self.assertRaises(ValueError):
            calculate_tmin(self.db, req)

    def test_source_has_table_page_line(self):
        r = self.look(302)
        self.assertEqual((r.table_name, r.line), ("Table 1A", 45))
        self.assertTrue(r.source.startswith("ASME II-D 2023, Table 1A (45)"))
        self.assertNotIn("line", r.source)

    def test_custom_stress_units_agree(self):
        m = self.mat
        res = []
        for val, unit in [(20, "ksi"), (20000, "psi"), (137.895, "mpa"), (1378.95, "bar")]:
            r = calculate_tmin(self.db, TminCalculateRequest(
                component_type="cylindrical_shell", design_pressure=D("10"), pressure_unit="bar",
                inside_diameter=D("1000"), dimension_unit="mm", design_temperature=D("150"),
                temperature_unit="celsius", joint_efficiency=D("1"), spec_number=m.spec_number,
                grade=m.grade, product_form=m.product_form, custom_stress=D(str(val)), custom_stress_unit=unit,
                custom_stress_source="client datasheet"))
            res.append(float(r.t_min_mm))
        self.assertLess(max(res) - min(res), 0.001)


if __name__ == "__main__":
    unittest.main()
