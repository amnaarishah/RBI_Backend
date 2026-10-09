from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uuid

from app.models.inspection import InspectionRecord
from app.models.equipment import Equipment
from app.schemas.inspection import InspectionRecordCreate


_LEGACY_ZONE_CODES = {
    "s1": ("S-1", "S1", "SHELL-1", "SHELL 1"),
    "s2": ("S-2", "S2", "SHELL-2", "SHELL 2"),
    "d1": ("D-1", "D1", "DISH-1", "DISH 1", "HEAD-1", "HEAD 1", "UPPE DISH", "UPPER DISH"),
    "d2": ("D-2", "D2", "DISH-2", "DISH 2", "HEAD-2", "HEAD 2", "LOWER DISH", "BOTTOM DISH"),
}
_LEGACY_POINTS = ("n", "s", "e", "w")
_LEGACY_ZONES = [   # code, name, type, field prefix, sort order (used when a record has no dynamic zones)
    ("S-1", "Shell Course 1", "Shell", "s1", 0),
    ("S-2", "Shell Course 2", "Shell", "s2", 1),
    ("D-1", "Upper Dish Head", "Dish", "d1", 2),
    ("D-2", "Lower Dish Head", "Dish", "d2", 3),
]


def _collect_readings(data) -> list:
    """Every non-empty thickness of a submitted inspection (dynamic zones first, else the 16 legacy fields)."""
    readings = [
        v for tml_in in (data.tml_readings or [])
        for v in (tml_in.reading_1, tml_in.reading_2, tml_in.reading_3, tml_in.reading_4) if v is not None
    ]
    if not readings:
        readings = [getattr(data, f"{z}_{p}") for z in _LEGACY_ZONE_CODES for p in _LEGACY_POINTS
                    if getattr(data, f"{z}_{p}") is not None]
    return readings


def _legacy_fields(data) -> dict:
    """The 16 legacy columns (s1_n ... d2_w): the submitted values, overridden by a dynamic zone whose
    code matches (S-1, Shell 2, D-1, Upper Dish ...)."""
    fields = {f"{z}_{p}": getattr(data, f"{z}_{p}") for z in _LEGACY_ZONE_CODES for p in _LEGACY_POINTS}
    for tml_in in data.tml_readings or []:
        code = (tml_in.tml_code or "").strip().upper()
        for z, codes in _LEGACY_ZONE_CODES.items():
            if code in codes:
                values = (tml_in.reading_1, tml_in.reading_2, tml_in.reading_3, tml_in.reading_4)
                for p, v in zip(_LEGACY_POINTS, values):
                    fields[f"{z}_{p}"] = v
                break
    return fields


