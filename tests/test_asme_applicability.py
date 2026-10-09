"""Section VIII-1 applicability (NP / temperature limit), Table 1B temperatures and the PDF page parsers.

Read-only: uses the asme_material_stress table of the configured database and the client's ASME PDF.
Book references (ASME BPVC 2023 II-D, customary):
  SA-178 C (K03503) Table 1A lines 30, 31, 32 -> VIII-1: NP, NP, 1000 F
  SA-285 C Table 1A line 4                    -> VIII-1 limit 900 F
  SB-516 Table 1B line 35 (spec page 284)     -> 19.4 ksi up to 800 F, 19.0 @850, 13.6 @900, 9.0 @950, 1.7 @1200
Run from backend/:  python -m unittest tests.test_asme_applicability -v
"""
import os
import sys
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf  # noqa: E402
from sqlalchemy import func  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.asme import AsmeMaterialStress  # noqa: E402
from app.crud.asme_calculator import get_materials_list, get_stress_lookup, calculate_tmin  # noqa: E402
from app.schemas.asme import TminCalculateRequest  # noqa: E402
from app.seed.asme_applicability import parse_applicability_page  # noqa: E402
from app.seed.asme_stress_pages import parse_stress_page  # noqa: E402
from app.seed.seed_full_pdf import PDF_PATH  # noqa: E402


class Applicability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        if cls.db.query(func.count(AsmeMaterialStress.id)).scalar() == 0:
            raise unittest.SkipTest("asme_material_stress is empty")
        if cls.db.query(func.count(AsmeMaterialStress.viii1_permitted)).scalar() == 0:
            raise unittest.SkipTest("applicability columns are empty (run backfill_asme_applicability)")
        cls.all = get_materials_list(cls.db, include_not_permitted=True)
        cls.shown = get_materials_list(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def line(self, spec, grade, n, pool=None):
        return next(m for m in (pool or self.all) if (m.clean_spec, m.clean_grade, m.line_no) == (spec, grade, n))

    def look(self, m, t, unit="celsius"):
        return get_stress_lookup(self.db, m.spec_number, m.grade, m.product_form, D(t), unit)

    def request(self, m, t_c):
        return TminCalculateRequest(
            component_type="cylindrical_shell", design_pressure=D("10"), pressure_unit="bar", inside_diameter=D("1000"),
            dimension_unit="mm", design_temperature=D(t_c), temperature_unit="celsius", joint_efficiency=D("1"),
            spec_number=m.spec_number, grade=m.grade, product_form=m.product_form)

    # ---- NP -----------------------------------------------------------------------------------------------------
    def test_np_lines_are_not_offered(self):
        shown = {(m.clean_spec, m.clean_grade, m.line_no) for m in self.shown if (m.clean_spec, m.clean_grade) == ("SA-178", "C")}
        self.assertEqual(shown, {("SA-178", "C", 32)})            # lines 30 and 31 are NP in VIII-1
        self.assertEqual(len(self.all) - len(self.shown), 400)    # 330 in Table 1A + 70 in Table 1B

    def test_np_line_is_refused_by_lookup_and_calculation(self):
        m = self.line("SA-178", "C", 30)
        r = self.look(m, 150)
        self.assertEqual(r.status, "not_permitted")
        self.assertIsNone(r.stress_ksi)
        with self.assertRaises(ValueError) as cm:
            calculate_tmin(self.db, self.request(m, 150))
        self.assertIn("NP", str(cm.exception))

    def test_permitted_line_has_its_book_data(self):
        r = self.look(self.line("SA-178", "C", 32), 150)
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.viii1_limit_f, 1000)
        self.assertEqual((r.min_tensile_ksi, r.min_yield_ksi), (D("60"), D("37")))
        self.assertEqual(r.notes_text, "G3, G10, G24, S1, T2, W6")

    # ---- VIII-1 temperature limit -----------------------------------------------------------------------------
    def test_limit_of_section_viii1_is_the_maximum_temperature(self):
        m = self.line("SA-285", "C", 4)                     # limit 900 F
        ok = self.look(m, 900, "fahrenheit")
        self.assertEqual((ok.status, ok.stress_ksi, ok.max_temp_f), ("ok", D("5.900"), 900))
        over = self.look(m, 901, "fahrenheit")
        self.assertEqual(over.status, "above_max_temperature")
        self.assertIn("Section VIII", over.message)
        with self.assertRaises(ValueError):
            calculate_tmin(self.db, self.request(m, 483))   # 483 C = 901.4 F

    def test_limit_below_last_stress_column_is_respected(self):
        M = AsmeMaterialStress
        sp, gr, pf, lim, last = (self.db.query(M.spec_number, M.grade, M.product_form, M.viii1_limit_f, func.max(M.temp_f))
                                 .filter(M.viii1_permitted.is_(True))
                                 .group_by(M.spec_number, M.grade, M.product_form, M.viii1_limit_f)
                                 .having(func.max(M.temp_f) > M.viii1_limit_f).first())
        self.assertLess(lim, last)
        self.assertEqual(get_stress_lookup(self.db, sp, gr, pf, D(lim), "fahrenheit").status, "ok")
        self.assertEqual(get_stress_lookup(self.db, sp, gr, pf, D(lim + 5), "fahrenheit").status, "above_max_temperature")

    # ---- picker -----------------------------------------------------------------------------------------------
    def test_every_picker_name_is_unique(self):
        import re
        labels = [re.sub(r"[\s\-.,]+", "", m.display_label.lower()) for m in self.shown]   # punctuation is no difference
        self.assertEqual(len(labels), len(set(labels)))

    def test_lines_that_differ_only_in_punctuation_are_still_told_apart(self):
        # SA-240 316L lines 34 (note G5: higher stress values) and 35 (lower values) have the same name; the book's
        # composition text differs only by dashes ("16Cr 12Ni 2Mo" / "16Cr - 12Ni - 2Mo")
        a = self.line("SA-240", "316L", 34, self.shown)
        b = self.line("SA-240", "316L", 35, self.shown)
        self.assertIn("G5", a.display_label)
        self.assertIn("G21", b.display_label)

    def test_same_name_lines_are_told_apart_by_the_book_data(self):
        a = self.line("SA-1011", "45", 44, self.shown)
        b = self.line("SA-1011", "45", 38, self.shown)
        self.assertNotEqual(a.display_label, b.display_label)
        self.assertIn("tensile", a.display_label)

    # ---- the values the book prints ---------------------------------------------------------------------------
    def test_table_1b_values_sit_under_the_right_temperatures(self):
        m = self.sb516()
        got = {p.temp_f: p.stress_ksi for p in self.look(m, 100, "fahrenheit").table}
        self.assertEqual(got[800], D("19.4"))
        self.assertEqual((got[850], got[900], got[950], got[1000], got[1200]),
                         (D("19.0"), D("13.6"), D("9.0"), D("6.0"), D("1.7")))
        self.assertNotIn(350, got)                          # blank in the book for this material

    def sb516(self):
        return next(m for m in self.all if m.clean_spec == "SB-516" and m.line_no == 35 and m.pdf_page == 284)

    def test_table_1b_has_the_extra_columns(self):
        m = next(m for m in self.all if m.table_name == "Table 1B" and m.pdf_page == 212 and m.line_no == 1)
        got = {p.temp_f: p.stress_ksi for p in self.look(m, 100, "fahrenheit").table}
        self.assertEqual((got[350], got[400]), (D("1.6"), D("1.3")))

    def test_table_1a_950_and_1000_were_not_lost(self):
        m = next(m for m in self.all if m.table_name == "Table 1A" and m.pdf_page == 68 and m.line_no == 23)
        got = {p.temp_f: p.stress_ksi for p in self.look(m, 100, "fahrenheit").table}
        self.assertEqual((got[950], got[1000]), (D("4.0"), D("2.5")))


