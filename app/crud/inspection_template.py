"""Strict import of the inspection template (one row = one zone of one inspection).

The template is the contract with the user: fixed column names, a closed list of zone types, readings in mm.
Nothing is guessed. A value that cannot be read, a zone type that is not in the list, an unknown equipment or an
ambiguous date is reported with its row and column, and when ANY row is wrong NOTHING is imported (a half-imported
file is worse than an error). The inspections themselves are created / updated through the same functions the
screens use, so every calculation is identical.
"""
import csv
import io
import logging
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core import unit_policy
from app.models.cml import InspectionReading, InspectionTML
from app.models.equipment import Equipment
from app.models.inspection import InspectionRecord

logger = logging.getLogger("ams.import")

TEMPLATE_SHEET = "Inspection Data"
INSTRUCTIONS_SHEET = "Instructions"
EXAMPLE_SHEET = "Example"
MAX_READINGS = 4

# canonical key -> (name written in the template, accepted spellings; compared without spaces / case / brackets)
COLUMNS = {
    "tag": ("Equipment Tag", {"equipmenttag", "equipmentid", "tagno", "tagnumber", "tag"}),
    "date": ("Inspection Date", {"inspectiondate", "dateofinspection", "inspdate"}),
    "inspector": ("Inspector Name", {"inspectorname", "inspector", "inspectedby"}),
    "code": ("TML Code", {"tmlcode", "zonecode", "cmlcode", "tmlid"}),
    "name": ("TML Name", {"tmlname", "zonename"}),
    "type": ("TML Type", {"tmltype", "zonetype", "componenttype"}),
    "labels": ("Direction Labels", {"directionlabels", "orientations", "pointlabels"}),
    "r1": ("Reading 1 (mm)", {"reading1", "r1"}),
    "r2": ("Reading 2 (mm)", {"reading2", "r2"}),
    "r3": ("Reading 3 (mm)", {"reading3", "r3"}),
    "r4": ("Reading 4 (mm)", {"reading4", "r4"}),
    "min_t": ("Min Required Thickness (mm)", {"minrequiredthickness", "tmin", "minthicknessrequired"}),
    "remarks": ("Remarks", {"remarks", "notes", "comments"}),
    "unit": ("Unit", {"unit", "units", "thicknessunit"}),
}
REQUIRED = ["tag", "date", "inspector", "code", "type", "r1", "r2", "r3", "r4"]
TEMPLATE_ORDER = ["tag", "date", "inspector", "code", "name", "type", "labels", "r1", "r2", "r3", "r4", "min_t", "remarks"]

# the zone types of the software (same list as the "Add Measurement Zone" screen)
ALLOWED_TYPES = ["Shell", "Dish", "Channel Head", "Tube", "Nozzle", "Tank Bottom", "Roof", "Other"]
_TYPE_ALIASES = {
    "shell": "Shell", "shellcourse": "Shell", "shellcourses": "Shell",
    "dish": "Dish", "head": "Dish", "dishhead": "Dish", "dishend": "Dish", "dishedend": "Dish",
    "channelhead": "Channel Head",
    "tube": "Tube", "tubes": "Tube", "tubebundle": "Tube",
    "nozzle": "Nozzle", "nozzleflange": "Nozzle", "flange": "Nozzle",
    "tankbottom": "Tank Bottom", "bottom": "Tank Bottom", "bottomplate": "Tank Bottom",
    "roof": "Roof", "fixedroof": "Roof", "floatingroof": "Roof", "tankroof": "Roof",
    "other": "Other", "othercomponent": "Other",
}
ZONE_SPECIFIC_TYPES = {"Nozzle", "Other"}      # no field in the Equipment Register: t_min is written on the zone
# the Equipment Register field that holds the minimum thickness of each zone type
PROFILE_FIELD = {"Shell": "min_required_thickness", "Dish": "dish_min_thickness", "Channel Head": "dish_min_thickness",
                 "Tube": "tube_min_thickness", "Tank Bottom": "bottom_min_thickness", "Roof": "roof_min_thickness"}
FIELD_LABEL = {"min_required_thickness": "Shell", "dish_min_thickness": "Dish / Channel Head", "tube_min_thickness": "Tube",
               "bottom_min_thickness": "Tank Bottom", "roof_min_thickness": "Roof"}
