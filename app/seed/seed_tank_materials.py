"""Load the tank material table (data/tank_materials.json, read from the client's sheet) into the database.
Idempotent: a material that is already there is updated, never duplicated.

Usage (from backend/):
    python -m app.seed.seed_tank_materials
"""
import json
import os
from decimal import Decimal

from app.core.database import SessionLocal
from app.models.tank import TankMaterial

DATA = os.path.join(os.path.dirname(__file__), "data", "tank_materials.json")
FIELDS = ("spec", "grade", "is_unknown", "yield_mpa", "tensile_mpa", "s_lower_mpa", "s_upper_mpa",
          "st_lower_mpa", "st_upper_mpa", "sheet_row")
NUMERIC = {"yield_mpa", "tensile_mpa", "s_lower_mpa", "s_upper_mpa", "st_lower_mpa", "st_upper_mpa"}


def load_json():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def main():
    rows = load_json()
    db = SessionLocal()
    added = updated = 0
    try:
        existing = {m.name: m for m in db.query(TankMaterial).all()}
        for r in rows:
            vals = {k: (Decimal(str(r[k])) if k in NUMERIC and r[k] is not None else r[k]) for k in FIELDS}
            m = existing.get(r["name"])
            if m is None:
                db.add(TankMaterial(name=r["name"], **vals))
                added += 1
            else:
                for k, v in vals.items():
                    setattr(m, k, v)
                updated += 1
        db.commit()
        print(f"tank_material: {added} added, {updated} updated, {len(rows)} in the sheet")
    finally:
        db.close()


if __name__ == "__main__":
    main()
