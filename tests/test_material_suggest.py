"""'Did you mean ...?' for materials that are not in the ASME tables (reads the real asme table, read-only)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.asme import AsmeMaterialStress  # noqa: E402
from app.crud.asme_calculator import suggest_materials, get_materials_list  # noqa: E402


class Suggestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        if cls.db.query(func.count(AsmeMaterialStress.id)).scalar() == 0:
            raise unittest.SkipTest("asme_material_stress is empty")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def pairs(self, spec, grade):
        return [(m.clean_spec, m.clean_grade) for m in suggest_materials(self.db, spec, grade)]

    def test_sa_375_grade_c_gets_close_grade_c_materials(self):
        # SA-375 does not exist in the book. Grade-C materials with a similar number are offered
        # (the user decides: SA-285 C plate, SA-372 C forging ...)
        self.assertNotIn("SA-375", {m.clean_spec for m in get_materials_list(self.db)})
        got = self.pairs("SA-375", "Grade C")
        for expected in (("SA-285", "C"), ("SA-372", "C")):
            self.assertIn(expected, got)
        self.assertTrue(all(g == "C" for _, g in got[:4]))

    def test_nothing_is_suggested_for_garbage(self):
        self.assertEqual(self.pairs("XYZ", "Grade C"), [])
        self.assertEqual(self.pairs("SA-99999", "ZZ9"), [])

    def test_known_material_suggests_itself_first(self):
        self.assertEqual(self.pairs("SA-516", "70")[0], ("SA-516", "70"))

    def test_limit_and_no_duplicates(self):
        got = self.pairs("SA-375", "Grade C")
        self.assertLessEqual(len(got), 6)
        self.assertEqual(len(got), len(set(got)))


if __name__ == "__main__":
    unittest.main()