NOT_MEASURED = {"-", "--", "na", "n/a", "nil", "none", "x"}     # written in a reading cell instead of leaving it empty


def register_value(eq, ztype):
    """The minimum thickness the Equipment Register has for this zone type (None when it has none)."""
    if ztype == "Roof":
        return eq.roof_min_thickness or eq.floating_roof_min_thickness or None
    field = PROFILE_FIELD.get(ztype)
    return (getattr(eq, field) or None) if field else None


def norm(value) -> str:
    return "".join(c.lower() for c in str(value or "") if c.isalnum())


def _header_key(value):
    """Canonical column key of a header cell ('Reading 1 (mm)' -> 'r1'), or None."""
    text = re.sub(r"\(.*?\)", "", str(value or ""))
    n = norm(text)
    if not n:
        return None
    for key, (_name, aliases) in COLUMNS.items():
        if n in aliases:
            return key
    return None


def _find_header(rows):
    """First of the first 30 rows that has the Equipment Tag, TML Code and Reading 1 columns.
    Returns (index in rows, {key: column index}, [unrecognised header texts]) or None."""
    for i, row in enumerate(rows[:30]):
        found = {}
        for c, cell in enumerate(row):
            key = _header_key(cell)
            if key and key not in found:
                found[key] = c
        if {"tag", "code", "r1"} <= set(found):
            return i, found, row
    return None


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def parse_date(v) -> datetime:
    """Excel dates are exact. Text dates are accepted only when they cannot be misread (2026-08-08, 08-Aug-2026,
    25/12/2026); 01/02/2026 could be 1 Feb or 2 Jan, so it is refused instead of guessed."""
    if isinstance(v, datetime):
        return datetime.combine(v.date(), time.min)
    if isinstance(v, date):
        return datetime.combine(v, time.min)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < v < 80000:                      # an Excel date serial that lost its date format
            return datetime.combine((datetime(1899, 12, 30) + timedelta(days=float(v))).date(), time.min)
        raise ValueError(f"'{v}' is not a date")
    s = str(v).strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return datetime(y, mo, d)
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        a, b, y = (int(x) for x in m.groups())
        if a > 12 and b <= 12:
            return datetime(y, b, a)
        if b > 12 and a <= 12:
            return datetime(y, a, b)
        if a == b:
            return datetime(y, a, b)
        if a <= 12 and b <= 12:
            raise ValueError(f"'{s}' can be read as day/month or month/day. Write it as 2026-08-08 or 08-Aug-2026")
    raise ValueError(f"'{s}' is not a date. Use a real Excel date, 2026-08-08 or 08-Aug-2026")


def parse_reading(v):
    """None when the cell is empty / marked not measured; a positive Decimal otherwise; ValueError when it is anything else."""
    if _is_empty(v):
        return None
    if isinstance(v, bool):
        raise ValueError("is not a number")
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v))
    else:
        s = str(v).strip()
        if s.lower() in NOT_MEASURED:
            return None
        if "," in s:
            raise ValueError(f"'{s}' is not a valid number (use a decimal point, e.g. 9.96)")
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise ValueError(f"'{s}' is not a number") from None
    if not d.is_finite():
        raise ValueError("is not a valid number")
    if d <= 0:
        raise ValueError(f"{d} is not a thickness (it must be greater than 0; leave the cell empty when nothing was measured)")
    return d


def normalise_labels(raw):
    s = _text(raw)
    if not s:
        return []
    parts = [p.strip() for p in re.split(r"[,;/]", s) if p.strip()]
    if len(parts) == 1 and " " in s:
        parts = s.split()
    return parts