def _sync_cml_readings(db: Session, equipment: Equipment, data: InspectionRecordCreate, tag: str,
                       create_missing_legacy_zones: bool) -> None:
    """Write the submitted readings into the CML tables (zones + readings) and recalculate the equipment.

    Dynamic zones: the zone is created when missing, its name / labels / t_min are kept in step with the
    Equipment Profile, and the reading is added (the CML engine calculates CR and remaining life).
    Legacy four-zone records: zones are only created when `create_missing_legacy_zones` (new records)."""
    from app.models.cml import InspectionTML
    from app.schemas.cml import InspectionReadingCreate
    from app.crud.cml import add_reading_to_tml, recalculate_equipment_cmls, min_t_for_type, is_zone_specific_type

    def reading_for(vals):
        return InspectionReadingCreate(
            inspection_date=data.inspection_date,
            inspector_name=data.inspector_name or "Lead Inspector",
            inspection_type=data.inspection_type or "Ultrasonic Thickness Measurement (UTM)",
            unit=data.thickness_unit or "mm",
            reading_1=vals[0], reading_2=vals[1], reading_3=vals[2], reading_4=vals[3],
            notes=data.remarks,
        )

    def find_zone(code):
        return db.query(InspectionTML).filter(
            InspectionTML.equipment_id == equipment.id, InspectionTML.tml_code == code).first()

    def new_zone(code, name, tml_type, labels, min_req, sort_order):
        zone = InspectionTML(equipment_id=equipment.id, equipment_tag=tag, tml_code=code, tml_name=name,
                             tml_type=tml_type, direction_labels=labels, min_required_thickness=min_req,
                             sort_order=sort_order)
        db.add(zone)
        db.commit()
        db.refresh(zone)
        return zone

    if data.tml_readings:
        for idx, tml_in in enumerate(data.tml_readings):
            vals = [tml_in.reading_1, tml_in.reading_2, tml_in.reading_3, tml_in.reading_4]
            if all(v is None for v in vals):
                continue
            zone = find_zone(tml_in.tml_code)
            if not zone:
                zone = new_zone(tml_in.tml_code, tml_in.tml_name or tml_in.tml_code, tml_in.tml_type or "Shell",
                                tml_in.direction_labels or "N,S,E,W",
                                min_t_for_type(equipment, tml_in.tml_type, tml_in.min_required_thickness), idx)
            else:
                if tml_in.direction_labels and zone.direction_labels != tml_in.direction_labels:
                    zone.direction_labels = tml_in.direction_labels
                if tml_in.tml_name and zone.tml_name != tml_in.tml_name:
                    zone.tml_name = tml_in.tml_name
                # keep the zone's t_min in step with the Equipment Profile (Nozzle / Other: the value entered on the zone)
                if is_zone_specific_type(zone.tml_type):
                    if tml_in.min_required_thickness is not None:
                        zone.min_required_thickness = tml_in.min_required_thickness
                else:
                    zone.min_required_thickness = min_t_for_type(equipment, zone.tml_type)
                db.commit()
            add_reading_to_tml(db, zone.id, reading_for(vals))
    else:
        for code, name, tml_type, prefix, sort_order in _LEGACY_ZONES:
            vals = [getattr(data, f"{prefix}_{p}") for p in _LEGACY_POINTS]
            if all(v is None for v in vals):
                continue
            zone = find_zone(code)
            if not zone and create_missing_legacy_zones:
                zone = new_zone(code, name, tml_type, "N,S,E,W", min_t_for_type(equipment, tml_type), sort_order)
            if zone:
                add_reading_to_tml(db, zone.id, reading_for(vals))
    recalculate_equipment_cmls(db, equipment.id)


def _snapshot_cml(db: Session, equipment_id):
    """Ids of the zones / readings that exist before a save, so a failed save can be undone."""
    from app.models.cml import InspectionTML, InspectionReading
    tml_ids = {t.id for t in db.query(InspectionTML.id).filter(InspectionTML.equipment_id == equipment_id)}
    reading_ids = set()
    if tml_ids:
        reading_ids = {r.id for r in db.query(InspectionReading.id).filter(InspectionReading.tml_id.in_(tml_ids))}
    return tml_ids, reading_ids


def _undo_created_record(db: Session, record, equipment_id, snapshot):
    """Remove everything a failed create produced (record, new readings, new zones)."""
    from app.models.cml import InspectionTML, InspectionReading
    db.rollback()
    pre_tmls, pre_readings = snapshot
    all_tmls = [t.id for t in db.query(InspectionTML.id).filter(InspectionTML.equipment_id == equipment_id)]
    if all_tmls:
        q = db.query(InspectionReading).filter(InspectionReading.tml_id.in_(all_tmls))
        if pre_readings:
            q = q.filter(~InspectionReading.id.in_(pre_readings))
        q.delete(synchronize_session=False)
    q = db.query(InspectionTML).filter(InspectionTML.equipment_id == equipment_id)
    if pre_tmls:
        q = q.filter(~InspectionTML.id.in_(pre_tmls))
    q.delete(synchronize_session=False)
    db.query(InspectionRecord).filter(InspectionRecord.id == record.id).delete(synchronize_session=False)
    db.commit()
    try:
        from app.crud.cml import recalculate_equipment_cmls
        recalculate_equipment_cmls(db, equipment_id)
    except Exception:
        db.rollback()


