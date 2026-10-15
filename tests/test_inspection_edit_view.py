"""The zones of a saved inspection (what the Edit form shows) carry their t_min, like the new-inspection form (own temporary database).

Before, Edit showed "t_min: Not set in Equipment Profile" for a zone whose t_min is set.
Run from backend/:  python -m unittest tests.test_inspection_edit_view -v
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
from app.schemas.inspection import DynamicTMLReadingInput, InspectionRecordCreate  # noqa: E402
from app.crud import inspection as crud  # noqa: E402

BASE_URL = make_url(settings.DATABASE_URL)
TEST_DB = "ams_test_" + uuid.uuid4().hex[:8]


def zone(code, typ, vals, min_t=None):
    return DynamicTMLReadingInput(tml_code=code, tml_name=code, tml_type=typ, direction_labels="N,S,E,W",
                                  reading_1=vals[0], reading_2=vals[1], min_required_thickness=min_t)


class EditViewCarriesTmin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        admin.dispose()
        cls.engine = create_engine(BASE_URL.set(database=TEST_DB))
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        admin.dispose()

    def test_each_zone_shows_its_own_tmin_when_the_inspection_is_opened(self):
        db = sessionmaker(bind=self.engine)()
        try:
            eq = Equipment(equipment_id="V-1", equipment_name="t", equipment_type="Pressure Vessel", plant="P", unit="U",
                           min_required_thickness=D("8.0"), dish_min_thickness=D("7.0"), nominal_thickness=D("12"))
            db.add(eq)
            db.commit()
            db.refresh(eq)
            rec = crud.create_inspection_record(db, InspectionRecordCreate(
                equipment_id=eq.id, equipment_tag="V-1", inspection_date=datetime(2026, 1, 1), inspector_name="t",
                tml_readings=[zone("S-1", "Shell", (10.0, 10.1)), zone("D-1", "Dish", (9.0, 9.1)),
                              zone("N-1", "Nozzle", (6.0, 6.2), min_t=D("4.5")), zone("T-1", "Tube", (2.0, 2.1))]))
            shown = {z.tml_code: z.min_required_thickness for z in crud.get_inspection_record(db, rec.id).tml_readings}
            self.assertEqual(shown["S-1"], D("8.0"))        # the Equipment Register's Shell minimum
            self.assertEqual(shown["D-1"], D("7.0"))        # the Dish minimum
            self.assertEqual(shown["N-1"], D("4.5"))        # a Nozzle keeps its own
            self.assertIsNone(shown["T-1"])                 # the register has no Tube minimum: genuinely not set
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
