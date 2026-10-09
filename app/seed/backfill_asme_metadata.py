"""Fill clean identity/source columns of asme_material_stress and add materials the first
extraction missed (rows whose description is split over several lines).

Idempotent. Existing stress values are NOT changed. Usage (from backend/):
    python -m app.seed.backfill_asme_metadata            # dry run (prints a report)
    python -m app.seed.backfill_asme_metadata --apply    # write to the database
"""
import re
import sys
import uuid
from decimal import Decimal

import pymupdf
from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.asme import AsmeMaterialStress
from app.seed.asme_parse import extract_rows, parse_material
from app.seed.seed_full_pdf import PDF_PATH, extract_page_data, clean_value, f_to_c, ksi_to_mpa, ksi_to_bar, ksi_to_psi

TABLES = {"Table 1A": (56, 211), "Table 1B": (212, 343)}
TEMPS_C = [100, 150, 200, 250, 300, 400, 500, 600, 650, 700, 750, 800, 850, 900]
TEMPS_D = [950, 1000, 1050, 1100, 1150, 1200, 1250, 1300, 1350, 1400, 1450, 1500, 1550, 1600, 1650]


def stress_values(row_c, row_d):
    out = {}
    for temps, row in ((TEMPS_C, row_c), (TEMPS_D, row_d)):
        for i, t in enumerate(temps):
            if row and i + 1 < len(row):
                v = clean_value(row[i + 1])
                if v:
                    out[t] = v
    return out


def stress_decimals(row_c, row_d):
    """{temp_f: number of decimals of the value exactly as printed in the book}, e.g. "20.0" -> 1, "22" -> 0."""
    out = {}
    for temps, row in ((TEMPS_C, row_c), (TEMPS_D, row_d)):
        for i, t in enumerate(temps):
            if row and i + 1 < len(row):
                tok = str(row[i + 1]).strip()
                if clean_value(tok):
                    out[t] = len(tok.split('.')[1]) if '.' in tok else 0
    return out


def fallback_row(page, line):
    """Value cells of a row whose pieces sit on slightly different y positions: take every
    span nearest to this line number and order them by x only."""
    import pymupdf  # noqa: F401
    spans = []
    for block in page.get_text('dict')['blocks']:
        if block['type'] != 0:
            continue
        for ln in block['lines']:
            for sp in ln['spans']:
                t = sp['text'].strip()
                if t:
                    spans.append((t, sp['bbox'][0], (sp['bbox'][1] + sp['bbox'][3]) / 2))
    anchors = {}
    for t, x, y in spans:
        if t.isdigit() and 1 <= int(t) <= 45:
            anchors.setdefault(int(t), []).append((x, y))
    if not anchors:
        return None
    col = max({round(x / 6) for v in anchors.values() for x, _ in v},
              key=lambda c: sum(1 for v in anchors.values() for x, _ in v if round(x / 6) == c))
    ys = {n: next(y for x, y in v if abs(round(x / 6) - col) <= 1) for n, v in anchors.items()
          if any(abs(round(x / 6) - col) <= 1 for x, _ in v)}
    if line not in ys:
        return None
    mine = [(x, t) for t, x, y in spans if min(ys, key=lambda k: abs(ys[k] - y)) == line]
    toks = [t for _, t in sorted(mine)]
    return toks if toks and toks[0] == str(line) else [str(line)] + [t for t in toks if t != str(line)]


def norm(s):
    return re.sub(r'[^A-Z0-9]', '', (s or '').upper())


def main(apply: bool):
    doc = pymupdf.open(PDF_PATH)
    db = SessionLocal()
    report = dict(updated_rows=0, updated_materials=0, inserted_materials=0, inserted_rows=0,
                  spec_changed=0, unresolved=[])
    try:
        for table, (a, b) in TABLES.items():
            group = "Ferrous" if table == "Table 1A" else "Non-Ferrous"
            for base in range(a, b + 1, 4):
                rows = extract_rows(doc[base - 1])
                pc, pd_ = extract_page_data(doc, base + 2), extract_page_data(doc, base + 3)
                for line in sorted(rows):
                    meta = parse_material([str(line)] + rows[line])
                    vals = stress_values(pc.get(line), pd_.get(line))
                    decs = stress_decimals(pc.get(line), pd_.get(line))
                    if not any(t <= 900 for t in vals):   # value cells split over several y positions
                        row_c, row_d = fallback_row(doc[base + 1], line), fallback_row(doc[base + 2], line)
                        fb = stress_values(row_c, row_d)
                        vals = {**fb, **vals}
                        decs = {**stress_decimals(row_c, row_d), **decs}
                    if not meta:
                        if vals:
                            report['unresolved'].append((table, base, line))
                        continue
                    key = f"L{line}_Pg{base}"
                    existing = (db.query(AsmeMaterialStress)
                                .filter(AsmeMaterialStress.notes == table, AsmeMaterialStress.grade == key).all())
                    common = dict(table_name=table, pdf_page=base, line_no=line, uns_no=meta['uns_no'],
                                  clean_spec=meta['clean_spec'], clean_grade=meta['clean_grade'],
                                  product_type=meta['product_type'], condition_text=meta['condition_text'],
                                  description=meta['description'])
                    if existing:
                        if norm(existing[0].spec_number) != norm(meta['clean_spec']):
                            report['spec_changed'] += 1
                        for r in existing:
                            for k, v in common.items():
                                setattr(r, k, v)
                            r.spec_number = meta['clean_spec']
                            r.product_form = meta['product_type']
                            if r.temp_f in decs:
                                r.stress_decimals = decs[r.temp_f]
                        report['updated_rows'] += len(existing)
                        report['updated_materials'] += 1
                        have = {r.temp_f for r in existing}
                        for temp_f, ksi in vals.items():     # add temperatures that were missing
                            if temp_f not in have:
                                db.add(AsmeMaterialStress(
                                    id=uuid.uuid4(), spec_number=meta['clean_spec'], grade=key,
                                    product_form=meta['product_type'], material_group=group,
                                    code_edition="ASME 2023 Section II-D", temp_f=temp_f, temp_c=f_to_c(temp_f),
                                    allowable_stress_ksi=Decimal(str(ksi)), allowable_stress_psi=ksi_to_psi(ksi),
                                    allowable_stress_mpa=ksi_to_mpa(ksi), allowable_stress_bar=ksi_to_bar(ksi),
                                    notes=table, stress_decimals=decs.get(temp_f), **common))
                                report['inserted_rows'] += 1
                    elif vals:
                        for temp_f, ksi in vals.items():
                            db.add(AsmeMaterialStress(
                                id=uuid.uuid4(), spec_number=meta['clean_spec'], grade=key,
                                product_form=meta['product_type'], material_group=group,
                                code_edition="ASME 2023 Section II-D", temp_f=temp_f, temp_c=f_to_c(temp_f),
                                allowable_stress_ksi=Decimal(str(ksi)), allowable_stress_psi=ksi_to_psi(ksi),
                                allowable_stress_mpa=ksi_to_mpa(ksi), allowable_stress_bar=ksi_to_bar(ksi),
                                notes=table, stress_decimals=decs.get(temp_f), **common))
                            report['inserted_rows'] += 1
                        report['inserted_materials'] += 1
            print(table, 'done')
        print({k: (v if k != 'unresolved' else len(v)) for k, v in report.items()})
        if report['unresolved']:
            print('lines with stress values but no readable specification:', report['unresolved'])
        if apply:
            db.commit()
            print('APPLIED')
        else:
            db.rollback()
            print('dry run — nothing written')
    finally:
        db.close()
        doc.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
