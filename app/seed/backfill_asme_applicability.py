"""Fill the strength / Section VIII-1 applicability / notes columns of asme_material_stress and make the
allowable-stress rows agree with the book, using the temperatures printed in the header of each page.

  * Table 1A: every existing value must already equal the book; only values that were missing (a stray empty row
    hid the 950 F+ values of some lines) are added. A line whose existing values differ is left alone and reported.
  * Table 1B: its pages have extra columns (350, 450, 550 F, 1700-1800 F) that the first extraction did not know,
    so its values sat under the wrong temperatures. They are corrected line by line.

Usage (from backend/):
    python -m app.seed.backfill_asme_applicability            # dry run (report only)
    python -m app.seed.backfill_asme_applicability --apply    # write to the database
"""
import sys
import uuid
import collections
from decimal import Decimal

import pymupdf

from app.core.database import SessionLocal
from app.models.asme import AsmeMaterialStress as M
from app.seed.asme_applicability import parse_applicability_page
from app.seed.asme_stress_pages import parse_stress_page
from app.seed.seed_full_pdf import PDF_PATH, f_to_c, ksi_to_mpa, ksi_to_bar, ksi_to_psi

TABLES = {"Table 1A": (56, 211), "Table 1B": (212, 343)}
APPLIC_FIELDS = ("min_tensile_ksi", "min_yield_ksi", "viii1_limit_f", "viii1_permitted", "notes_text", "size_thickness")


def read_book():
    doc = pymupdf.open(PDF_PATH)
    applic, stress, problems = {}, {}, []
    for table, (a, b) in TABLES.items():
        for base in range(a, b + 1, 4):
            rows, why = parse_applicability_page(doc[base], table)
            if why:
                problems.append((table, base + 1, why))
            for ln, rec in rows.items():
                applic[(table, base, ln)] = rec
            for off in (2, 3):
                rows, temps = parse_stress_page(doc[base + off - 1])
                if not temps:
                    problems.append((table, base + off, "no temperature header"))
                for ln, vals in rows.items():
                    stress.setdefault((table, base, ln), {}).update(vals)
    doc.close()
    return applic, stress, problems


def set_stress(r, temp_f, ksi, dec):
    r.temp_f, r.temp_c = temp_f, f_to_c(temp_f)
    r.allowable_stress_ksi = Decimal(str(ksi))
    r.allowable_stress_psi, r.allowable_stress_mpa, r.allowable_stress_bar = ksi_to_psi(ksi), ksi_to_mpa(ksi), ksi_to_bar(ksi)
    r.stress_decimals = dec


def new_row(template, temp_f, ksi, dec):
    r = M()
    for col in M.__table__.columns.keys():
        setattr(r, col, getattr(template, col))
    r.id = uuid.uuid4()
    set_stress(r, temp_f, ksi, dec)
    return r


def main(apply: bool):
    applic, stress, problems = read_book()
    db = SessionLocal()
    rep = collections.Counter()
    notes = collections.defaultdict(list)
    try:
        groups = collections.defaultdict(list)
        for r in db.query(M).all():
            groups[(r.notes, r.pdf_page, r.line_no)].append(r)
        for key, rows in groups.items():
            table = key[0]
            a = applic.get(key)
            if a:
                for r in rows:
                    for f in APPLIC_FIELDS:
                        setattr(r, f, a[f])
                rep["lines with applicability"] += 1
            else:
                rep["lines WITHOUT applicability"] += 1
                notes["no applicability"].append(key)

            s = stress.get(key)
            if not s:
                rep["lines without parsed stress (left alone)"] += 1
                notes["no stress"].append(key)
                continue
            if any(v[0] is None for v in s.values()):
                rep["lines with an ambiguous stress cell (left alone)"] += 1
                notes["ambiguous"].append(key)
                continue
            have = {r.temp_f: r for r in rows}
            same = all(t in s and abs(float(r.allowable_stress_ksi) - s[t][0]) < 1e-9 for t, r in have.items())
            if table == "Table 1A":
                if not same:
                    rep["Table 1A lines with values differing from the book (left alone)"] += 1
                    notes["1A differs"].append(key)
                    continue
                for t, (ksi, dec) in s.items():
                    if t not in have:
                        db.add(new_row(rows[0], t, ksi, dec))
                        rep["Table 1A values added"] += 1
            else:
                if same and set(have) == set(s):
                    rep["Table 1B lines already right"] += 1
                    continue
                for t, r in have.items():
                    if t not in s:
                        db.delete(r)
                        rep["Table 1B wrong rows removed"] += 1
                for t, (ksi, dec) in s.items():
                    if t in have:
                        if abs(float(have[t].allowable_stress_ksi) - ksi) > 1e-9 or have[t].stress_decimals != dec:
                            set_stress(have[t], t, ksi, dec)
                            rep["Table 1B values corrected"] += 1
                    else:
                        db.add(new_row(rows[0], t, ksi, dec))
                        rep["Table 1B values added"] += 1
                rep["Table 1B lines corrected"] += 1
        print("pages with problems:", problems)
        for k, v in sorted(rep.items()):
            print(f"  {k}: {v}")
        for k, v in notes.items():
            print(f"  [{k}] {len(v)}: {v[:8]}")
        if apply:
            db.commit()
            print("APPLIED")
        else:
            db.rollback()
            print("dry run - nothing written")
    finally:
        db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