def create_inspection_record(db: Session, data: InspectionRecordCreate) -> InspectionRecord:
    # Fetch parent equipment for nominal and min required thickness
    equipment = db.query(Equipment).filter(Equipment.id == data.equipment_id).first()
    if not equipment:
        raise ValueError("Target equipment not found")

    # Check for existing inspection record on same date
    insp_date = data.inspection_date.date() if isinstance(data.inspection_date, datetime) else data.inspection_date
    start_of_day = datetime.combine(insp_date, datetime.min.time())
    end_of_day = datetime.combine(insp_date, datetime.max.time())
    
    existing = db.query(InspectionRecord).filter(
        InspectionRecord.equipment_id == data.equipment_id,
        InspectionRecord.inspection_date >= start_of_day,
        InspectionRecord.inspection_date <= end_of_day
    ).first()
    
    if existing and data.tml_readings:
        # Record exists on same date — check if user is trying to add a NEW zone
        # or re-submitting an ALREADY RECORDED zone (which we block)
        from app.models.cml import InspectionTML, InspectionReading
        formatted_date = insp_date.strftime('%d-%b-%Y') if hasattr(insp_date, 'strftime') else str(insp_date)
        
        duplicate_zones = []
        for tml_in in data.tml_readings:
            if not tml_in.tml_code:
                continue
            # Check if this zone already has a reading on this date
            existing_tml = db.query(InspectionTML).filter(
                InspectionTML.equipment_id == data.equipment_id,
                InspectionTML.tml_code == tml_in.tml_code
            ).first()
            if existing_tml:
                d_start = start_of_day
                d_end = end_of_day
                existing_reading = db.query(InspectionReading).filter(
                    InspectionReading.tml_id == existing_tml.id,
                    InspectionReading.inspection_date >= d_start,
                    InspectionReading.inspection_date <= d_end
                ).first()
                if existing_reading:
                    duplicate_zones.append(tml_in.tml_code)
        
        if duplicate_zones:
            zone_list = ", ".join(duplicate_zones)
            raise ValueError(
                f"Zone(s) '{zone_list}' already have readings recorded for {equipment.equipment_id} "
                f"on {formatted_date}. To change them, please edit the existing record."
            )
        # No duplicates — fall through and add new zones to the existing record below
    
    elif existing and not data.tml_readings:
        # Legacy path (non-dynamic zones) — block same date
        formatted_date = insp_date.strftime('%d-%b-%Y') if hasattr(insp_date, 'strftime') else str(insp_date)
        raise ValueError(f"An inspection record already exists for {equipment.equipment_id} on {formatted_date}. Please select a different date.")

    readings = _collect_readings(data)

    # Corrosion rate and remaining life are NOT estimated here. They are calculated by the
    # CML engine (client sheet method: Ti / Tp / Tf, MAX(short-term, long-term) and the
    # t_min stored in the Equipment Profile) and written back by recalculate_equipment_cmls().
    # A first/baseline reading has no corrosion rate yet, so these stay empty (None).
    min_measured_t = min(readings) if readings else None
    corrosion_rate = None
    remaining_life = None

    legacy = _legacy_fields(data)

    # Next inspection dates (API 510 internal / external) are set by recalculate_equipment_cmls()
    # once a remaining life can be calculated; nothing is assumed for a baseline reading.
    next_inspection_due = None

    record = InspectionRecord(
        equipment_id=data.equipment_id,
        equipment_tag=data.equipment_tag or equipment.equipment_id,
        inspection_date=data.inspection_date,
        inspector_name=data.inspector_name or "Lead Inspector",
        inspection_type=data.inspection_type or "Ultrasonic Thickness Measurement (UTM)",
        thickness_unit=data.thickness_unit or "mm",
        **legacy,
        min_measured_thickness=min_measured_t,
        corrosion_rate=corrosion_rate,
        remaining_life=remaining_life,
        next_inspection_due=next_inspection_due,
        remarks=data.remarks,
        created_by=data.created_by or "System",
    )

    cml_snapshot = _snapshot_cml(db, data.equipment_id)
    db.add(record)

    # Latest inspection date only; corrosion rate / remaining life come from the CML engine
    equipment.inspection_date_current = data.inspection_date
    for field, value in legacy.items():
        if value is not None:
            setattr(equipment, field, value)

    db.commit()
    db.refresh(record)

    # Sync into the CML tables (zones + readings) and recalculate the equipment
    try:
        _sync_cml_readings(db, equipment, data, data.equipment_tag or equipment.equipment_id,
                           create_missing_legacy_zones=True)
    except Exception as e:
        # Never report success when the calculation could not be saved: undo the new record
        import logging
        logging.getLogger("ams.inspection").exception("CML sync failed while creating an inspection")
        _undo_created_record(db, record, data.equipment_id, cml_snapshot)
        raise RuntimeError(f"Calculation could not be completed ({e}). The inspection was not saved.") from e

    record.tml_readings = populate_record_tml_readings(db, record)
    return record