def _sheet_rows(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _csv_rows(contents: bytes):
    text = contents.decode("utf-8-sig", errors="replace")
    head = "\n".join(text.splitlines()[:5])
    delim = max([",", ";", "\t"], key=lambda d: head.count(d))
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def read_template_rows(contents: bytes, filename: str):
    """(rows, sheet name) of the sheet that holds the template data, or None when the file is not in the template layout.
    Raises ValueError when it is meant to be the template (a sheet called 'Inspection Data') but its header row is wrong."""
    if filename.lower().endswith(".csv"):
        rows = _csv_rows(contents)
        return (rows, None) if _find_header(rows) else None

    from openpyxl import load_workbook
    wb = load_workbook(filename=io.BytesIO(contents), data_only=True)
    if TEMPLATE_SHEET in wb.sheetnames:
        rows = _sheet_rows(wb[TEMPLATE_SHEET])
        if not _find_header(rows):
            raise ValueError(
                f"The sheet '{TEMPLATE_SHEET}' has no header row with the columns Equipment Tag, TML Code and Reading 1. "
                f"Do not change or delete the header row of the template.")
        return rows, TEMPLATE_SHEET
    for name in wb.sheetnames:
        if name in (INSTRUCTIONS_SHEET, EXAMPLE_SHEET):
            continue
        rows = _sheet_rows(wb[name])
        if _find_header(rows):
            return rows, name
    return None


def parse_template(rows, db: Session):
    """Validate every row. Returns (campaigns, errors, warnings, data_row_count, register_values to save)."""
    hdr_idx, cols, header_row = _find_header(rows)
    errors, warnings = [], []

    missing = [COLUMNS[k][0] for k in REQUIRED if k not in cols]
    if missing:
        raise ValueError("Column(s) missing in the header row: " + ", ".join(missing) + ". Use the template (Download Template).")
    ignored = [str(h).strip() for c, h in enumerate(header_row) if not _is_empty(h) and c not in cols.values()]
    if ignored:
        warnings.append("These columns are not part of the template and were NOT read: " + ", ".join(ignored))

    equipment = db.query(Equipment).all()
    by_tag = {}
    by_norm = {}
    for eq in equipment:
        by_tag[eq.equipment_id.strip().upper()] = eq
        by_norm.setdefault(norm(eq.equipment_id), []).append(eq)

    zones_of = {}       # equipment uuid -> {normalised code: InspectionTML}

    def zones_for(eq):
        if eq.id not in zones_of:
            zs = {}
            for z in db.query(InspectionTML).filter(InspectionTML.equipment_id == eq.id).all():
                zs.setdefault(norm(z.tml_code), []).append(z)
            zones_of[eq.id] = zs
        return zones_of[eq.id]

    groups = {}         # (equipment id, date) -> campaign
    seen_zone = {}      # (equipment id, date, normalised code) -> row number
    zone_type_in_file = {}   # (equipment id, normalised code) -> (type, row)
    new_zone_warned = set()
    sheet_register = {}      # (equipment id, register field) -> (value, row): minimums the register lacks and the sheet gives
    today = datetime.now().date()
    data_rows = 0

    def cell(row, key):
        c = cols.get(key)
        return row[c] if c is not None and c < len(row) else None

    for offset, row in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 2):
        if all(_is_empty(v) for v in row):
            continue
        data_rows += 1
        where = f"Row {offset}"
        row_errors = []

        # ---- equipment
        tag = _text(cell(row, "tag"))
        eq = None
        if not tag:
            row_errors.append(f"{where}, Equipment Tag: is empty.")
        else:
            eq = by_tag.get(tag.upper())
            if eq is None:
                matches = by_norm.get(norm(tag), [])
                if len(matches) == 1:
                    eq = matches[0]
                elif len(matches) > 1:
                    row_errors.append(f"{where}, Equipment Tag '{tag}': matches more than one equipment, write the exact tag.")
                else:
                    row_errors.append(f"{where}, Equipment Tag '{tag}': this equipment is not in the Equipment Register. Add it first.")

        # ---- date
        insp_date = None
        raw_date = cell(row, "date")
        if _is_empty(raw_date):
            row_errors.append(f"{where}, Inspection Date: is empty.")
        else:
            try:
                insp_date = parse_date(raw_date)
                if insp_date.date() > today:
                    row_errors.append(f"{where}, Inspection Date {insp_date:%Y-%m-%d}: is in the future.")
                elif insp_date.year < 1990:
                    row_errors.append(f"{where}, Inspection Date {insp_date:%Y-%m-%d}: is not plausible.")
            except ValueError as e:
                row_errors.append(f"{where}, Inspection Date: {e}.")

        # ---- inspector
        inspector = _text(cell(row, "inspector"))
        if not inspector:
            row_errors.append(f"{where}, Inspector Name: is empty.")

        # ---- zone code and type
        code = re.sub(r"\s+", " ", _text(cell(row, "code")))
        if not code:
            row_errors.append(f"{where}, TML Code: is empty.")
        raw_type = _text(cell(row, "type"))
        ztype = _TYPE_ALIASES.get(norm(raw_type)) if raw_type else None
        if not raw_type:
            row_errors.append(f"{where}, TML Type: is empty. Choose one of: {', '.join(ALLOWED_TYPES)}.")
        elif ztype is None:
            row_errors.append(f"{where}, TML Type '{raw_type}': is not valid. Choose one of: {', '.join(ALLOWED_TYPES)}.")

        # ---- unit (optional column)
        if "unit" in cols and not _is_empty(cell(row, "unit")):
            try:
                unit_policy.length_unit(_text(cell(row, "unit")), "Thickness readings")
            except ValueError as e:
                row_errors.append(f"{where}, Unit: {e}")

        # ---- readings
        readings = []
        for i, key in enumerate(("r1", "r2", "r3", "r4"), start=1):
            try:
                readings.append(parse_reading(cell(row, key)))
            except ValueError as e:
                readings.append(None)
                row_errors.append(f"{where}, Reading {i}: {e}.")
        if not row_errors and all(r is None for r in readings):
            row_errors.append(f"{where} ({code}): has no thickness reading. Leave the zone out when it was not measured.")

        # ---- labels
        labels = normalise_labels(cell(row, "labels"))
        last = max((i for i, r in enumerate(readings, start=1) if r is not None), default=0)
        if len(labels) > MAX_READINGS:
            row_errors.append(f"{where}, Direction Labels: more than {MAX_READINGS} labels.")
        elif labels and len(labels) < last:
            row_errors.append(f"{where}, Direction Labels: {len(labels)} label(s) but Reading {last} is filled.")
        if not labels:
            labels = [f"Point {i}" for i in range(1, max(last, 1) + 1)]

        # ---- zone-specific min thickness
        min_t = None
        raw_min = cell(row, "min_t")
        if not _is_empty(raw_min):
            try:
                min_t = parse_reading(raw_min)
            except ValueError as e:
                row_errors.append(f"{where}, Min Required Thickness: {e}.")

        if row_errors:
            errors.extend(row_errors)
            continue

        # ---- checks against the database and the rest of the file
        existing = zones_for(eq).get(norm(code), [])
        zone = None
        if len(existing) == 1:
            zone = existing[0]
        elif len(existing) > 1:
            zone = next((z for z in existing if z.tml_code == code), None)
            if zone is None:
                errors.append(f"{where}, TML Code '{code}': {eq.equipment_id} has several zones with similar codes ({', '.join(z.tml_code for z in existing)}). Write the exact code.")
                continue
        final_code = zone.tml_code if zone else code
        if zone is not None and zone.tml_code != code:
            warnings.append(f"{where}: '{code}' was matched to the existing zone '{zone.tml_code}' of {eq.equipment_id}.")

        if zone is not None and zone.tml_type and _TYPE_ALIASES.get(norm(zone.tml_type), zone.tml_type) != ztype:
            errors.append(f"{where}, TML Type '{raw_type}': zone '{final_code}' of {eq.equipment_id} is already a {zone.tml_type} zone. "
                          f"The type of an existing zone cannot change; use another TML Code for a different component.")
            continue
        prev_type = zone_type_in_file.get((eq.id, norm(final_code)))
        if prev_type and prev_type[0] != ztype:
            errors.append(f"{where}, TML Type '{raw_type}': zone '{final_code}' is a {prev_type[0]} in row {prev_type[1]}.")
            continue
        zone_type_in_file[(eq.id, norm(final_code))] = (ztype, offset)

        if ztype in ZONE_SPECIFIC_TYPES:
            if min_t is None and not (zone is not None and zone.min_required_thickness):
                errors.append(f"{where}, Min Required Thickness: needed for a {ztype} zone (it has no field in the Equipment Register).")
                continue
        elif min_t is not None:
            have = register_value(eq, ztype)
            if have is not None:
                # the Equipment Register always wins
                if have != min_t:
                    warnings.append(f"{where}: Min Required Thickness {min_t} of {final_code} was not used: the Equipment Register of "
                                    f"{eq.equipment_id} already has {have} mm for {ztype} zones and the register always wins.")
                min_t = None
            else:
                field = PROFILE_FIELD[ztype]
                prev = sheet_register.get((eq.id, field))
                if prev and prev[0] != min_t:
                    errors.append(f"{where}, Min Required Thickness {min_t}: row {prev[1]} gives {prev[0]} for the same kind of zone "
                                  f"({FIELD_LABEL[field]}) of {eq.equipment_id}. The Equipment Register keeps one value, write the same value.")
                    continue
                sheet_register.setdefault((eq.id, field), (min_t, offset))

        if zone is None and zones_for(eq) and (eq.id, norm(final_code)) not in new_zone_warned:
            new_zone_warned.add((eq.id, norm(final_code)))
            known = ", ".join(sorted({z.tml_code for zs in zones_for(eq).values() for z in zs}))
            warnings.append(f"{where}: '{final_code}' is a NEW zone for {eq.equipment_id} (existing zones: {known}). "
                            f"If it is the same zone as one of these, correct the TML Code and upload again.")

        key = (eq.id, insp_date.date())
        zkey = (eq.id, insp_date.date(), norm(final_code))
        if zkey in seen_zone:
            errors.append(f"{where}: zone '{final_code}' of {eq.equipment_id} on {insp_date:%Y-%m-%d} is already in row {seen_zone[zkey]}.")
            continue
        seen_zone[zkey] = offset

        camp = groups.setdefault(key, {
            "equipment": eq, "date": insp_date, "inspector": inspector, "remarks": [], "zones": [], "first_row": offset,
        })
        if camp["inspector"].lower() != inspector.lower():
            errors.append(f"{where}, Inspector Name '{inspector}': {eq.equipment_id} on {insp_date:%Y-%m-%d} already has "
                          f"'{camp['inspector']}' in row {camp['first_row']}. One inspection has one inspector.")
            continue
        remark = _text(cell(row, "remarks"))
        if remark and remark not in camp["remarks"]:
            camp["remarks"].append(remark)
        camp["zones"].append({
            "code": final_code, "name": _text(cell(row, "name")) or final_code, "type": ztype,
            "labels": ",".join(labels), "readings": readings, "min_t": min_t, "row": offset,
        })

    if data_rows == 0 and not errors:
        errors.append("The file has a header row but no data rows.")
    return list(groups.values()), errors, warnings, data_rows, {k: v[0] for k, v in sheet_register.items()}


