"""An inspection needs at least one reading and there is ONE inspection per equipment and date (own temporary database).

The client saved the form with every field empty (twice, same date) and got empty "Baseline" records.
Run from backend/:  python -m unittest tests.test_inspection_empty_save -v
"""
import os
import sys
import unittest
import uuid
from datetime import datetime
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models.equipment, app.models.inspection, app.models.cml  # noqa: E402,F401
from app.models.equipment import Equipment  # noqa: E402
from app.models.inspection import InspectionRecord  # noqa: E402
from app.models.cml import InspectionTML, InspectionReading  # noqa: E402
from app.schemas.inspection import DynamicTMLReadingInput, InspectionRecordCreate  # noqa: E402
from app.crud import inspection as crud  # noqa: E402

BASE_URL = make_url(settings.DATABASE_URL)
TEST_DB = "ams_test_" + uuid.uuid4().hex[:8]


def zone(code, vals):
    v = list(vals) + [None] * (4 - len(vals))
    return DynamicTMLReadingInput(tml_code=code, tml_name=code, tml_type="Shell", direction_labels="N,S,E,W",
                                  reading_1=v[0], reading_2=v[1], reading_3=v[2], reading_4=v[3])


class EmptySave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        admin.dispose()
        cls.engine = create_engine(BASE_URL.set(database=TEST_DB))
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        admin.dispose()

    def setUp(self):
        self.db = self.Session()
        for t in ("inspection_reading", "inspection_tml", "inspection_records", "equipment"):
            self.db.execute(text(f"DELETE FROM {t}"))
        self.db.commit()
        self.eq = Equipment(equipment_id="EMPTY-1", equipment_name="t", equipment_type="Pressure Vessel", plant="P", unit="U",
                            min_required_thickness=D("8.0"), nominal_thickness=D("12"))
        self.db.add(self.eq)
        self.db.commit()
        self.db.refresh(self.eq)

    def tearDown(self):
        self.db.close()

    def save(self, date, zones, **kw):
        return crud.create_inspection_record(self.db, InspectionRecordCreate(
            equipment_id=self.eq.id, equipment_tag="EMPTY-1", inspection_date=date, inspector_name="t", tml_readings=zones, **kw))

    def count(self):
        return self.db.query(InspectionRecord).count()

    def test_a_form_with_every_field_empty_creates_nothing(self):
        with self.assertRaises(ValueError) as cm:
            self.save(datetime(2026, 1, 1), [zone("S-1", [None] * 4), zone("S-2", [None] * 4)])
        self.assertIn("at least one thickness reading", str(cm.exception))
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.db.query(InspectionTML).count(), 0)          # no zone left behind either

    def test_a_form_without_any_zone_creates_nothing(self):
        with self.assertRaises(ValueError):
            self.save(datetime(2026, 1, 1), [])
        self.assertEqual(self.count(), 0)

    def test_the_same_zone_twice_on_the_same_date_is_refused(self):
        self.save(datetime(2026, 1, 1), [zone("S-1", [10.0, 10.1])])
        with self.assertRaises(ValueError) as cm:
            self.save(datetime(2026, 1, 1), [zone("S-1", [9.9, 10.0])])
        self.assertIn("S-1", str(cm.exception))
        self.assertIn("already have readings", str(cm.exception))
        with self.assertRaises(ValueError):                                # an empty form on that date: still refused
            self.save(datetime(2026, 1, 1), [zone("S-1", [None] * 4)])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.db.query(InspectionReading).count(), 1)      # the readings were not touched

    def test_a_different_zone_on_the_same_date_is_added_to_that_inspection(self):
        first = self.save(datetime(2026, 1, 1), [zone("D-1", [9.0, 9.2])])
        second = self.save(datetime(2026, 1, 1), [zone("S-1", [10.0, 10.1])], remarks="shell done later")
        self.assertEqual(self.count(), 1)                                   # ONE inspection for the date
        self.assertEqual(second.id, first.id)
        self.assertEqual({z.tml_code for z in second.tml_readings}, {"D-1", "S-1"})
        self.db.refresh(first)
        self.assertEqual(first.min_measured_thickness, D("9.0"))           # lowest of ALL readings of the date
        self.assertIn("shell done later", first.remarks)
        self.assertEqual(self.db.query(InspectionReading).count(), 2)

    def test_adding_zones_one_by_one_calculates_like_one_save(self):
        self.save(datetime(2025, 1, 1), [zone("S-1", [10.0, 10.0]), zone("S-2", [10.0, 10.0])])      # baseline
        one_by_one = None
        self.save(datetime(2026, 1, 1), [zone("S-1", [9.5, 9.9])])
        self.save(datetime(2026, 1, 1), [zone("S-2", [9.0, 9.8])])
        self.assertEqual(self.count(), 2)
        self.db.refresh(self.eq)
        one_by_one = (self.eq.remaining_life, self.eq.corrosion_rate)
        self.assertIsNotNone(one_by_one[0])
        # the same readings in one save give the same equipment result
        self.db.execute(text("DELETE FROM inspection_reading")); self.db.execute(text("DELETE FROM inspection_tml")); self.db.execute(text("DELETE FROM inspection_records"))
        self.db.commit()
        self.save(datetime(2025, 1, 1), [zone("S-1", [10.0, 10.0]), zone("S-2", [10.0, 10.0])])
        self.save(datetime(2026, 1, 1), [zone("S-1", [9.5, 9.9]), zone("S-2", [9.0, 9.8])])
        self.db.refresh(self.eq)
        self.assertEqual((self.eq.remaining_life, self.eq.corrosion_rate), one_by_one)

    def test_a_failed_merge_leaves_the_existing_inspection_untouched(self):
        rec = self.save(datetime(2026, 1, 1), [zone("S-1", [10.0, 10.1])])
        real = crud._sync_cml_readings

        def boom(*a, **k):
            raise RuntimeError("boom")

        crud._sync_cml_readings = boom
        try:
            with self.assertRaises(RuntimeError):
                self.save(datetime(2026, 1, 1), [zone("S-2", [9.0])])
        finally:
            crud._sync_cml_readings = real
        self.db.refresh(rec)
        self.assertEqual(self.count(), 1)
        self.assertEqual(rec.min_measured_thickness, D("10.0"))
        self.assertEqual({z.tml_code for z in self.db.query(InspectionTML).all()}, {"S-1"})

    def test_editing_cannot_remove_every_reading(self):
        rec = self.save(datetime(2026, 1, 1), [zone("S-1", [10.0, 10.1])])
        with self.assertRaises(ValueError) as cm:
            crud.update_inspection_record(self.db, rec.id, InspectionRecordCreate(
                equipment_id=self.eq.id, equipment_tag="EMPTY-1", inspection_date=datetime(2026, 1, 1), inspector_name="t",
                tml_readings=[zone("S-1", [None] * 4)]))
        self.assertIn("at least one thickness reading", str(cm.exception))
        self.db.refresh(rec)
        self.assertEqual(rec.min_measured_thickness, D("10.0"))             # unchanged
        self.assertEqual(self.db.query(InspectionReading).count(), 1)

    def test_one_reading_is_enough_and_other_dates_are_not_affected(self):
        self.save(datetime(2026, 1, 1), [zone("S-1", [10.0])])
        self.save(datetime(2027, 1, 1), [zone("S-1", [9.7])])
        self.assertEqual(self.count(), 2)

    def test_the_old_four_zone_fields_still_work(self):
        crud.create_inspection_record(self.db, InspectionRecordCreate(
            equipment_id=self.eq.id, equipment_tag="EMPTY-1", inspection_date=datetime(2026, 1, 1), inspector_name="t",
            s1_n=D("10.1"), s1_s=D("10.0")))
        self.assertEqual(self.count(), 1)
        with self.assertRaises(ValueError):
            crud.create_inspection_record(self.db, InspectionRecordCreate(
                equipment_id=self.eq.id, equipment_tag="EMPTY-1", inspection_date=datetime(2027, 1, 1), inspector_name="t"))

    def test_the_api_answers_400_with_the_message(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.database import get_db

        def override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override
        try:
            body = {"equipment_id": str(self.eq.id), "equipment_tag": "EMPTY-1", "inspection_date": "2026-01-01T00:00:00",
                    "inspector_name": "t", "tml_readings": [{"tml_code": "S-1", "tml_type": "Shell"}]}
            res = TestClient(app).post("/api/v1/inspection", json=body)
            self.assertEqual(res.status_code, 400, res.text)
            self.assertIn("at least one thickness reading", res.json()["detail"])
        finally:
            app.dependency_overrides.pop(get_db, None)


if __name__ == "__main__":
    unittest.main()
