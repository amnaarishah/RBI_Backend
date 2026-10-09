"""One unit system for the whole software: mm, bar, °C. Anything else is refused, never silently misread.

Run from backend/:  python -m unittest tests.test_unit_policy -v
"""
import io
import os
import sys
import unittest
import uuid
from datetime import datetime
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openpyxl  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core import unit_policy  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models.equipment, app.models.inspection, app.models.cml  # noqa: E402,F401
from app.models.equipment import Equipment  # noqa: E402
from app.models.inspection import InspectionRecord  # noqa: E402
from app.crud.inspection_import import import_inspections_from_excel  # noqa: E402
from app.schemas.cml import InspectionReadingCreate  # noqa: E402
from app.schemas.equipment import EquipmentCreate  # noqa: E402
from app.schemas.inspection import InspectionRecordCreate  # noqa: E402

REV02 = os.path.join(os.path.dirname(__file__), "..", "..", "Equipment Register-Rev02.xlsx")


class Policy(unittest.TestCase):
    def test_accepted_spellings_become_the_one_canonical_unit(self):
        for v in ("mm", "MM", " mm ", "millimetre", "Millimeters"):
            self.assertEqual(unit_policy.length_unit(v), "mm")
        self.assertEqual(unit_policy.pressure_unit("Bar"), "bar")
        for v in ("°C", "C", "celsius", "°c", "�C"):
            self.assertEqual(unit_policy.temperature_unit(v), "°C")
        self.assertIsNone(unit_policy.length_unit(None))
        self.assertIsNone(unit_policy.length_unit("  "))

    def test_everything_else_is_refused_with_a_clear_message(self):
        for fn, bad in ((unit_policy.length_unit, "inch"), (unit_policy.length_unit, "m"),
                        (unit_policy.pressure_unit, "psi"), (unit_policy.pressure_unit, "MPa"),
                        (unit_policy.temperature_unit, "°F"), (unit_policy.temperature_unit, "K")):
            with self.assertRaises(ValueError) as cm:
                fn(bad)
            self.assertIn("mm, bar and °C only", str(cm.exception))


class Schemas(unittest.TestCase):
    BASE = dict(equipment_id="X-1", equipment_name="x", equipment_type="Vessel")

    def test_equipment_defaults_and_good_units(self):
        e = EquipmentCreate(**self.BASE)
        self.assertEqual((e.pressure_unit, e.temperature_unit, e.thickness_unit, e.diameter_unit), ("bar", "°C", "mm", "mm"))
        e = EquipmentCreate(**self.BASE, thickness_unit="MM", temperature_unit="C", pressure_unit="BAR")
        self.assertEqual((e.thickness_unit, e.temperature_unit, e.pressure_unit), ("mm", "°C", "bar"))

    def test_equipment_refuses_other_units(self):
        for field, bad in (("thickness_unit", "inch"), ("diameter_unit", "inch"), ("length_unit", "m"),
                           ("pressure_unit", "psi"), ("temperature_unit", "°F")):
            with self.assertRaises(ValidationError, msg=field):
                EquipmentCreate(**self.BASE, **{field: bad})

    def test_readings_refuse_inch(self):
        with self.assertRaises(ValidationError):
            InspectionReadingCreate(inspection_date=datetime(2026, 1, 1), reading_1=D("0.25"), unit="inch")
        self.assertEqual(InspectionReadingCreate(inspection_date=datetime(2026, 1, 1), unit="mm").unit, "mm")
        with self.assertRaises(ValidationError):
            InspectionRecordCreate(equipment_id=uuid.uuid4(), equipment_tag="X", inspection_date=datetime(2026, 1, 1),
                                   thickness_unit="inches")


@unittest.skipUnless(os.path.exists(REV02), "Equipment Register-Rev02.xlsx not found")
class ImportRefusesInches(unittest.TestCase):
    """A workbook row whose unit says inch must not be imported as if it were mm."""

    @classmethod
    def setUpClass(cls):
        base = make_url(settings.DATABASE_URL)
        cls.name = "ams_test_" + uuid.uuid4().hex[:8]
        cls.admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{cls.name}"'))
        cls.engine = create_engine(base.set(database=cls.name))
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{cls.name}" WITH (FORCE)'))
        cls.admin.dispose()

    def workbook_bytes(self, unit_for_00_v_15):
        wb = openpyxl.load_workbook(REV02)
        ws = wb["List of equipment"]
        for r in (18, 19, 20):                       # the 3 inspection rows of 00-V-15; column BU holds the unit
            ws[f"BU{r}"] = unit_for_00_v_15
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def run_import(self, unit):
        db = sessionmaker(bind=self.engine)()
        try:
            db.query(InspectionRecord).delete()
            db.query(Equipment).delete()
            db.add(Equipment(equipment_id="00-V-15", equipment_name="x", equipment_type="Vessel", plant="P", unit="U",
                             min_required_thickness=D("9.5"), dish_min_thickness=D("9.5")))
            db.commit()
            res = import_inspections_from_excel(db, self.workbook_bytes(unit), "Equipment Register-Rev02.xlsx")
            from app.models.cml import InspectionTML
            self.zone_codes = {z.tml_code for z in db.query(InspectionTML).all()}
            return res, db.query(InspectionRecord).count()
        finally:
            db.close()

    def test_inch_rows_are_refused_not_imported(self):
        res, records = self.run_import("inch")
        self.assertEqual(records, 0)
        self.assertTrue(any("'inch'" in e and "mm" in e for e in res["errors"]), res["errors"])

    def test_mm_rows_are_imported(self):
        res, records = self.run_import("mm")
        self.assertEqual(records, 3)
        # equipment that is not in the database is reported, and its readings are NOT given to 00-V-15
        self.assertTrue(res["errors"])
        self.assertTrue(all("not in the database" in e for e in res["errors"]), res["errors"])
        # 00-V-15 has the zones of its own block only (the next block, 00-E-12 with Tube / Channel Head, is not its own)
        self.assertEqual(self.zone_codes, {"Shell-1", "Shell 2", "Uppe Dish", "Lower Dish"})


if __name__ == "__main__":
    unittest.main()