def run_template_import(db: Session, contents: bytes, filename: str):
    """None when the file is not in the template layout (the other readers then try it)."""
    from app.crud.cml import min_t_for_type
    from app.crud.inspection import create_inspection_record, update_inspection_record
    from app.schemas.inspection import DynamicTMLReadingInput, InspectionRecordCreate

    found = read_template_rows(contents, filename)
    if found is None:
        return None
    rows, _sheet = found
    campaigns, errors, warnings, data_rows, to_register = parse_template(rows, db)

    result = {"status": "rejected", "layout": "template", "total_rows": data_rows, "records_created": 0,
              "records_updated": 0, "errors": errors, "warnings": warnings, "summary": []}
    if errors:
        result["errors"] = errors + ["Nothing was imported. Correct the rows above and upload the file again."]
        return result

    for (eq_id, field), value in to_register.items():
        eq = db.query(Equipment).filter(Equipment.id == eq_id).one()
        setattr(eq, field, value)
        warnings.append(f"{eq.equipment_id}: the Equipment Register had no minimum thickness for {FIELD_LABEL[field]} zones, "
                        f"so {value} mm from the file was saved to the Equipment Register.")
    if to_register:
        db.commit()

    campaigns.sort(key=lambda c: (c["equipment"].equipment_id, c["date"]))
    failed = 0
    for camp in campaigns:
        eq, when = camp["equipment"], camp["date"]
        zones_summary = []
        tml_readings = []
        for z in camp["zones"]:
            tml_readings.append(DynamicTMLReadingInput(
                tml_code=z["code"], tml_name=z["name"], tml_type=z["type"], direction_labels=z["labels"],
                reading_1=z["readings"][0], reading_2=z["readings"][1], reading_3=z["readings"][2], reading_4=z["readings"][3],
                min_required_thickness=z["min_t"],
            ))
            used = [r for r in z["readings"] if r is not None]
            t_min = min_t_for_type(eq, z["type"], z["min_t"])
            existing_zone = db.query(InspectionTML).filter(InspectionTML.equipment_id == eq.id, InspectionTML.tml_code == z["code"]).first()
            if t_min is None and existing_zone is not None and z["type"] in ZONE_SPECIFIC_TYPES:
                t_min = existing_zone.min_required_thickness
            if t_min is None:
                warnings.append(f"{eq.equipment_id} / {z['code']} ({z['type']}): no minimum required thickness, neither in the Equipment Register "
                                f"nor in the file. The readings and the corrosion rate are saved, but no remaining life can be calculated "
                                f"for this zone. Enter it in the Equipment Register or in the column Min Required Thickness.")
            zones_summary.append({"code": z["code"], "type": z["type"], "readings": [str(r) for r in used],
                                  "min_reading": str(min(used)), "t_min": str(t_min) if t_min is not None else None})
        payload = InspectionRecordCreate(
            equipment_id=eq.id, equipment_tag=eq.equipment_id, inspection_date=when,
            inspector_name=camp["inspector"], remarks=" | ".join(camp["remarks"]) or None, tml_readings=tml_readings,
        )
        existing = db.query(InspectionRecord).filter(
            InspectionRecord.equipment_id == eq.id,
            InspectionRecord.inspection_date >= datetime.combine(when.date(), time.min),
            InspectionRecord.inspection_date <= datetime.combine(when.date(), time.max),
        ).first()
        try:
            if existing:
                record = update_inspection_record(db, existing.id, payload)
                result["records_updated"] += 1
                action = "updated"
            else:
                record = create_inspection_record(db, payload)
                result["records_created"] += 1
                action = "created"
            db.refresh(record)
            for zs in zones_summary:       # the calculated result of each zone for this inspection (None = baseline)
                tml = db.query(InspectionTML).filter(InspectionTML.equipment_id == eq.id, InspectionTML.tml_code == zs["code"]).first()
                rd = db.query(InspectionReading).filter(
                    InspectionReading.tml_id == tml.id,
                    InspectionReading.inspection_date >= datetime.combine(when.date(), time.min),
                    InspectionReading.inspection_date <= datetime.combine(when.date(), time.max),
                ).first() if tml else None
                zs["corrosion_rate"] = str(rd.controlling_cr) if rd is not None and rd.controlling_cr is not None else None
                zs["remaining_life"] = str(rd.controlling_rl) if rd is not None and rd.controlling_rl is not None else None
                zs["controlling_point"] = (rd.controlling_point_index + 1) if rd is not None and rd.controlling_point_index is not None else None
            result["summary"].append({
                "equipment": eq.equipment_id, "date": when.strftime("%Y-%m-%d"), "action": action, "zones": zones_summary,
                "min_thickness": str(record.min_measured_thickness) if record.min_measured_thickness is not None else None,
                "corrosion_rate": str(record.corrosion_rate) if record.corrosion_rate is not None else None,
                "remaining_life": str(record.remaining_life) if record.remaining_life is not None else None,
            })
        except ValueError as e:
            failed += 1
            result["errors"].append(f"{eq.equipment_id} on {when:%Y-%m-%d}: {e}")
        except Exception:                    # noqa: BLE001 - reported, never shown raw to the user
            logger.exception("template import failed for %s on %s", eq.equipment_id, when)
            failed += 1
            result["errors"].append(f"{eq.equipment_id} on {when:%Y-%m-%d}: the inspection could not be saved (see the server log).")
    saved = result["records_created"] + result["records_updated"]
    result["status"] = "success" if not failed else ("partial" if saved else "rejected")
    return result


