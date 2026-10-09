"""
Full ASME Table 1A & 1B Extractor and Database Seeder.
Reads directly from the PDF, matching rows across the 4-page cycles.

NOTE: this drops and rebuilds asme_material_stress and ends by running
app.seed.backfill_asme_metadata (clean identity columns). The old hand-typed
seed_asme_materials.py (wrong values) was removed.
"""
import sys
import os
import pymupdf
from decimal import Decimal
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from app.core.database import SessionLocal, engine
from app.models.asme import AsmeMaterialStress
from app.core.database import Base

PDF_PATH = r"C:\client\RBI\ASME BPVC 2023 Section II part D (customary).pdf"

def f_to_c(f: int) -> int:
    return int(round((f - 32) * 5 / 9))

def ksi_to_mpa(ksi: float) -> Decimal:
    return Decimal(str(round(ksi * 6.89476, 3)))

def ksi_to_bar(ksi: float) -> Decimal:
    return Decimal(str(round(ksi * 68.9476, 4)))

def ksi_to_psi(ksi: float) -> Decimal:
    return Decimal(str(round(ksi * 1000, 2)))

def extract_page_data(doc, page_num):
    """Extract rows that start with a line number 1-45."""
    page = doc[page_num - 1]
    blocks = page.get_text('dict')['blocks']
    
    items = []
    for block in blocks:
        if block['type'] == 0:
            for line in block['lines']:
                for span in line['spans']:
                    text = span['text'].strip()
                    if text:
                        # Clean special chars used as dashes
                        text = text.replace('\ufffd', '-').replace('\u2013', '-')
                        x0 = span['bbox'][0]
                        y0 = span['bbox'][1]
                        items.append({'text': text, 'x': x0, 'y': y0})
                        
    rows_by_y = {}
    for item in items:
        matched_y = None
        for y in rows_by_y.keys():
            if abs(y - item['y']) < 3:
                matched_y = y
                break
        if matched_y is None:
            matched_y = item['y']
            rows_by_y[matched_y] = []
        rows_by_y[matched_y].append(item)
        
    data_rows = {}
    for y in sorted(rows_by_y.keys()):
        row_items = sorted(rows_by_y[y], key=lambda i: i['x'])
        texts = [i['text'] for i in row_items]
        if texts:
            try:
                line_no = int(texts[0])
                if 1 <= line_no <= 45:
                    data_rows[line_no] = texts
            except ValueError:
                pass
    return data_rows

def clean_value(v):
    if v == '-' or v == '...' or v == '' or not v:
        return None
    try:
        return float(v)
    except:
        return None

