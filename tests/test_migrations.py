"""A database built ONLY by the migrations (as on Railway) must work like the development database.

The other tests create their tables from the models; this one runs `alembic upgrade head` on an empty temporary
database, so a column that the code uses but no migration creates is caught here.
Run from backend/:  python -m unittest tests.test_migrations -v
"""
import os
import subprocess
import sys
import unittest
import uuid
from datetime import datetime
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models.equipment, app.models.inspection, app.models.cml, app.models.asme, app.models.tank, app.models.user  # noqa: E402,F401
from app.models.equipment import Equipment  # noqa: E402
from app.schemas.inspection import DynamicTMLReadingInput, InspectionRecordCreate  # noqa: E402
from app.crud import inspection as crud  # noqa: E402

BACKEND = os.path.join(os.path.dirname(__file__), "..")


class MigratedDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = make_url(settings.DATABASE_URL)
        cls.name = "ams_mig_" + uuid.uuid4().hex[:8]
        cls.admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{cls.name}"'))
        cls.url = base.set(database=cls.name).render_as_string(hide_password=False)
        run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND,
                             env=dict(os.environ, DATABASE_URL=cls.url), capture_output=True, text=True)
        if run.returncode != 0:
            cls.tearDownClass()
            raise AssertionError("alembic upgrade head failed: " + run.stderr[-600:])
        cls.engine = create_engine(cls.url)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "engine", None) is not None:
            cls.engine.dispose()
        with cls.admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{cls.name}" WITH (FORCE)'))
        cls.admin.dispose()

    def test_every_column_of_the_code_exists_after_the_migrations(self):
        insp = inspect(self.engine)
        have_tables = set(insp.get_table_names())
        for table in Base.metadata.sorted_tables:
            self.assertIn(table.name, have_tables, f"table {table.name} is not created by the migrations")
            have = {c["name"] for c in insp.get_columns(table.name)}
            missing = sorted({c.name for c in table.columns} - have)
            self.assertEqual(missing, [], f"{table.name}: columns used by the code but not created by any migration")

    def test_saving_inspections_works_on_a_migrated_database(self):
        db = sessionmaker(bind=self.engine)()
        try:
            eq = Equipment(equipment_id="MIG-1", equipment_name="migrated", equipment_type="Pressure Vessel", plant="P", unit="U",
                           min_required_thickness=D("8.0"), nominal_thickness=D("12"))
            db.add(eq)
            db.commit()
            db.refresh(eq)
            for date, t in ((datetime(2024, 1, 1), D("11.9")), (datetime(2026, 1, 1), D("11.2"))):    # the 2nd save reads the 1st
                rec = crud.create_inspection_record(db, InspectionRecordCreate(
                    equipment_id=eq.id, equipment_tag="MIG-1", inspection_date=date, inspector_name="t",
                    tml_readings=[DynamicTMLReadingInput(tml_code="S-1", tml_type="Shell", reading_1=t, reading_2=t, reading_3=t, reading_4=t)]))
                self.assertIsNotNone(rec.id)
            db.refresh(eq)
            self.assertIsNotNone(eq.remaining_life)
        finally:
            db.close()


class SampleTemplateRoute(unittest.TestCase):
    def test_the_sample_excel_can_be_downloaded(self):
        from fastapi.testclient import TestClient
        from app.main import app
        res = TestClient(app).get("/api/v1/inspection/sample-template")
        self.assertEqual(res.status_code, 200, res.text[:200])      # it was taken for a record id (422) when declared after /{record_id}
        self.assertIn("spreadsheetml", res.headers["content-type"])
        self.assertTrue(res.content.startswith(b"PK"))              # an .xlsx file is a zip


class ErrorTextOutsideDevelopment(unittest.TestCase):
    def test_database_errors_are_not_shown_to_the_user_in_production(self):
        from app.crud.inspection import _error_detail
        old = settings.APP_ENV
        try:
            settings.APP_ENV = "production"
            self.assertEqual(_error_detail(Exception('column "x" does not exist')), "")
            settings.APP_ENV = "development"
            self.assertIn("does not exist", _error_detail(Exception('column "x" does not exist')))
        finally:
            settings.APP_ENV = old


if __name__ == "__main__":
    unittest.main()