def populate_record_tml_readings(db: Session, record: InspectionRecord):
    """Load all TML measurement zones and readings matching this inspection record."""
    from app.models.cml import InspectionTML, InspectionReading
    from app.schemas.inspection import DynamicTMLReadingInput

    d_start = record.inspection_date - timedelta(hours=12)
    d_end = record.inspection_date + timedelta(hours=12)
    readings = db.query(InspectionReading).join(InspectionTML).filter(
        InspectionTML.equipment_id == record.equipment_id,
        InspectionReading.inspection_date >= d_start,
        InspectionReading.inspection_date <= d_end
    ).order_by(InspectionTML.sort_order.asc(), InspectionTML.created_at.asc()).all()

    tml_list = []
    if readings:
        for rd in readings:
            tml = rd.tml
            tml_list.append(DynamicTMLReadingInput(
                tml_id=tml.id,
                tml_code=tml.tml_code,
                tml_name=tml.tml_name or tml.tml_code,
                tml_type=tml.tml_type or "Shell",
                direction_labels=tml.direction_labels or "N,S,E,W",
                reading_1=rd.reading_1,
                reading_2=rd.reading_2,
                reading_3=rd.reading_3,
                reading_4=rd.reading_4,
                reading_5=rd.reading_5,
                reading_6=rd.reading_6,
            ))
    else:
        legacy_zones = [
            ("S-1", "Shell Course 1", "Shell", [record.s1_n, record.s1_s, record.s1_e, record.s1_w]),
            ("S-2", "Shell Course 2", "Shell", [record.s2_n, record.s2_s, record.s2_e, record.s2_w]),
            ("D-1", "Upper Dish Head", "Dish", [record.d1_n, record.d1_s, record.d1_e, record.d1_w]),
            ("D-2", "Lower Dish Head", "Dish", [record.d2_n, record.d2_s, record.d2_e, record.d2_w]),
        ]
        for code, name, t_type, vals in legacy_zones:
            if any(v is not None for v in vals):
                tml_list.append(DynamicTMLReadingInput(
                    tml_code=code,
                    tml_name=name,
                    tml_type=t_type,
                    direction_labels="N,S,E,W",
                    reading_1=vals[0],
                    reading_2=vals[1],
                    reading_3=vals[2],
                    reading_4=vals[3],
                ))

    return tml_list


def get_inspection_record(db: Session, record_id: uuid.UUID):
    record = db.query(InspectionRecord).filter(InspectionRecord.id == record_id).first()
    if record:
        record.tml_readings = populate_record_tml_readings(db, record)
    return record