def process_cycles(doc, start_page, end_page, table_name, db):
    """Process 4-page cycles"""
    # Verify we have a multiple of 4 pages
    total_pages = end_page - start_page + 1
    if total_pages % 4 != 0:
        print(f"Warning: Range for {table_name} is not a multiple of 4 pages!")
    
    cycles = total_pages // 4
    inserted = 0
    
    for cycle in range(cycles):
        base_pg = start_page + (cycle * 4)
        
        # Read the 4 pages
        pg_a = extract_page_data(doc, base_pg)       # Specs
        pg_b = extract_page_data(doc, base_pg + 1)   # Tensile/Yield
        pg_c = extract_page_data(doc, base_pg + 2)   # Temp 100-900
        pg_d = extract_page_data(doc, base_pg + 3)   # Temp 950-1650
        
        # Match lines
        for line_no in range(1, 46):
            if line_no not in pg_a:
                continue
                
            row_a = pg_a[line_no]
            row_b = pg_b.get(line_no, [])
            row_c = pg_c.get(line_no, [])
            row_d = pg_d.get(line_no, [])
            
            # Very basic parsing of row A
            # [1, 'Carbon', 'steel', 'Plate', 'SA-516', '70'] etc.
            # This requires careful stitching because spaces break words into items
            
            # Combine all text except line_no, find SA/SB
            full_str = " ".join(row_a[1:])
            
            # Simple heuristic for Spec: find SA- or SB-
            spec = ""
            import re
            match = re.search(r'(SA|SB|EN|JIS)\s*-\s*[\w\d\.]+', full_str.replace(' ', ''))
            if match:
                spec = match.group(0)
            else:
                # Try finding SA or SB
                parts = full_str.split()
                for i, p in enumerate(parts):
                    if p in ['SA', 'SB', 'SA-'] and i+1 < len(parts):
                        spec = p + parts[i+1]
                        break
            
            if not spec:
                continue # Skip if no spec found
                
            # If spec is found, let's grab it, we'll assign generic grade/product for now
            # to ensure the system is populated.
            spec = spec.replace(' ', '').replace('', '-')
            
            grade = ""
            product_form = "Product"
            material_group = "Ferrous" if table_name == "Table 1A" else "Non-Ferrous"
            
            # Tensile/Yield
            tensile = None
            yield_str = None
            if len(row_b) >= 3:
                tensile = clean_value(row_b[1])
                yield_str = clean_value(row_b[2])
                
            # Temperature parsing
            temps_c = [100, 150, 200, 250, 300, 400, 500, 600, 650, 700, 750, 800, 850, 900]
            temps_d = [950, 1000, 1050, 1100, 1150, 1200, 1250, 1300, 1350, 1400, 1450, 1500, 1550, 1600, 1650]
            
            stress_values = {}
            
            # Match columns to temps for Page C
            if row_c:
                col_idx = 1
                for t in temps_c:
                    if col_idx < len(row_c):
                        v = clean_value(row_c[col_idx])
                        if v: stress_values[t] = v
                    col_idx += 1
                    
            # Match columns to temps for Page D
            if row_d:
                col_idx = 1
                for t in temps_d:
                    if col_idx < len(row_d):
                        v = clean_value(row_d[col_idx])
                        if v: stress_values[t] = v
                    col_idx += 1
            
            if not stress_values:
                continue
                
            # Insert into DB
            for temp_f, stress_ksi in stress_values.items():
                row = AsmeMaterialStress(
                    spec_number=spec,
                    grade=f"L{line_no}_Pg{base_pg}", # Using Line_No as grade for uniqueness tracking right now
                    product_form=full_str[:70],      # Storing full string as product form for visibility
                    material_group=material_group,
                    code_edition="ASME 2023 Section II-D",
                    temp_f=temp_f,
                    temp_c=f_to_c(temp_f),
                    allowable_stress_ksi=Decimal(str(stress_ksi)),
                    allowable_stress_psi=ksi_to_psi(stress_ksi),
                    allowable_stress_mpa=ksi_to_mpa(stress_ksi),
                    allowable_stress_bar=ksi_to_bar(stress_ksi),
                    min_tensile_strength_ksi=Decimal(str(tensile)) if tensile else None,
                    min_yield_strength_ksi=Decimal(str(yield_str)) if yield_str else None,
                    notes=table_name
                )
                db.add(row)
                inserted += 1
                
        # Commit every cycle
        db.commit()
        print(f"  Processed {table_name} cycle {cycle+1}/{cycles} (Pages {base_pg}-{base_pg+3})")
        
    return inserted


def seed_all():
    # Setup DB
    Base.metadata.drop_all(bind=engine, tables=[AsmeMaterialStress.__table__])
    Base.metadata.create_all(bind=engine, tables=[AsmeMaterialStress.__table__])
    
    db = SessionLocal()
    doc = pymupdf.open(PDF_PATH)
    
    try:
        print("Starting Table 1A Extraction...")
        ins_1a = process_cycles(doc, 56, 211, "Table 1A", db)
        print(f"Table 1A: Inserted {ins_1a} stress records.")
        
        print("\nStarting Table 1B Extraction...")
        ins_1b = process_cycles(doc, 212, 343, "Table 1B", db)
        print(f"Table 1B: Inserted {ins_1b} stress records.")
        
        db.commit()
        # Fill clean material identity / source columns and recover rows with split descriptions
        from app.seed.backfill_asme_metadata import main as backfill
        backfill(apply=True)

        print("\nSUCCESS! Fully populated DB.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()
        doc.close()

if __name__ == "__main__":
    seed_all()
