"""Bulk import of inspection readings from Excel / CSV (client register layouts and the flat template).

Detects the layout, groups rows by equipment and date, validates equipment tags and creates or updates
inspection records through the same functions the screens use, so every calculation is identical.
"""
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import unit_policy
from app.models.inspection import InspectionRecord
from app.crud.inspection import create_inspection_record, update_inspection_record

logger = logging.getLogger("ams.import")


def import_inspections_from_excel(db: Session, file_contents: bytes, filename: str) -> dict:
    """Parse Excel (.xlsx) or CSV file and bulk import inspection records into DB.
    Automatically detects header row, matches flexible column names, groups rows by equipment & date,
    validates equipment tags, creates/updates records, provisions TMLs, and auto-recomputes API 510 rates and life."""
    import io
    import csv
    import re
    from openpyxl import load_workbook
    from app.models.equipment import Equipment
    from app.schemas.inspection import InspectionRecordCreate, DynamicTMLReadingInput

    EXCLUDE_TAG_KEYS = ["units", "unit", "equipmenttype", "vesseltype", "assettype", "equipmentcategory", "category", "equipmentdescription", "description", "location", "type", "componenttype"]
    TAG_KEYWORDS = [
        "equipmenttag", "tagno", "tagnum", "tag#", "assettag", "vesseltag", "equipmentid", "assetid", "tagname", "tag"
    ]
    # Removed 'year', 'time', 'inspected' which caused false matches on equipment design spec columns like 'Year'
    DATE_KEYWORDS = [
        "inspectiondate", "dateofinspection", "inspdate", "inspectiondt", "testdate", "surveydate", "readingdate",
        "examdate", "date"
    ]
    READING_KEYWORDS = [
        "tml", "cml", "reading", "thickness", "tp", "point", "shell", "dish", "head",
        "r1", "r2", "r3", "r4", "s1_n", "s1", "s2", "d1", "d2", "north", "south", "east", "west",
        "90", "180", "270"
    ]
    INSPECTOR_KEYWORDS = ["inspectorname", "inspector", "engineer", "inspectedby"]
    REMARKS_KEYWORDS = ["remarks", "notes", "comments", "description"]

    def norm(key):
        return "".join(c.lower() for c in str(key or "") if c.isalnum())

    # Pre-fetch all known equipment tags for fuzzy matching and header detection
    all_eqs = db.query(Equipment).all()
    tag_map = {eq.equipment_id.upper(): eq for eq in all_eqs}
    norm_tag_map = {norm(eq.equipment_id): eq for eq in all_eqs}

    def parse_decimal(val):
        if val is None or val == "":
            return None
        try:
            return Decimal(str(val).strip())
        except Exception:
            return None

    def parse_date(val):
        if isinstance(val, (datetime, date)):
            if isinstance(val, datetime):
                return val
            return datetime.combine(val, datetime.min.time())
        if val is None or val == "":
            return None
        s = str(val).strip()
        if "T" in s:
            s = s.split("T")[0]
        s_clean = s.replace(".", "-")
        for fmt in (
            "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d",
            "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
            "%d-%b-%Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y",
            "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%y", "%m/%d/%y"
        ):
            for target_str in (s, s_clean):
                try:
                    return datetime.strptime(target_str, fmt)
                except ValueError:
                    pass
        # Try Excel serial float number (serial > 25000 is year >= 1968)
        try:
            f = float(s)
            if f > 25000:
                return datetime(1899, 12, 30) + timedelta(days=f)
        except Exception:
            pass
        return None

    rows_data = []
    is_csv = filename.lower().endswith(".csv")

    if not is_csv:
        wb = load_workbook(filename=io.BytesIO(file_contents), data_only=True)

        # =========================================================================
        # ENGINE 1: Multi-Asset Equipment Register Parser
        # Automatically detects industrial equipment register workbooks where assets
        # and inspection campaigns are organized across rows (e.g. Equipment Register-Rev01.xlsx)
        # =========================================================================
        register_campaigns = []
        row_errors = []        # rows refused (unit is not mm) or skipped (equipment not in the database)
        unknown_reported = set()
        for sname in wb.sheetnames:
            cur_ws = wb[sname]
            cur_eq = None
            cur_unknown = None      # tag of an equipment block that is NOT in the database
            cur_zones = []

            for r in range(1, cur_ws.max_row + 1):
                row_vals = [cur_ws.cell(row=r, column=c).value for c in range(1, cur_ws.max_column + 1)]
                if not any(v is not None and str(v).strip() != "" for v in row_vals):
                    continue

                # Check if row declares an equipment tag
                declared = False
                for c_idx in range(1, min(15, len(row_vals) + 1)):
                    cell_val = row_vals[c_idx - 1]
                    if cell_val:
                        c_str = str(cell_val).strip().upper()
                        c_norm = norm(cell_val)
                        if c_str in tag_map:
                            cur_eq = tag_map[c_str]
                            cur_unknown = None
                            cur_zones = []
                            declared = True
                            break
                        elif c_norm in norm_tag_map:
                            cur_eq = norm_tag_map[c_norm]
                            cur_unknown = None
                            cur_zones = []
                            declared = True
                            break
                # A new equipment block (serial number in column A, tag in column B) whose tag is not in the
                # database: from here on its rows must NOT be read as belonging to the previous equipment.
                if (not declared and r > 1 and len(row_vals) > 1
                        and isinstance(row_vals[0], (int, float, str)) and str(row_vals[0]).strip().replace(".", "").isdigit()
                        and isinstance(row_vals[1], str) and row_vals[1].strip()):
                    cur_eq = None
                    cur_unknown = row_vals[1].strip()
                    cur_zones = []

                # Check if row defines zone headers (e.g. S-1, Shell-1, Shell 2, Dish, D-1,
                # Channel Head 1, Channel Head 2, Tube)
                EXCLUDE_ZONE_WORDS = [
                    "type", "unit", "material", "pressure", "temp", "category",
                    "service", "fluid", "data", "formula", "corrosion", "allowance",
                    "remaining", "short", "long", "loss", "thickness", "rate",
                    "life", "minimum", "exchanger"
                ]
                row_zones = []
                for c_idx in range(50, min(73, len(row_vals) + 1)):
                    cell_val = cur_ws.cell(row=r, column=c_idx).value
                    if cell_val and isinstance(cell_val, str):
                        c_raw = str(cell_val).strip()
                        c_norm = norm(cell_val)
                        if c_raw.lower() in ["d", "d1", "d2", "t", "t1", "t2", "units", "unit"]:
                            continue
                        is_zone = any(kw in c_norm for kw in [
                            "shell", "dish", "head", "tube",
                            "s1", "s2", "d1", "d2",
                            "channel", "channal"
                        ]) and not any(ex in c_norm for ex in EXCLUDE_ZONE_WORDS)
                        if is_zone:
                            code = c_raw
                            if "tube" in c_norm:
                                t_type = "Tube"
                            elif any(k in c_norm for k in ["dish", "head", "channel", "channal"]):
                                t_type = "Channel Head" if ("channel" in c_norm or "channal" in c_norm) else "Dish"
                            elif re.fullmatch(r"d\d+", c_norm):      # "D-1", "D2": dished end, not a shell
                                t_type = "Dish"
                            else:
                                t_type = "Shell"
                            # The cells right of the zone name hold its point labels (N,S,E,W / 0,90,180,270)
                            labels = []
                            for off in range(1, 5):
                                lv = cur_ws.cell(row=r, column=c_idx + off).value
                                if lv is None or str(lv).strip() == "":
                                    break
                                labels.append(f"{int(lv)}°" if isinstance(lv, (int, float)) else str(lv).strip())
                            row_zones.append({
                                "code": code,
                                "type": t_type,
                                "col_idx": c_idx,
                                "labels": ",".join(labels) if len(labels) >= 2 else None,
                            })
                if len(row_zones) >= 2:
                    cur_zones = row_zones

                # Check if row has an Inspection Date (priority: cols 74, 76, 75, 77, then others)
                insp_date = None
                date_col_idx = None
                priority_cols = [74, 76, 75, 77]
                other_cols = [c for c in range(1, len(row_vals) + 1) if c not in priority_cols]
                for c_idx in (priority_cols + other_cols):
                    if c_idx <= len(row_vals):
                        v = cur_ws.cell(row=r, column=c_idx).value
                        d = parse_date(v)
                        if d and d.year >= 1970:
                            insp_date = d
                            date_col_idx = c_idx
                            break

                # A unit written next to the readings of this row ("mm"). A known non-mm unit (inch, cm, m, ft ...) is
                # refused: the software works in mm only, so such readings must never be treated as mm.
                if insp_date and cur_unknown and cur_zones and cur_unknown not in unknown_reported:
                    unknown_reported.add(cur_unknown)
                    row_errors.append(
                        f"Sheet '{sname}' row {r}: equipment '{cur_unknown}' is not in the database, so its readings "
                        f"were not imported. Add the equipment first, then import again."
                    )

                if insp_date and cur_eq and cur_zones and date_col_idx:
                    bad_unit = next((
                        str(cur_ws.cell(row=r, column=c).value).strip()
                        for c in range(max(1, date_col_idx - 6), date_col_idx)
                        if isinstance(cur_ws.cell(row=r, column=c).value, str)
                        and cur_ws.cell(row=r, column=c).value.strip().lower() in unit_policy.NON_MM_LENGTH_UNITS
                    ), None)
                    if bad_unit:
                        row_errors.append(
                            f"Sheet '{sname}' row {r} ({cur_eq.equipment_id}): the unit is '{bad_unit}', but readings "
                            f"must be in mm. This inspection was not imported; please convert the readings to mm."
                        )
                        continue

                if insp_date and cur_eq and cur_zones:
                    tml_readings = []
                    for z in cur_zones:
                        c_base = z["col_idx"]
                        z_type = z["type"]

                        # Tube zone: single wall reading only (col_base + 1)
                        if z_type == "Tube":
                            r1 = parse_decimal(cur_ws.cell(row=r, column=c_base + 1).value)
                            if r1 is not None:
                                tml_readings.append(DynamicTMLReadingInput(
                                    tml_code=z["code"],
                                    tml_name=z["code"],
                                    tml_type=z_type,
                                    direction_labels="Wall",
                                    reading_1=r1,
                                ))
                        else:
                            # Shell / Channel Head: 4 directional readings
                            r1 = parse_decimal(cur_ws.cell(row=r, column=c_base + 1).value)
                            r2 = parse_decimal(cur_ws.cell(row=r, column=c_base + 2).value)
                            r3 = parse_decimal(cur_ws.cell(row=r, column=c_base + 3).value)
                            r4 = parse_decimal(cur_ws.cell(row=r, column=c_base + 4).value)
                            if any(x is not None for x in (r1, r2, r3, r4)):
                                tml_readings.append(DynamicTMLReadingInput(
                                    tml_code=z["code"],
                                    tml_name=z["code"],
                                    tml_type=z_type,
                                    direction_labels=z.get("labels") or "0°,90°,180°,270°",
                                    reading_1=r1,
                                    reading_2=r2,
                                    reading_3=r3,
                                    reading_4=r4
                                ))

                    if tml_readings:
                        remarks = None
                        inspector = "Lead Inspector"
                        if date_col_idx and date_col_idx < len(row_vals):
                            for off in (1, 2):
                                cand = cur_ws.cell(row=r, column=date_col_idx + off).value
                                if cand and isinstance(cand, str) and len(cand) > 1:
                                    remarks = cand
                                    break
                        register_campaigns.append({
                            "equipment": cur_eq,
                            "date": insp_date,
                            "inspector": inspector,
                            "remarks": remarks,
                            "tml_readings": tml_readings
                        })

        # =================================================================
        # ENGINE 1b: Tank Sheet Parser (API 653 Storage Tank)
        # Reads the dedicated 'Tank' sheet with its own zone layout:
        #   Shell Course 1 (cols 3-6), Shell Course 2 (cols 8-11),
        #   Shell Course 3 (cols 13-16), Roof (cols 18-21),
        #   Bottom (cols 23-25), Date (col 29)
        # Only rows 2-4 are data; rows 5-17 are formulas/notes.
        # =================================================================
        for sheet_name in wb.sheetnames:
            if sheet_name.lower() == 'tank':
                tank_ws = wb[sheet_name]
                # -------------------------------------------------------
                # Dynamic equipment matching:
                # 1st priority: Read Equipment_Tag from Col 27, Row 2
                #   (Client should fill this cell with the equipment tag,
                #    e.g. "05-TK-11". Col 27 Row 1 = header "Equipment_Tag")
                # 2nd priority: Match by sheet name (if sheet named like "05-TK-11")
                # 3rd priority: Fallback to any Storage Tank type in DB
                # -------------------------------------------------------
                tank_eq = None

                # Priority 1: Read tag from Excel Cell (Col 27, Row 2)
                tag_from_excel = tank_ws.cell(row=2, column=27).value
                if tag_from_excel:
                    tag_from_excel = str(tag_from_excel).strip().upper()
                    for eq in all_eqs:
                        if eq.equipment_id.upper() == tag_from_excel:
                            tank_eq = eq
                            break

                # Priority 2: Sheet name matches equipment_id
                if not tank_eq:
                    sheet_upper = sheet_name.strip().upper()
                    for eq in all_eqs:
                        if eq.equipment_id.upper() == sheet_upper:
                            tank_eq = eq
                            break

                # Priority 3: Fallback — any Storage Tank type equipment
                if not tank_eq:
                    for eq in all_eqs:
                        eq_type = (eq.equipment_type or '').lower()
                        eq_cat  = (eq.equipment_category or '').lower()
                        if 'tank' in eq_type or 'tank' in eq_cat:
                            tank_eq = eq
                            break

                if not tank_eq:
                    logger.warning(f"Tank sheet '{sheet_name}': No matching equipment found in DB. Skipping.")
                    continue

                # Dynamically extract component min thicknesses from sheet notes (e.g. rows 5-15)
                import re
                sheet_shell_min = None
                sheet_bottom_min = None
                sheet_roof_min = None

                for r_scan in range(1, 20):
                    for c_scan in range(1, 10):
                        cell_val = tank_ws.cell(row=r_scan, column=c_scan).value
                        if cell_val and isinstance(cell_val, str):
                            txt = cell_val.lower()
                            if 'minimum' in txt or 'thikness' in txt or 'thickness' in txt:
                                m_num = re.search(r'(?:shell|roof|bottom)[^0-9]*(\d+(?:\.\d+)?)', txt)
                                if not m_num:
                                    m_num = re.search(r'[:=]\s*(\d+(?:\.\d+)?)', txt)
                                if m_num:
                                    try:
                                        num_val = Decimal(m_num.group(1))
                                        if 'shell' in txt and sheet_shell_min is None:
                                            sheet_shell_min = num_val
                                        elif 'bottom' in txt and sheet_bottom_min is None:
                                            sheet_bottom_min = num_val
                                        elif 'roof' in txt and sheet_roof_min is None:
                                            sheet_roof_min = num_val
                                    except Exception:
                                        pass

                # Rule 2: Sync extracted sheet min thicknesses into equipment profile if not already set
                if sheet_shell_min is not None and not tank_eq.min_required_thickness:
                    tank_eq.min_required_thickness = sheet_shell_min
                if sheet_bottom_min is not None and not tank_eq.bottom_min_thickness:
                    tank_eq.bottom_min_thickness = sheet_bottom_min
                if sheet_roof_min is not None and not tank_eq.roof_min_thickness:
                    tank_eq.roof_min_thickness = sheet_roof_min
                db.commit()

                # Effective min thicknesses strictly from sheet or equipment profile (no hardcoded numbers)
                eff_shell_min = sheet_shell_min if sheet_shell_min is not None else tank_eq.min_required_thickness
                eff_bottom_min = sheet_bottom_min if sheet_bottom_min is not None else tank_eq.bottom_min_thickness
                eff_roof_min = sheet_roof_min if sheet_roof_min is not None else (tank_eq.roof_min_thickness or tank_eq.floating_roof_min_thickness)

                tank_zones = [
                    {"code": "Shell course 1", "type": "Shell", "col_base": 3, "n_readings": 4,
                     "direction_labels": "0°,90°,180°,270°", "min_t": eff_shell_min},
                    {"code": "Shell course 2", "type": "Shell", "col_base": 8, "n_readings": 4,
                     "direction_labels": "0°,90°,180°,270°", "min_t": eff_shell_min},
                    {"code": "Shell Course 3", "type": "Shell", "col_base": 13, "n_readings": 4,
                     "direction_labels": "0°,90°,180°,270°", "min_t": eff_shell_min},
                    {"code": "Roof", "type": "Roof", "col_base": 18, "n_readings": 4,
                     "direction_labels": "0°,90°,180°,270°", "min_t": eff_roof_min},
                    {"code": "Bottom", "type": "Bottom", "col_base": 23, "n_readings": 3,
                     "direction_labels": "Point 1,Point 2,Point 3", "min_t": eff_bottom_min},
                ]

                # Read inspection campaigns from rows 2, 3, 4 only
                for r in range(2, 5):
                    insp_date = parse_date(tank_ws.cell(row=r, column=29).value)
                    if not insp_date:
                        continue

                    tml_readings = []
                    for z in tank_zones:
                        readings_vals = []
                        for i in range(z["n_readings"]):
                            val = parse_decimal(tank_ws.cell(row=r, column=z["col_base"] + i).value)
                            readings_vals.append(val)

                        if any(v is not None for v in readings_vals):
                            while len(readings_vals) < 4:
                                readings_vals.append(None)

                            tml_readings.append(DynamicTMLReadingInput(
                                tml_code=z["code"],
                                tml_name=z["code"],
                                tml_type=z["type"],
                                direction_labels=z["direction_labels"],
                                reading_1=readings_vals[0],
                                reading_2=readings_vals[1],
                                reading_3=readings_vals[2],
                                reading_4=readings_vals[3],
                                min_required_thickness=z["min_t"],
                            ))

                    if tml_readings:
                        register_campaigns.append({
                            "equipment": tank_eq,
                            "date": insp_date,
                            "inspector": "Lead Inspector",
                            "remarks": None,
                            "tml_readings": tml_readings
                        })
                break  # Only process one 'Tank' sheet

        # (also when every row was refused for its unit: the import stops here and reports it, it must not
        # fall through to the other parsers, which would read the same rows again as if they were mm)
        if register_campaigns or row_errors:
            records_created = 0
            records_updated = 0
            errors = list(row_errors)
            for camp in register_campaigns:
                eq = camp["equipment"]
                insp_dt = camp["date"]
                payload = InspectionRecordCreate(
                    equipment_id=eq.id,
                    equipment_tag=eq.equipment_id,
                    inspection_date=insp_dt,
                    inspector_name=camp["inspector"],
                    remarks=camp["remarks"],
                    tml_readings=camp["tml_readings"]
                )
                start_of_day = datetime.combine(insp_dt.date(), datetime.min.time())
                end_of_day = datetime.combine(insp_dt.date(), datetime.max.time())
                existing_record = db.query(InspectionRecord).filter(
                    InspectionRecord.equipment_id == eq.id,
                    InspectionRecord.inspection_date >= start_of_day,
                    InspectionRecord.inspection_date <= end_of_day
                ).first()
                try:
                    if existing_record:
                        update_inspection_record(db, existing_record.id, payload)
                        records_updated += 1
                    else:
                        create_inspection_record(db, payload)
                        records_created += 1
                except Exception as e:
                    errors.append(f"Error processing {eq.equipment_id} on {insp_dt.strftime('%Y-%m-%d')}: {str(e)}")

            return {
                "status": "success",
                "total_rows": len(register_campaigns),
                "records_created": records_created,
                "records_updated": records_updated,
                "errors": errors,
            }

    # =========================================================================
    # ENGINE 2: Standard Single-Table & Multi-Campaign Parser (CSV / Flat XLSX)
    # =========================================================================
    def score_header_row(raw_vals):
        norm_vals = [norm(v) for v in raw_vals if v]
        score = 0
        has_tag = any(any(kw == nv or nv.endswith(kw) or nv.startswith(kw) for kw in TAG_KEYWORDS) for nv in norm_vals)
        if any(norm(v) in norm_tag_map for v in raw_vals if v):
            has_tag = True

        has_date = any(any(kw in nv for kw in DATE_KEYWORDS) for nv in norm_vals)
        has_reading = any(any(kw in nv for kw in READING_KEYWORDS) for nv in norm_vals)

        if has_tag: score += 10
        if has_date: score += 25
        if (has_tag and has_date): score += 20
        if (has_date and has_reading): score += 30

        for nv in norm_vals:
            if any(kw in nv for kw in READING_KEYWORDS): score += 2
            if any(kw in nv for kw in INSPECTOR_KEYWORDS + REMARKS_KEYWORDS): score += 1

        return score

    if is_csv:
        text = file_contents.decode("utf-8-sig", errors="replace")
        lines = [line for line in text.splitlines() if line.strip()]
        header_idx = 0
        best_score = -9999
        for i, line in enumerate(lines[:50]):
            raw_cols = [c.strip() for c in line.split(",") if c.strip()]
            s = score_header_row(raw_cols)
            if s > best_score:
                best_score = s
                header_idx = i
        csv_text = "\n".join(lines[header_idx:])
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = list(reader.fieldnames or [])
        for r in reader:
            rows_data.append({k.strip(): v for k, v in r.items() if k})
        header_row_idx = header_idx + 1
    else:
        wb = load_workbook(filename=io.BytesIO(file_contents), data_only=True)
        sheet = wb.active

        header_row_idx = 1
        best_score = -9999
        for sname in wb.sheetnames:
            ws = wb[sname]
            for row_num in range(1, min(50, ws.max_row + 1)):
                row_vals = [str(ws.cell(row=row_num, column=col).value or "").strip() for col in range(1, ws.max_column + 1)]
                if not any(v for v in row_vals):
                    continue

                s = score_header_row(row_vals)
                if s > best_score:
                    best_score = s
                    sheet = ws
                    header_row_idx = row_num

        headers = [str(sheet.cell(row=header_row_idx, column=col).value or "").strip() for col in range(1, sheet.max_column + 1)]

        # --- Multi-level / merged-cell header fill-down ---
        # Strategy: For every blank column in Row N (primary header), scan Row N+1 and N+2
        # for a non-numeric text value. If found, use it as that column's header.
        # Only advance data_start_row if we filled in at least one inspection-specific column.
        data_start_row = header_row_idx + 1
        inspection_kw_filled = False
        sub_row_consumed = False

        for look_ahead in range(1, 3):  # check up to 2 rows below the header
            if header_row_idx + look_ahead > sheet.max_row:
                break
            candidate_vals = [
                str(sheet.cell(row=header_row_idx + look_ahead, column=col).value or "").strip()
                for col in range(1, sheet.max_column + 1)
            ]
            found_any_fill = False
            for col_idx, (h1, h2) in enumerate(zip(headers, candidate_vals)):
                if h1:
                    continue  # primary header already filled — don't override
                if not h2:
                    continue  # nothing to fill with
                # Only use h2 as header if it is NOT a numeric/date data value
                h2n = norm(h2)
                try:
                    float(h2)
                    is_numeric = True
                except ValueError:
                    is_numeric = False

                if not is_numeric:
                    headers[col_idx] = h2
                    found_any_fill = True
                    if any(kw in h2n for kw in DATE_KEYWORDS + INSPECTOR_KEYWORDS + READING_KEYWORDS):
                        inspection_kw_filled = True

            if found_any_fill and not sub_row_consumed:
                sub_row_consumed = True
                # Check: does this look-ahead row have ONLY header-like content
                # (i.e., is it a pure sub-header row, not a data+header hybrid)?
                non_empty = [v for v in candidate_vals if v]
                numeric_count = sum(1 for v in non_empty if (lambda s: (
                    lambda t: t.lstrip('-').replace('.', '', 1).isdigit()
                )(s.strip()))(v))
                # If the row is MOSTLY non-numeric text, it's a pure sub-header → skip it
                if len(non_empty) > 0 and (numeric_count / len(non_empty)) < 0.3:
                    data_start_row = header_row_idx + look_ahead + 1
        # --- end header fill-down ---

        # --- Deep scan: If "Inspection Date" column still not in headers, search entire sheet ---
        # This handles files where the date column header is far from the equipment tag header.
        has_date_col = any(
            any(kw in norm(h) for kw in DATE_KEYWORDS)
            for h in headers if h
        )
        if not has_date_col:
            for scan_row in range(1, min(100, sheet.max_row + 1)):
                for scan_col in range(1, min(300, sheet.max_column + 1)):
                    cell_val = str(sheet.cell(row=scan_row, column=scan_col).value or "").strip()
                    if cell_val and any(kw in norm(cell_val) for kw in DATE_KEYWORDS):
                        # Found an "Inspection Date" header cell at (scan_row, scan_col)
                        # Read the entire row as supplementary headers
                        for sup_col in range(1, sheet.max_column + 1):
                            sup_val = str(sheet.cell(row=scan_row, column=sup_col).value or "").strip()
                            sup_idx = sup_col - 1
                            if sup_idx < len(headers) and not headers[sup_idx] and sup_val:
                                try:
                                    float(sup_val)
                                except ValueError:
                                    headers[sup_idx] = sup_val
                                    if any(kw in norm(sup_val) for kw in DATE_KEYWORDS):
                                        inspection_kw_filled = True
                        if inspection_kw_filled:
                            data_start_row = max(data_start_row, scan_row + 1)
                        break
                if inspection_kw_filled:
                    break
        # --- end deep scan ---

        for row in sheet.iter_rows(min_row=data_start_row, values_only=True):
            if not any(v is not None and str(v).strip() != "" for v in row):
                continue
            row_dict = {}
            for h, v in zip(headers, row):
                if h:
                    row_dict[h] = v
            rows_data.append(row_dict)

    if not rows_data:
        raise ValueError("The uploaded file contains no readable data rows.")

    def get_field_val(norm_dict, keywords, exclude_keys=None):
        for key, val in norm_dict.items():
            if val is not None and str(val).strip() != "":
                if exclude_keys and any(ex in key for ex in exclude_keys):
                    continue
                for kw in keywords:
                    if kw in key:
                        return val
        return None

    def parse_decimal(val):
        if val is None or val == "":
            return None
        try:
            return Decimal(str(val).strip())
        except Exception:
            return None

    def parse_date(val):
        if isinstance(val, (datetime, date)):
            if isinstance(val, datetime):
                return val
            return datetime.combine(val, datetime.min.time())
        if val is None or val == "":
            return None
        s = str(val).strip()
        if "T" in s:
            s = s.split("T")[0]
        s_clean = s.replace(".", "-")
        for fmt in (
            "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d",
            "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
            "%d-%b-%Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y",
            "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%y", "%m/%d/%y"
        ):
            for target_str in (s, s_clean):
                try:
                    return datetime.strptime(target_str, fmt)
                except ValueError:
                    pass
        # Try Excel serial float number
        try:
            f = float(s)
            return datetime(1899, 12, 30) + timedelta(days=f)
        except Exception:
            pass
        return None

    grouped_records = {}
    errors = []
    total_processed_rows = 0

    # Pre-fetch all known equipment tags for fuzzy matching
    all_eqs = db.query(Equipment).all()
    tag_map = {eq.equipment_id.upper(): eq for eq in all_eqs}
    norm_tag_map = {norm(eq.equipment_id): eq for eq in all_eqs}

    last_seen_eq_tag = None
    last_seen_date = None

    for idx, row in enumerate(rows_data, start=header_row_idx + 1 if not is_csv else 2):
        total_processed_rows += 1
        normalized_row = {norm(k): v for k, v in row.items() if k}

        # PRIORITY 1: Check if ANY cell value in the row directly matches a DB equipment tag
        eq_tag_raw = None
        for cell_val in row.values():
            if cell_val:
                c_str = str(cell_val).strip().upper()
                c_norm = norm(cell_val)
                if c_str in tag_map:
                    eq_tag_raw = tag_map[c_str].equipment_id
                    break
                elif c_norm in norm_tag_map:
                    eq_tag_raw = norm_tag_map[c_norm].equipment_id
                    break

        # PRIORITY 2: Match column by TAG_KEYWORDS excluding non-tag column words like 'units' or 'type'
        if not eq_tag_raw:
            eq_tag_raw = get_field_val(normalized_row, TAG_KEYWORDS, exclude_keys=EXCLUDE_TAG_KEYS)

        # PRIORITY 3: Forward fill from previous row if blank
        if not eq_tag_raw and last_seen_eq_tag:
            eq_tag_raw = last_seen_eq_tag

        date_raw = get_field_val(normalized_row, DATE_KEYWORDS)
        insp_date = parse_date(date_raw) if date_raw else None
        if not insp_date and last_seen_date:
            insp_date = last_seen_date

        if not eq_tag_raw or not insp_date:
            errors.append(f"Row {idx}: Missing Equipment Tag or Inspection Date.")
            continue

        eq_tag = str(eq_tag_raw).strip()
        # Resolve to exact DB equipment_id if available
        if eq_tag.upper() in tag_map:
            eq_tag = tag_map[eq_tag.upper()].equipment_id
        elif norm(eq_tag) in norm_tag_map:
            eq_tag = norm_tag_map[norm(eq_tag)].equipment_id

        last_seen_eq_tag = eq_tag
        last_seen_date = insp_date

        group_key = (eq_tag.upper(), insp_date.strftime("%Y-%m-%d"))

        if group_key not in grouped_records:
            inspector = get_field_val(normalized_row, INSPECTOR_KEYWORDS)
            remarks = get_field_val(normalized_row, REMARKS_KEYWORDS)
            grouped_records[group_key] = {
                "eq_tag": eq_tag,
                "inspection_date": insp_date,
                "inspector_name": str(inspector or "Lead Inspector").strip(),
                "remarks": str(remarks or "").strip() or None,
                "tml_readings": [],
                "legacy": {},
            }

        grp = grouped_records[group_key]

        # Horizontal Multi-Zone Scanner: Scan all header columns for Shell / Dish / Head zone groups
        row_keys = list(row.keys())
        row_vals = list(row.values())

        processed_zones = set()

        for col_idx, h in enumerate(row_keys):
            h_str = str(h or "").strip()
            h_norm = norm(h_str)

            # Check if column header denotes a Zone (e.g. Shell-1, Shell 2, Upper Dish, Lower Dish, S-1, D-1)
            is_zone_hdr = any(kw in h_norm for kw in ["shell", "dish", "head", "course", "s1", "s2", "d1", "d2"]) and not any(ex in h_norm for ex in ["type", "units", "name", "category"])

            if is_zone_hdr and h_str not in processed_zones:
                processed_zones.add(h_str)
                code = h_str
                name = h_str
                if "upp" in h_norm or "d1" in h_norm:
                    name = "Upper Dish Head"
                    t_type = "Dish"
                elif "low" in h_norm or "d2" in h_norm:
                    name = "Lower Dish Head"
                    t_type = "Dish"
                elif "dish" in h_norm or "head" in h_norm:
                    t_type = "Dish"
                else:
                    t_type = "Shell"

                # Extract readings from this column and next 3 contiguous columns
                r1 = parse_decimal(row_vals[col_idx]) if col_idx < len(row_vals) else None
                r2 = parse_decimal(row_vals[col_idx + 1]) if col_idx + 1 < len(row_vals) else None
                r3 = parse_decimal(row_vals[col_idx + 2]) if col_idx + 2 < len(row_vals) else None
                r4 = parse_decimal(row_vals[col_idx + 3]) if col_idx + 3 < len(row_vals) else None

                if any(r is not None for r in [r1, r2, r3, r4]):
                    grp["tml_readings"].append(
                        DynamicTMLReadingInput(
                            tml_code=code,
                            tml_name=name,
                            tml_type=t_type,
                            direction_labels="0°,90°,180°,270°",
                            reading_1=r1,
                            reading_2=r2,
                            reading_3=r3,
                            reading_4=r4,
                        )
                    )

        # Fallback 1: Check for explicit single zone-based row layout columns
        if not grp["tml_readings"]:
            tml_code = get_field_val(normalized_row, ["tmlcode", "zonecode", "tml", "zone", "location", "tmlid"])
            if tml_code:
                tml_code = str(tml_code).strip()
                tml_name = str(get_field_val(normalized_row, ["tmlname", "zonename", "locationname"]) or tml_code).strip()
                tml_type = str(get_field_val(normalized_row, ["tmltype", "componenttype", "component"]) or "Shell").strip()
                orientations = str(get_field_val(normalized_row, ["directionlabels", "direction", "orientations", "points", "labels"]) or "N,S,E,W").strip()

                r1 = parse_decimal(get_field_val(normalized_row, ["reading1", "r1", "north", "n", "deg0", "p1", "point1"]))
                r2 = parse_decimal(get_field_val(normalized_row, ["reading2", "r2", "south", "s", "deg90", "p2", "point2"]))
                r3 = parse_decimal(get_field_val(normalized_row, ["reading3", "r3", "east", "e", "deg180", "p3", "point3"]))
                r4 = parse_decimal(get_field_val(normalized_row, ["reading4", "r4", "west", "w", "deg270", "p4", "point4"]))

                grp["tml_readings"].append(
                    DynamicTMLReadingInput(
                        tml_code=tml_code,
                        tml_name=tml_name,
                        tml_type=tml_type,
                        direction_labels=orientations,
                        reading_1=r1,
                        reading_2=r2,
                        reading_3=r3,
                        reading_4=r4,
                    )
                )

        # Fallback 2: Check for flat layout columns (S1_N, S1_S, S1_E, S1_W, etc.)
        if not grp["tml_readings"]:
            for key_prefix, code, name, t_type in [
                ("s1", "S-1", "Shell Course 1", "Shell"),
                ("s2", "S-2", "Shell Course 2", "Shell"),
                ("d1", "D-1", "Upper Dish Head", "Dish"),
                ("d2", "D-2", "Lower Dish Head", "Dish"),
            ]:
                n_val = parse_decimal(get_field_val(normalized_row, [f"{key_prefix}n", f"{key_prefix}north"]))
                s_val = parse_decimal(get_field_val(normalized_row, [f"{key_prefix}s", f"{key_prefix}south"]))
                e_val = parse_decimal(get_field_val(normalized_row, [f"{key_prefix}e", f"{key_prefix}east"]))
                w_val = parse_decimal(get_field_val(normalized_row, [f"{key_prefix}w", f"{key_prefix}west"]))

                if any(v is not None for v in [n_val, s_val, e_val, w_val]):
                    grp["tml_readings"].append(
                        DynamicTMLReadingInput(
                            tml_code=code,
                            tml_name=name,
                            tml_type=t_type,
                            direction_labels="N,S,E,W",
                            reading_1=n_val,
                            reading_2=s_val,
                            reading_3=e_val,
                            reading_4=w_val,
                        )
                    )
                    grp["legacy"][f"{key_prefix}_n"] = n_val
                    grp["legacy"][f"{key_prefix}_s"] = s_val
                    grp["legacy"][f"{key_prefix}_e"] = e_val
                    grp["legacy"][f"{key_prefix}_w"] = w_val

    records_created = 0
    records_updated = 0

    for (eq_tag_key, insp_date_str), grp in grouped_records.items():
        equipment = db.query(Equipment).filter(
            (Equipment.equipment_id.ilike(grp["eq_tag"])) | (Equipment.equipment_id.ilike(eq_tag_key))
        ).first()

        if not equipment:
            errors.append(f"Equipment '{grp['eq_tag']}' not found in Database. Skipped inspection for {insp_date_str}.")
            continue

        # Build payload
        payload = InspectionRecordCreate(
            equipment_id=equipment.id,
            equipment_tag=equipment.equipment_id,
            inspection_date=grp["inspection_date"],
            inspector_name=grp["inspector_name"],
            remarks=grp["remarks"],
            tml_readings=grp["tml_readings"] if grp["tml_readings"] else None,
            s1_n=grp["legacy"].get("s1_n"),
            s1_s=grp["legacy"].get("s1_s"),
            s1_e=grp["legacy"].get("s1_e"),
            s1_w=grp["legacy"].get("s1_w"),
            s2_n=grp["legacy"].get("s2_n"),
            s2_s=grp["legacy"].get("s2_s"),
            s2_e=grp["legacy"].get("s2_e"),
            s2_w=grp["legacy"].get("s2_w"),
            d1_n=grp["legacy"].get("d1_n"),
            d1_s=grp["legacy"].get("d1_s"),
            d1_e=grp["legacy"].get("d1_e"),
            d1_w=grp["legacy"].get("d1_w"),
            d2_n=grp["legacy"].get("d2_n"),
            d2_s=grp["legacy"].get("d2_s"),
            d2_e=grp["legacy"].get("d2_e"),
            d2_w=grp["legacy"].get("d2_w"),
        )

        # Check if record already exists on this date
        start_of_day = datetime.combine(grp["inspection_date"].date(), datetime.min.time())
        end_of_day = datetime.combine(grp["inspection_date"].date(), datetime.max.time())

        existing_record = db.query(InspectionRecord).filter(
            InspectionRecord.equipment_id == equipment.id,
            InspectionRecord.inspection_date >= start_of_day,
            InspectionRecord.inspection_date <= end_of_day
        ).first()

        try:
            if existing_record:
                update_inspection_record(db, existing_record.id, payload)
                records_updated += 1
            else:
                create_inspection_record(db, payload)
                records_created += 1
        except Exception as e:
            errors.append(f"Error processing {grp['eq_tag']} on {insp_date_str}: {str(e)}")

    if records_created == 0 and records_updated == 0:
        detected_hdrs = [h for h in headers if h] if 'headers' in locals() else []
        errors.insert(0, f"Diagnostic Info — Auto-detected headers on Row {header_row_idx if 'header_row_idx' in locals() else 1}: {detected_hdrs}")

    return {
        "status": "success",
        "total_rows": total_processed_rows,
        "records_created": records_created,
        "records_updated": records_updated,
        "errors": errors,
    }