# ------------------------------------------------------------------ the downloadable template
def build_template_workbook() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    head_fill = PatternFill("solid", fgColor="1F3A5F")
    req_fill = PatternFill("solid", fgColor="1F3A5F")
    opt_fill = PatternFill("solid", fgColor="5B7A99")
    thin = Side(style="thin", color="C9D3DF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    white_bold = Font(bold=True, color="FFFFFF")

    wb = Workbook()

    # ---------------- Instructions
    ws = wb.active
    ws.title = INSTRUCTIONS_SHEET
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 70
    ws.column_dimensions["D"].width = 30
    ws["A1"] = "Inspection data import - how to fill this file"
    ws["A1"].font = Font(bold=True, size=15, color="1F3A5F")
    lines = [
        "1. Fill the sheet 'Inspection Data' (one row = ONE zone of ONE inspection). Do not change the header row. Dark blue headers are required, light blue headers are optional. The sheet 'Example' is only an example and is never imported.",
        "2. The equipment must already exist in the Equipment Register (same Equipment Tag).",
        "3. All thicknesses are in millimetres (mm). Leave a reading EMPTY when that point was not measured - never type 0. Up to 4 readings per zone; fewer is fine.",
        "4. Keep Reading 1 to Reading 4 at the SAME position in every inspection (for example Reading 1 = 0 degrees, Reading 2 = 90 degrees ...). Each point is compared with the same point of the earlier inspections to calculate the corrosion rate.",
        "5. Use the SAME TML Code for the same zone in every inspection (e.g. always S-1; S-1, s1 and S 1 are read as the same zone). A different code creates a new zone and its history starts again.",
        "6. Every row of the same equipment and date is one inspection; give each zone its own row. One inspection has one Inspector Name.",
        "7. If an inspection of that equipment on that date already exists, it is replaced by the file.",
        "8. If ANY row is wrong, nothing is imported and the screen lists every problem with its row. Correct the file and upload again.",
        "9. After the import the screen shows, for every inspection, its corrosion rate and remaining life; 'Details' shows every zone with its readings, minimum required thickness (t_min), corrosion rate, remaining life and controlling point. Please check them.",
    ]
    for i, text in enumerate(lines, start=3):
        ws.cell(i, 1, text).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=4)
        ws.row_dimensions[i].height = 32
    r = 3 + len(lines) + 1
    for c, h in enumerate(["Column", "Required?", "What to write", "Example"], start=1):
        cell = ws.cell(r, c, h)
        cell.font = white_bold
        cell.fill = head_fill
        cell.border = border
    guide = [
        ("Equipment Tag", "Yes", "Tag of the equipment exactly as in the Equipment Register.", "00-V-15"),
        ("Inspection Date", "Yes", "A real Excel date (not in the future). If you type text, use 2026-08-08 or 08-Aug-2026.", "2026-08-08"),
        ("Inspector Name", "Yes", "Who took the readings.", "Inspector 1"),
        ("TML Code", "Yes", "Short code of the zone / measurement location. Keep it the same in every inspection.", "S-1"),
        ("TML Name", "No", "Readable name of the zone.", "Shell Course 1"),
        ("TML Type", "Yes", "Choose from the list: " + ", ".join(ALLOWED_TYPES) + ". The type decides which minimum thickness of the Equipment Register is used.", "Shell"),
        ("Direction Labels", "No", "Names of the reading points, separated by commas. If empty: Point 1, Point 2 ...", "0, 90, 180, 270"),
        ("Reading 1 - 4 (mm)", "Reading 1 at least", "Measured thickness in mm. Empty = not measured. Use a decimal point (9.96, not 9,96).", "11.97"),
        ("Min Required Thickness (mm)", "Nozzle / Other: yes; others: optional", "Every zone type uses its own minimum thickness (Shell, Dish, Tube, Roof, Tank Bottom). It is taken from the Equipment Register. If the register has none for that type, the value written here is used AND saved to the Equipment Register; if the register has one, the register value is used. Nozzle / Other have no register field: write the value here.", "6.5"),
        ("Remarks", "No", "Any note about this inspection.", "Routine UT"),
    ]
    for i, row in enumerate(guide, start=r + 1):
        for c, v in enumerate(row, start=1):
            cell = ws.cell(i, c, v)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = border
        ws.row_dimensions[i].height = 44

    # ---------------- Inspection Data (empty, with checks)
    data = wb.create_sheet(TEMPLATE_SHEET)
    optional = {"name", "labels", "min_t", "remarks"}
    for c, key in enumerate(TEMPLATE_ORDER, start=1):
        cell = data.cell(1, c, COLUMNS[key][0])
        cell.font = white_bold
        cell.fill = opt_fill if key in optional else req_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
        data.column_dimensions[get_column_letter(c)].width = 22 if key in ("tag", "date", "inspector", "name", "type", "labels", "min_t", "remarks") else 14
    data.row_dimensions[1].height = 34
    data.freeze_panes = "A2"
    last_row = 2000
    col = {k: get_column_letter(i) for i, k in enumerate(TEMPLATE_ORDER, start=1)}
    types = DataValidation(type="list", formula1='"' + ",".join(ALLOWED_TYPES) + '"', allow_blank=True, showErrorMessage=True,
                           errorTitle="TML Type", error="Choose one of: " + ", ".join(ALLOWED_TYPES))
    types.add(f"{col['type']}2:{col['type']}{last_row}")
    nums = DataValidation(type="decimal", operator="greaterThan", formula1="0", allow_blank=True, showErrorMessage=True,
                          errorTitle="Thickness", error="Enter the thickness in mm (greater than 0) or leave the cell empty.")
    for k in ("r1", "r2", "r3", "r4", "min_t"):
        nums.add(f"{col[k]}2:{col[k]}{last_row}")
    dates = DataValidation(type="date", operator="greaterThan", formula1="32874", allow_blank=True, showErrorMessage=True,
                           errorTitle="Inspection Date", error="Enter a real date, e.g. 2026-08-08.")
    dates.add(f"{col['date']}2:{col['date']}{last_row}")
    for dv in (types, nums, dates):
        data.add_data_validation(dv)
    for rr in range(2, last_row + 1):
        data[f"{col['date']}{rr}"].number_format = "yyyy-mm-dd"

    # ---------------- Example (never imported)
    ex = wb.create_sheet(EXAMPLE_SHEET)
    ex["A1"] = "EXAMPLE ONLY - this sheet is not imported. Copy the idea, type your own rows in 'Inspection Data'."
    ex["A1"].font = Font(bold=True, color="B42318")
    for c, key in enumerate(TEMPLATE_ORDER, start=1):
        cell = ex.cell(2, c, COLUMNS[key][0])
        cell.font = white_bold
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        ex.column_dimensions[get_column_letter(c)].width = 22 if key in ("tag", "date", "inspector", "name", "type", "labels", "min_t", "remarks") else 14
    D = datetime
    examples = [
        ["00-V-15", D(2026, 8, 8), "Inspector 1", "S-1", "Shell Course 1", "Shell", "0, 90, 180, 270", 11.97, 11.95, 11.86, 11.17, None, "Routine UT measurement"],
        ["00-V-15", D(2026, 8, 8), "Inspector 1", "S-2", "Shell Course 2", "Shell", "0, 90, 180, 270", 12.06, 11.94, 11.91, 11.89, None, None],
        ["00-V-15", D(2026, 8, 8), "Inspector 1", "D-1", "Upper Dish Head", "Dish", "0, 90, 180, 270", 11.77, 11.77, 11.78, 11.60, None, None],
        ["00-E-12", D(2026, 8, 8), "Inspector 2", "TUBE-1", "Tube bundle", "Tube", "Wall", 1.95, None, None, None, None, "One wall reading"],
        ["06-TK-12", D(2026, 8, 8), "Inspector 2", "BTM-1", "Tank bottom", "Tank Bottom", "Point 1, Point 2, Point 3", 5.79, 5.93, 5.91, None, None, "Three points only"],
        ["00-V-15", D(2026, 8, 8), "Inspector 1", "N-1", "Nozzle N1", "Nozzle", "Wall", 9.10, None, None, None, 6.5, "Nozzle: Min Required Thickness is written here"],
    ]
    for i, row in enumerate(examples, start=3):
        for c, v in enumerate(row, start=1):
            cell = ex.cell(i, c, v)
            if isinstance(v, datetime):
                cell.number_format = "yyyy-mm-dd"

    wb.active = 0
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()
