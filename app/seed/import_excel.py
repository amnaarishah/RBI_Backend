"""
Excel Importer for AMS — Imports all 25 equipment records from Equipment Register-Rev01.xlsx into PostgreSQL.
Run: cd backend && uv run python -m app.seed.import_excel
"""
import sys
import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database import SessionLocal, engine, Base
from app.models.equipment import Equipment

EXCEL_PATH = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "Equipment Register-Rev01.xlsx"))


def parse_decimal(val):
    if val is None or val == "":
        return None
    val_str = str(val).strip().replace("Not written", "").replace("Not Written", "").replace("ATM", "1.013").replace("N/A", "").replace("na", "").replace("coling water", "").replace("water", "").replace("steam", "")
    val_str = val_str.strip()
    if not val_str:
        return None
    try:
        return Decimal(val_str)
    except (InvalidOperation, ValueError):
        return None


def parse_string(val):
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("n/a", "na", "not written", "none", "0"):
        if s == "0" and len(s) == 1:
            return "0"
    return s if s else None


def read_excel_rows(file_path):
    with zipfile.ZipFile(file_path) as z:
        strings_xml = z.read('xl/sharedStrings.xml')
        tree = ET.fromstring(strings_xml)
        strings = [elem.text if elem.text is not None else "" for elem in tree.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')]

        sheet_xml = z.read('xl/worksheets/sheet1.xml')
        stree = ET.fromstring(sheet_xml)

        rows = []
        for r in stree.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'):
            row_data = {}
            for c in r.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c'):
                t = c.attrib.get('t')
                v = c.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                val = v.text if v is not None else ''
                if t == 's' and val != '':
                    idx = int(val)
                    val = strings[idx] if idx < len(strings) else val
                ref = c.attrib.get('r')
                col_let = ''.join([ch for ch in ref if ch.isalpha()])
                row_data[col_let] = val
            rows.append(row_data)
        return rows


def import_excel():
    rows = read_excel_rows(EXCEL_PATH)
    # Equipment data rows start from row index 3
    eq_rows = [r for r in rows[3:] if 'B' in r and r['B'].strip()]

    db = SessionLocal()
    imported_count = 0

    try:
        for r in eq_rows:
            eq_id = r.get('B', '').strip()
            unit_no = r.get('C', '0').strip()
            eq_type_raw = r.get('D', 'Vessel').strip()
            sub_cat = r.get('E', '').strip()
            desc = r.get('H', '').strip() or f"{eq_type_raw} {eq_id}"
            year_val = parse_decimal(r.get('I', ''))
            orient = r.get('K', '').strip()

            criticality = "HIGH" if "Reactor" in desc or "Column" in sub_cat or "Boiler" in sub_cat else "MEDIUM"
            if "Tank" in eq_type_raw:
                eq_type = "Storage Tank"
            elif "Exchanger" in eq_type_raw:
                eq_type = "Heat Exchanger"
            elif "Column" in sub_cat:
                eq_type = "Column"
            elif "Reactor" in sub_cat:
                eq_type = "Reactor"
            elif "Boiler" in sub_cat:
                eq_type = "Boiler"
            else:
                eq_type = "Pressure Vessel"

            plant_name = f"Plant {int(unit_no):02d}" if unit_no.isdigit() and int(unit_no) > 0 else "Plant 01"
            unit_name = f"Process Unit {unit_no}" if unit_no != "0" else "Main Process Unit"

            # Parse thickness readings from Rev01 columns
            s1_n = parse_decimal(r.get('AZ'))
            s1_s = parse_decimal(r.get('BA'))
            s1_e = parse_decimal(r.get('BB'))
            s1_w = parse_decimal(r.get('BC'))

            s2_n = parse_decimal(r.get('BE'))
            s2_s = parse_decimal(r.get('BF'))
            s2_e = parse_decimal(r.get('BG'))
            s2_w = parse_decimal(r.get('BH'))

            d1_n = parse_decimal(r.get('BJ'))
            d1_s = parse_decimal(r.get('BK'))
            d1_e = parse_decimal(r.get('BL'))
            d1_w = parse_decimal(r.get('BM'))

            d2_n = parse_decimal(r.get('BO'))
            d2_s = parse_decimal(r.get('BP'))
            d2_e = parse_decimal(r.get('BQ'))
            d2_w = parse_decimal(r.get('BR'))

            # Corrosion rate / remaining life are NOT estimated by this importer. They are calculated
            # by the inspection engine (client sheet method) once inspections are logged.
            cr = None
            rl = None

            existing = db.query(Equipment).filter(Equipment.equipment_id == eq_id).first()
            if existing:
                existing.s1_n = s1_n
                existing.s1_s = s1_s
                existing.s1_e = s1_e
                existing.s1_w = s1_w
                existing.s2_n = s2_n
                existing.s2_s = s2_s
                existing.s2_e = s2_e
                existing.s2_w = s2_w
                existing.d1_n = d1_n
                existing.d1_s = d1_s
                existing.d1_e = d1_e
                existing.d1_w = d1_w
                existing.d2_n = d2_n
                existing.d2_s = d2_s
                existing.d2_e = d2_e
                existing.d2_w = d2_w
                existing.corrosion_rate = cr
                existing.remaining_life = rl
            else:
                item = Equipment(
                    equipment_id=eq_id,
                    equipment_name=f"{desc} ({eq_id})",
                    equipment_type=eq_type,
                    equipment_category=sub_cat or eq_type_raw,
                    asset_tag=f"TAG-{eq_id}",
                    orientation=orient.capitalize() if orient else "Horizontal",
                    plant=plant_name,
                    unit=unit_name,
                    area="Area A",
                    location=f"Unit {unit_no} Bay",
                    description=f"Industrial {eq_type} asset tag {eq_id} - {desc}",
                    status="ACTIVE",
                    criticality=criticality,
                    material=parse_string(r.get('Q')),
                    material_grade=parse_string(r.get('R')),
                    operating_pressure=parse_decimal(r.get('L')),
                    operating_temperature=parse_decimal(r.get('M')),
                    design_pressure=parse_decimal(r.get('N')),
                    design_temperature=parse_decimal(r.get('O')),
                    service=parse_string(r.get('P')),
                    diameter=parse_decimal(r.get('S')),
                    manway_size=parse_decimal(r.get('T')),
                    nominal_thickness=parse_decimal(r.get('U')),
                    min_required_thickness=parse_decimal(r.get('V')),
                    length=parse_decimal(r.get('W')),
                    dish_type=parse_string(r.get('X')),
                    dish_material=parse_string(r.get('Y')),
                    dish_grade=parse_string(r.get('Z')),
                    dish_nominal_thickness=parse_decimal(r.get('AA')),
                    dish_min_thickness=parse_decimal(r.get('AB')),
                    bottom_material=parse_string(r.get('AC')),
                    bottom_nominal_thickness=parse_decimal(r.get('AD')),
                    bottom_min_thickness=parse_decimal(r.get('AE')),
                    roof_material=parse_string(r.get('AF')),
                    roof_nominal_thickness=parse_decimal(r.get('AG')),
                    roof_min_thickness=parse_decimal(r.get('AH')),
                    floating_roof_min_thickness=parse_decimal(r.get('AI')),
                    floating_roof_thickness=parse_decimal(r.get('AJ')),
                    tube_operating_pressure=parse_decimal(r.get('AK')),
                    tube_operating_temperature=parse_decimal(r.get('AL')),
                    tube_design_pressure=parse_decimal(r.get('AM')),
                    tube_design_temperature=parse_decimal(r.get('AN')),
                    tube_service=parse_string(r.get('AO')),
                    tube_material=parse_string(r.get('AP')),
                    no_of_tubes=int(parse_decimal(r.get('AQ'))) if parse_decimal(r.get('AQ')) is not None else None,
                    tube_od=parse_decimal(r.get('AR')),
                    tube_length=parse_decimal(r.get('AS')),
                    tube_thickness=parse_decimal(r.get('AT')),
                    tube_min_thickness=parse_decimal(r.get('AU')),
                    has_insulation=parse_string(r.get('AW')),
                    insulation_thickness=parse_decimal(r.get('AX')),
                    s1_n=s1_n, s1_s=s1_s, s1_e=s1_e, s1_w=s1_w,
                    s2_n=s2_n, s2_s=s2_s, s2_e=s2_e, s2_w=s2_w,
                    d1_n=d1_n, d1_s=d1_s, d1_e=d1_e, d1_w=d1_w,
                    d2_n=d2_n, d2_s=d2_s, d2_e=d2_e, d2_w=d2_w,
                    corrosion_rate=cr,
                    remaining_life=rl,
                    manufacturing_year=int(year_val) if year_val else None,
                    created_by="Excel Auto-Importer",
                    updated_by="Excel Auto-Importer"
                )
                db.add(item)
            imported_count += 1

        db.commit()
        print(f"SUCCESS: Seeded Rev01 inspection data for all {imported_count} equipment assets in PostgreSQL database!")
    except Exception as e:
        db.rollback()
        print(f"ERROR during import: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import_excel()
