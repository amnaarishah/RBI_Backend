"""Rev02 import when the Tank sheet names a tank that is not in the database (own temporary DB)."""
import os
import sys
import unittest
import uuid
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models.equipment, app.models.inspection, app.models.cml  # noqa: E402,F401
from app.models.equipment import Equipment  # noqa: E402
from app.crud.inspection_import import import_inspections_from_excel  # noqa: E402

REV02 = os.path.join(os.path.dirname(__file__), "..", "..", "Equipment Register-Rev02.xlsx")
BASE_URL = make_url(settings.DATABASE_URL)
TEST_DB = "ams_test_" + uuid.uuid4().hex[:8]


@unittest.skipUnless(os.path.exists(REV02), "Equipment Register-Rev02.xlsx not found")
class ImportWithoutTank(unittest.TestCase):
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

    def test_missing_tank_does_not_break_the_import(self):
        db = sessionmaker(bind=self.engine)()
        try:
            db.add(Equipment(equipment_id="00-V-15", equipment_name="x", equipment_type="Vessel", plant="P",
                             unit="U", min_required_thickness=D("9.5"), dish_min_thickness=D("9.5")))
            db.commit()
            res = import_inspections_from_excel(db, open(REV02, "rb").read(), "Equipment Register-Rev02.xlsx")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["records_created"], 3)       # only 00-V-15's three inspection dates
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
