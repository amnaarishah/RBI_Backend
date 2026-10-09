"""End-to-end inspection flow on a TEMPORARY database (the real DB is never touched).

Creates a throw-away Postgres database on the same server, builds the tables from the
models, runs the create-inspection flow with client sheet data (00-V-15, 3 inspections)
and compares every stored result with an independent implementation of the sheet formulas.
t_min values below are only inputs for the test (the software always uses the Equipment
Profile field).

Run from backend/:  python -m unittest tests.test_inspection_flow -v
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

from app.core.config import settings  # noqa: E402

BASE_URL = make_url(settings.DATABASE_URL)
TEST_DB = "ams_test_" + uuid.uuid4().hex[:8]

from sqlalchemy.orm import sessionmaker  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models.equipment, app.models.inspection, app.models.cml  # noqa: E402,F401

# Own engine bound to the temporary database; the app's real engine is never used here
engine = create_engine(BASE_URL.set(database=TEST_DB), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from app.models.equipment import Equipment  # noqa: E402
from app.models.inspection import InspectionRecord  # noqa: E402
from app.models.cml import InspectionTML  # noqa: E402
from app.schemas.inspection import InspectionRecordCreate, DynamicTMLReadingInput  # noqa: E402
from app.crud import inspection as crud  # noqa: E402

DATES = [datetime(2020, 1, 1), datetime(2023, 2, 6), datetime(2026, 8, 8)]
# Sheet "00-V-15" readings, 0/90/180/270 per zone, for the 3 inspection dates
ZONES = {
    "S-1": ("Shell-1", "Shell", [[12.1, 12.11, 12.12, 12.09], [11.65, 11.94, 11.86, 12.03], [11.97, 11.95, 11.86, 11.17]]),
    "S-2": ("Shell 2", "Shell", [[12.16, 12.18, 12.17, 12.11], [12.08, 11.95, 11.94, 12.01], [12.06, 11.94, 11.91, 11.89]]),
    "D-1": ("Upper Dish", "Dish", [[12.1, 12.09, 12.08, 12.08], [11.95, 11.92, 11.86, 11.84], [11.77, 11.77, 11.78, 11.8]]),
    "D-2": ("Lower Dish", "Dish", [[12.08, 12.08, 12.08, 12.09], [11.65, 11.56, 11.46, 11.17], [11.56, 11.54, 11.41, 11.09]]),
}
SHELL_TMIN, DISH_TMIN = D("9.5"), D("9.5")


def sheet_life(readings, n, tmin):
    """Sheet rows 21-26 for point set at inspection index n (n>=1)."""
    ti, tp, tf = readings[0], readings[n - 1], readings[n]
    d0, dp, df = DATES[0], DATES[n - 1], DATES[n]
    out = []
    for i in range(4):
        st = (tp[i] - tf[i]) / ((df - dp).days / 365)
        lt = (ti[i] - tf[i]) / ((df - d0).days / 365)
        cr = max(st, lt)
        out.append(((tf[i] - float(tmin)) / cr, cr))
    return out


def setUpModule():
    admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()
    Base.metadata.create_all(bind=engine)


def tearDownModule():
    engine.dispose()
    admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    admin.dispose()


def payload(eq, n):
    return InspectionRecordCreate(
        equipment_id=eq.id, equipment_tag=eq.equipment_id, inspection_date=DATES[n],
        tml_readings=[
            DynamicTMLReadingInput(tml_code=code, tml_name=name, tml_type=typ,
                                   reading_1=D(str(r[n][0])), reading_2=D(str(r[n][1])),
                                   reading_3=D(str(r[n][2])), reading_4=D(str(r[n][3])))
            for code, (name, typ, r) in ZONES.items()
        ],
    )


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        cls.eq = Equipment(equipment_id="00-V-15", equipment_name="Test column", equipment_type="Vessel",
                           plant="P", unit="U", min_required_thickness=SHELL_TMIN,
                           dish_min_thickness=DISH_TMIN, nominal_thickness=D("14"))
        cls.db.add(cls.eq)
        cls.db.commit()
        cls.db.refresh(cls.eq)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_flow(self):
        db, eq = self.db, self.eq

        # ---- 1st inspection = baseline: NOTHING may be invented ----
        rec1 = crud.create_inspection_record(db, payload(eq, 0))
        db.refresh(eq)
        self.assertIsNone(rec1.remaining_life)
        self.assertIsNone(rec1.corrosion_rate)
        self.assertIsNone(eq.remaining_life)
        self.assertIsNone(eq.corrosion_rate)
        for tml in db.query(InspectionTML).filter_by(equipment_id=eq.id).all():
            self.assertEqual([r.status for r in tml.readings], ["BASELINE"])

        # ---- 2nd and 3rd inspection ----
        rec2 = crud.create_inspection_record(db, payload(eq, 1))
        rec3 = crud.create_inspection_record(db, payload(eq, 2))
        db.refresh(eq)
        for r in (rec1, rec2, rec3):
            db.refresh(r)

        def expected(n):
            worst = (1e9, 0)
            for code, (name, typ, r) in ZONES.items():
                tmin = DISH_TMIN if typ == "Dish" else SHELL_TMIN
                for life, cr in sheet_life(r, n, tmin):
                    if life < worst[0]:
                        worst = (life, cr)
            return worst

        life3, cr3 = expected(2)
        self.assertAlmostEqual(float(eq.remaining_life), life3, places=2)
        self.assertAlmostEqual(float(eq.corrosion_rate), cr3, places=3)
        self.assertAlmostEqual(float(rec3.remaining_life), life3, places=2)
        self.assertAlmostEqual(float(rec3.corrosion_rate), cr3, places=3)

        life2, cr2 = expected(1)               # 2nd inspection: ST = LT (only Ti and Tp)
        self.assertAlmostEqual(float(rec2.remaining_life), life2, places=2)
        self.assertIsNone(rec1.remaining_life)  # baseline record stays empty

        # component-wise life (shell / head) and next inspection date per API 510 rules
        shell3 = min(l for c, (n_, t, r) in ZONES.items() if t == "Shell" for l, _ in sheet_life(r, 2, SHELL_TMIN))
        head3 = min(l for c, (n_, t, r) in ZONES.items() if t == "Dish" for l, _ in sheet_life(r, 2, DISH_TMIN))
        self.assertAlmostEqual(float(eq.shell_controlling_life), shell3, places=2)
        self.assertAlmostEqual(float(eq.head_controlling_life), head3, places=2)
        rl = life3
        interval = rl if rl < 2 else 2 if rl <= 4 else min(10, rl / 2)
        self.assertEqual((eq.next_internal_inspection_date.replace(tzinfo=None) - DATES[2]).days, int(interval * 365))

        # ---- missing t_min must be visible, never silently defaulted ----
        eq.min_required_thickness = None
        db.commit()
        from app.crud.cml import recalculate_equipment_cmls
        recalculate_equipment_cmls(db, eq.id)
        shell_tml = db.query(InspectionTML).filter_by(equipment_id=eq.id, tml_code="S-1").one()
        self.assertEqual(shell_tml.readings[-1].status, "NO_TMIN")
        self.assertIsNone(shell_tml.readings[-1].controlling_rl)


class NozzleZoneTest(unittest.TestCase):
    """Nozzle / Other zones use the t_min entered on the zone, never the shell t_min."""

    def test_nozzle_uses_own_tmin(self):
        db = SessionLocal()
        try:
            eq = Equipment(equipment_id="N-1", equipment_name="Nozzle test", equipment_type="Vessel",
                           plant="P", unit="U", min_required_thickness=D("9.5"))
            db.add(eq)
            db.commit()
            db.refresh(eq)

            def rec(date, shell, noz):
                return InspectionRecordCreate(
                    equipment_id=eq.id, equipment_tag="N-1", inspection_date=date,
                    tml_readings=[
                        DynamicTMLReadingInput(tml_code="S-1", tml_type="Shell", reading_1=D(str(shell))),
                        DynamicTMLReadingInput(tml_code="N-1", tml_type="Nozzle", reading_1=D(str(noz)),
                                               min_required_thickness=D("3.0")),
                    ])

            crud.create_inspection_record(db, rec(DATES[0], 12.0, 6.0))
            crud.create_inspection_record(db, rec(DATES[2], 11.0, 5.0))
            noz = db.query(InspectionTML).filter_by(equipment_id=eq.id, tml_code="N-1").one()
            shell = db.query(InspectionTML).filter_by(equipment_id=eq.id, tml_code="S-1").one()
            self.assertEqual(float(noz.min_required_thickness), 3.0)     # not 9.5
            self.assertEqual(float(shell.min_required_thickness), 9.5)
            years = (DATES[2] - DATES[0]).days / 365
            self.assertAlmostEqual(float(noz.readings[-1].controlling_rl), (5.0 - 3.0) / (1.0 / years), places=2)

            noz.min_required_thickness = None                            # no t_min -> visible, not borrowed
            db.commit()
            from app.crud.cml import recalculate_equipment_cmls
            recalculate_equipment_cmls(db, eq.id)
            noz = db.query(InspectionTML).filter_by(equipment_id=eq.id, tml_code="N-1").one()
            self.assertEqual(noz.readings[-1].status, "NO_TMIN")
        finally:
            db.close()


class FailedSyncTest(unittest.TestCase):
    """If the calculation step fails, the create must fail loudly and leave nothing behind."""

    def test_failed_sync_is_undone(self):
        from unittest import mock
        db = SessionLocal()
        try:
            eq = Equipment(equipment_id="F-1", equipment_name="Failure test", equipment_type="Vessel",
                           plant="P", unit="U", min_required_thickness=D("5"))
            db.add(eq)
            db.commit()
            db.refresh(eq)
            with mock.patch("app.crud.cml.add_reading_to_tml", side_effect=ValueError("boom")):
                with self.assertRaises(RuntimeError):
                    crud.create_inspection_record(db, payload_for(eq))
            self.assertEqual(db.query(InspectionRecord).filter_by(equipment_id=eq.id).count(), 0)
            self.assertEqual(db.query(InspectionTML).filter_by(equipment_id=eq.id).count(), 0)
        finally:
            db.close()


def payload_for(eq):
    return InspectionRecordCreate(
        equipment_id=eq.id, equipment_tag=eq.equipment_id, inspection_date=DATES[0],
        tml_readings=[DynamicTMLReadingInput(tml_code="S-1", tml_type="Shell", reading_1=D("6.0"))])


class DeleteLastRecordTest(unittest.TestCase):
    """Deleting the last inspection must clear every derived value (no stale dates / lives)."""

    def test_everything_derived_is_cleared(self):
        db = SessionLocal()
        try:
            eq = Equipment(equipment_id="D-1", equipment_name="Delete test", equipment_type="Vessel",
                           plant="P", unit="U", min_required_thickness=D("5"))
            db.add(eq)
            db.commit()
            db.refresh(eq)

            def rec(date, t):
                return InspectionRecordCreate(
                    equipment_id=eq.id, equipment_tag="D-1", inspection_date=date,
                    tml_readings=[DynamicTMLReadingInput(tml_code="S-1", tml_type="Shell", reading_1=D(str(t)))])

            crud.create_inspection_record(db, rec(DATES[0], 10.0))
            last = crud.create_inspection_record(db, rec(DATES[2], 9.0))
            db.refresh(eq)
            self.assertIsNotNone(eq.remaining_life)
            self.assertIsNotNone(eq.next_internal_inspection_date)
            first = db.query(InspectionRecord).filter_by(equipment_id=eq.id).order_by(InspectionRecord.inspection_date).first()
            crud.delete_inspection_record(db, last.id)
            crud.delete_inspection_record(db, first.id)
            db.refresh(eq)
            for field in ("remaining_life", "corrosion_rate", "shell_controlling_life", "head_controlling_life",
                          "next_internal_inspection_date", "next_external_inspection_date", "inspection_date_current"):
                self.assertIsNone(getattr(eq, field), field)
        finally:
            db.close()


class UpdateAndLegacyTest(unittest.TestCase):
    """Editing an inspection and the old four-zone (S-1 / S-2 / D-1 / D-2) input path."""

    def make_eq(self, tag):
        db = SessionLocal()
        eq = Equipment(equipment_id=tag, equipment_name=tag, equipment_type="Vessel", plant="P", unit="U",
                       min_required_thickness=D("5"), dish_min_thickness=D("6"))
        db.add(eq)
        db.commit()
        db.refresh(eq)
        return db, eq

    def test_edit_changes_readings_and_moves_the_date(self):
        db, eq = self.make_eq("U-1")
        try:
            def rec(date, t):
                return InspectionRecordCreate(
                    equipment_id=eq.id, equipment_tag="U-1", inspection_date=date,
                    tml_readings=[DynamicTMLReadingInput(tml_code="S-1", tml_type="Shell", reading_1=D(str(t)))])

            crud.create_inspection_record(db, rec(DATES[0], 10.0))
            last = crud.create_inspection_record(db, rec(DATES[1], 9.0))
            zone = db.query(InspectionTML).filter_by(equipment_id=eq.id, tml_code="S-1").one()
            life_before = float(zone.readings[-1].controlling_rl)

            # same date, thinner reading -> shorter life
            crud.update_inspection_record(db, last.id, rec(DATES[1], 7.0))
            db.refresh(zone)
            self.assertLess(float(zone.readings[-1].controlling_rl), life_before)
            self.assertEqual(float(zone.readings[-1].reading_1), 7.0)

            # move the inspection to another date: the reading of the old date must be gone, none duplicated
            crud.update_inspection_record(db, last.id, rec(DATES[2], 7.0))
            db.expire_all()
            dates = [r.inspection_date.date() for r in db.query(InspectionTML).filter_by(
                equipment_id=eq.id, tml_code="S-1").one().readings]
            self.assertEqual(dates, [DATES[0].date(), DATES[2].date()])
            self.assertEqual(db.query(InspectionRecord).filter_by(equipment_id=eq.id).count(), 2)
            db.refresh(eq)
            self.assertIsNotNone(eq.remaining_life)
        finally:
            db.close()

    def test_legacy_four_zone_input(self):
        db, eq = self.make_eq("L-1")
        try:
            def rec(date, shell, dish):
                return InspectionRecordCreate(
                    equipment_id=eq.id, equipment_tag="L-1", inspection_date=date,
                    s1_n=D(str(shell)), s1_s=D(str(shell)), s1_e=D(str(shell)), s1_w=D(str(shell)),
                    d1_n=D(str(dish)), d1_s=D(str(dish)), d1_e=D(str(dish)), d1_w=D(str(dish)))

            crud.create_inspection_record(db, rec(DATES[0], 10.0, 10.0))
            r2 = crud.create_inspection_record(db, rec(DATES[2], 9.0, 8.0))
            zones = {z.tml_code: z for z in db.query(InspectionTML).filter_by(equipment_id=eq.id)}
            self.assertEqual(sorted(zones), ["D-1", "S-1"])
            self.assertEqual((zones["S-1"].tml_type, zones["D-1"].tml_type), ("Shell", "Dish"))
            self.assertEqual(float(zones["S-1"].min_required_thickness), 5.0)   # shell t_min
            self.assertEqual(float(zones["D-1"].min_required_thickness), 6.0)   # dish t_min
            db.refresh(r2)
            self.assertEqual(float(r2.s1_n), 9.0)                                # legacy columns are filled
            db.refresh(eq)
            self.assertEqual(float(eq.d1_w), 8.0)                                # and mirrored on the equipment
            self.assertIsNotNone(eq.shell_controlling_life)
            self.assertIsNotNone(eq.head_controlling_life)

            crud.update_inspection_record(db, r2.id, rec(DATES[2], 9.0, 7.0))    # legacy edit path
            db.expire_all()
            d1 = db.query(InspectionTML).filter_by(equipment_id=eq.id, tml_code="D-1").one()
            self.assertEqual(float(d1.readings[-1].reading_1), 7.0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