@unittest.skipUnless(os.path.exists(PDF_PATH), "ASME PDF not found")
class PdfParsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(PDF_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_strength_page_of_sa178(self):                  # PDF page 65 = spec page 64 + 1
        rows, why = parse_applicability_page(self.doc[64], "Table 1A")
        self.assertIsNone(why)
        self.assertEqual([(rows[n]["viii1_text"], rows[n]["viii1_permitted"]) for n in (30, 31, 32)],
                         [("NP", False), ("NP", False), ("1000", True)])
        self.assertEqual((rows[32]["min_tensile_ksi"], rows[32]["min_yield_ksi"]), (60.0, 37.0))
        self.assertEqual(rows[31]["notes_text"], "G10, S1, T1, W13")

    def test_strength_page_of_table_1b_has_size_range(self):    # PDF page 213
        rows, why = parse_applicability_page(self.doc[212], "Table 1B")
        self.assertIsNone(why)
        self.assertEqual((rows[1]["size_thickness"], rows[1]["viii1_limit_f"]), ("0.006-0.499", 400))

    def test_stress_page_uses_the_header_temperatures_of_table_1b(self):   # PDF pages 286 / 287
        rows, temps = parse_stress_page(self.doc[285])
        self.assertIn(350, temps)
        self.assertEqual(rows[35][900], (13.6, 1))
        self.assertNotIn(150, rows[35])                      # blank cell: no value, nothing shifts
        rows2, temps2 = parse_stress_page(self.doc[286])
        self.assertEqual(temps2[-1], 1800)
        self.assertEqual(rows2[35][1200], (1.7, 1))

    def test_stray_empty_row_does_not_hide_values(self):     # Table 1A page 71, line 23 also has a lone "23"
        rows, _ = parse_stress_page(self.doc[70])
        self.assertEqual((rows[23][950][0], rows[23][1000][0]), (4.0, 2.5))


if __name__ == "__main__":
    unittest.main()