def get_inspection_list(
    db: Session,
    equipment_tag: str = None,
    page: int = 1,
    page_size: int = 25
):
    query = db.query(InspectionRecord)
    if equipment_tag:
        query = query.filter(InspectionRecord.equipment_tag.ilike(f"%{equipment_tag}%"))

    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    items = query.order_by(InspectionRecord.inspection_date.desc()).offset((page - 1) * page_size).limit(page_size).all()

    for item in items:
        item.tml_readings = populate_record_tml_readings(db, item)

    return items, total, total_pages


def get_inspection_stats(db: Session):
    # Total = logged inspection events. Healthy / warning / critical are counted per ASSET using the
    # equipment's governing remaining life (not once per historical record).
    total = db.query(InspectionRecord).count()
    assets = db.query(Equipment).filter(Equipment.status != "DELETED")
    critical = assets.filter(Equipment.remaining_life <= 2.0).count()
    warning = assets.filter(Equipment.remaining_life > 2.0, Equipment.remaining_life <= 5.0).count()
    healthy = assets.filter(Equipment.remaining_life > 5.0).count()

    return {
        "total_inspections": total,
        "critical_life_count": critical,
        "warning_life_count": warning,
        "healthy_life_count": healthy,
    }


def update_inspection_record(db: Session, record_id: uuid.UUID, data: InspectionRecordCreate) -> InspectionRecord:
    record = db.query(InspectionRecord).filter(InspectionRecord.id == record_id).first()
    if not record:
        raise ValueError("Inspection record not found")

    equipment = db.query(Equipment).filter(Equipment.id == record.equipment_id).first()
    if not equipment:
        raise ValueError("Target equipment not found")

    old_date = record.inspection_date

    # If inspection_date is changed, check for duplicate on the new date
    insp_date = data.inspection_date.date() if isinstance(data.inspection_date, datetime) else data.inspection_date
    start_of_day = datetime.combine(insp_date, datetime.min.time())
    end_of_day = datetime.combine(insp_date, datetime.max.time())

    existing = db.query(InspectionRecord).filter(
        InspectionRecord.equipment_id == record.equipment_id,
        InspectionRecord.id != record_id,
        InspectionRecord.inspection_date >= start_of_day,
        InspectionRecord.inspection_date <= end_of_day
    ).first()

    if existing:
        formatted_date = insp_date.strftime('%d-%b-%Y') if hasattr(insp_date, 'strftime') else str(insp_date)
        raise ValueError(f"Another inspection record already exists for {equipment.equipment_id} on {formatted_date}.")

    readings = _collect_readings(data)

    # Corrosion rate and remaining life are NOT estimated here. They are calculated by the
    # CML engine (client sheet method: Ti / Tp / Tf, MAX(short-term, long-term) and the
    # t_min stored in the Equipment Profile) and written back by recalculate_equipment_cmls().
    # A first/baseline reading has no corrosion rate yet, so these stay empty (None).
    min_measured_t = min(readings) if readings else None
    corrosion_rate = None
    remaining_life = None

    legacy = _legacy_fields(data)

    # Update record
    record.inspection_date = data.inspection_date
    record.inspector_name = data.inspector_name or record.inspector_name
    record.inspection_type = data.inspection_type or record.inspection_type
    record.thickness_unit = data.thickness_unit or record.thickness_unit
    for field, value in legacy.items():
        setattr(record, field, value)
    record.min_measured_thickness = min_measured_t
    record.corrosion_rate = corrosion_rate
    record.remaining_life = remaining_life
    record.remarks = data.remarks
    if data.created_by:
        record.updated_by = data.created_by      # the signed-in user making this change

    db.commit()
    db.refresh(record)

    # Sync into the CML tables (zones + readings) and recalculate the equipment
    try:
        from app.models.cml import InspectionTML, InspectionReading

        # If inspection date was changed, remove readings tied to old date
        old_d = old_date.date() if isinstance(old_date, datetime) else old_date
        new_d = data.inspection_date.date() if isinstance(data.inspection_date, datetime) else data.inspection_date
        if old_date and old_d != new_d:
            d_start = old_date - timedelta(hours=12)
            d_end = old_date + timedelta(hours=12)
            old_readings = db.query(InspectionReading).join(InspectionTML).filter(
                InspectionTML.equipment_id == equipment.id,
                InspectionReading.inspection_date >= d_start,
                InspectionReading.inspection_date <= d_end
            ).all()
            for ord_item in old_readings:
                db.delete(ord_item)
            db.commit()

        _sync_cml_readings(db, equipment, data, equipment.equipment_id, create_missing_legacy_zones=False)
    except Exception as e:
        import logging
        logging.getLogger("ams.inspection").exception("CML sync failed while updating an inspection")
        db.rollback()
        raise RuntimeError(
            f"The readings were saved but the calculation could not be completed ({e}). "
            f"Open the record and save it again to retry."
        ) from e

    record.tml_readings = populate_record_tml_readings(db, record)
    return record


def delete_inspection_record(db: Session, record_id: uuid.UUID) -> bool:
    """Delete an inspection record, its associated CML readings, and recalculate equipment rates."""
    record = db.query(InspectionRecord).filter(InspectionRecord.id == record_id).first()
    if not record:
        raise ValueError("Inspection record not found")

    equipment_id = record.equipment_id
    insp_date = record.inspection_date

    # Delete related inspection readings for this date and equipment
    try:
        from app.models.cml import InspectionTML, InspectionReading
        from app.crud.cml import recalculate_equipment_cmls

        tmls = db.query(InspectionTML).filter(InspectionTML.equipment_id == equipment_id).all()
        tml_ids = [t.id for t in tmls]
        if tml_ids and insp_date:
            date_val = insp_date.date() if hasattr(insp_date, 'date') else insp_date
            start_of_day = datetime.combine(date_val, datetime.min.time())
            end_of_day = datetime.combine(date_val, datetime.max.time())
            db.query(InspectionReading).filter(
                InspectionReading.tml_id.in_(tml_ids),
                InspectionReading.inspection_date >= start_of_day,
                InspectionReading.inspection_date <= end_of_day
            ).delete(synchronize_session=False)
            db.commit()

        # Clean up any orphaned TML zones that now have 0 readings left
        for t in tmls:
            reading_count = db.query(InspectionReading).filter(InspectionReading.tml_id == t.id).count()
            if reading_count == 0:
                db.delete(t)
        db.commit()
    except Exception as e:
        # do not delete the record while its readings could not be cleaned up
        import logging
        logging.getLogger("ams.inspection").exception("Cleaning up readings failed while deleting an inspection")
        db.rollback()
        raise RuntimeError(f"The inspection could not be deleted ({e}). Nothing was changed.") from e

    # Delete the inspection record
    db.delete(record)
    db.commit()

    # If no inspection records remain for this equipment, clean up and reset stats
    remaining_recs = db.query(InspectionRecord).filter(InspectionRecord.equipment_id == equipment_id).count()
    if remaining_recs == 0:
        db.query(InspectionTML).filter(InspectionTML.equipment_id == equipment_id).delete(synchronize_session=False)
        eq = db.query(Equipment).filter(Equipment.id == equipment_id).first()
        if eq:
            from app.crud.cml import reset_equipment_inspection_results
            reset_equipment_inspection_results(eq)
        db.commit()
    else:
        # Recalculate remaining life and corrosion rate for equipment with remaining records
        try:
            recalculate_equipment_cmls(db, equipment_id)
        except Exception as e:
            import logging
            logging.getLogger("ams.inspection").exception("Recalculation after deleting an inspection failed")
            db.rollback()
            raise RuntimeError(
                f"The inspection was deleted, but the remaining life could not be recalculated ({e}). "
                f"Open another inspection of this equipment and save it again to refresh the values."
            ) from e

    return True
