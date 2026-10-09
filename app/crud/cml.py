from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
import uuid

from app.models.cml import InspectionTML, InspectionReading
from app.models.equipment import Equipment
from app.schemas.cml import InspectionTMLCreate, InspectionReadingCreate


def get_tmls_by_equipment(db: Session, equipment_id: uuid.UUID):
    # Read-only: stored results are refreshed whenever readings or the Equipment Profile
    # t_min values change (create / update / delete inspection, update equipment).
    equipment = db.query(Equipment).filter(Equipment.id == equipment_id).first()

    tmls = db.query(InspectionTML).filter(
        InspectionTML.equipment_id == equipment_id
    ).order_by(InspectionTML.sort_order).all()

    if equipment:
        for tml in tmls:
            tml.min_required_thickness = _get_min_t_for_tml(tml, equipment)

    return tmls



def create_tml(db: Session, data: InspectionTMLCreate) -> InspectionTML:
    tml = InspectionTML(**data.model_dump())
    db.add(tml)
    db.commit()
    db.refresh(tml)
    return tml


def _calculate_days_diff(d1: datetime, d2: datetime) -> float:
    delta = d2.replace(tzinfo=None) - d1.replace(tzinfo=None)
    return max(1.0, delta.days)  # Avoid division by zero


def is_zone_specific_type(tml_type: Optional[str]) -> bool:
    """Nozzle / Other zones have no Equipment Profile field: their t_min is entered on the zone."""
    return (tml_type or "").strip().lower() in ('nozzle', 'nozzle / flange', 'nozzle/flange', 'other', 'other component')


def min_t_for_type(equipment: Equipment, tml_type: Optional[str], zone_value: Optional[Decimal] = None) -> Optional[Decimal]:
    """Minimum required thickness for a zone type, strictly from the Equipment Profile.
    Nozzle / Other zones have no profile field: their own value (`zone_value`) is used."""
    t = (tml_type or "").lower()

    if is_zone_specific_type(t):
        return zone_value
    if t in ('tube', 'tubes', 'tube bundle'):
        return equipment.tube_min_thickness
    if t in ('bottom', 'tank bottom', 'bottom plate'):
        return equipment.bottom_min_thickness
    if t in ('roof', 'fixed roof', 'floating roof', 'tank roof'):
        return equipment.roof_min_thickness or equipment.floating_roof_min_thickness
    if t in ('dish', 'head', 'channel head', 'dish / head'):
        return equipment.dish_min_thickness
    return equipment.min_required_thickness          # shell course / default


def _get_min_t_for_tml(tml: InspectionTML, equipment: Equipment) -> Optional[Decimal]:
    """Return correct minimum required thickness based strictly on Equipment Profile."""
    if not equipment:
        return tml.min_required_thickness
    return min_t_for_type(equipment, tml.tml_type, tml.min_required_thickness)


def _compute_readings(
    current_vals: list,
    initial_vals: list,
    prev_vals: list,
    initial_date: datetime,
    prev_date: datetime,
    current_date: datetime,
    min_t: Decimal,
    is_tank_bottom: bool = False
):
    loss_list = []
    cr_list = []
    rl_list = []
    st_cr_list = []
    lt_cr_list = []
    cr_type_list = []
    worst_rl = Decimal('999999')
    worst_cr = Decimal('0')
    worst_idx = -1

    long_term_years = Decimal(str(_calculate_days_diff(initial_date, current_date) / 365.0))
    short_term_years = Decimal(str(_calculate_days_diff(prev_date, current_date) / 365.0))

    for i in range(6):
        cv = current_vals[i]
        iv = initial_vals[i]   # Initial (Ti) value
        pv = prev_vals[i]      # Previous (Tp) value

        if cv is not None and iv is not None:
            # Long-Term Loss and CR (Ti -> Tf)
            long_loss = iv - cv
            long_cr = max(Decimal("0.001"), long_loss / long_term_years) if long_loss > 0 else Decimal("0")

            # Short-Term Loss and CR (Tp -> Tf)
            if pv is not None:
                short_loss = pv - cv
                short_cr = max(Decimal("0.001"), short_loss / short_term_years) if short_loss > 0 else Decimal("0")
            else:
                short_cr = long_cr  # fallback

            # Client's method: Controlling CR = MAX(Short, Long)
            if short_cr >= long_cr and pv is not None and initial_date != prev_date:
                controlling_cr = short_cr
                cr_type = "ST"
            else:
                controlling_cr = long_cr
                cr_type = "LT"

            # Remaining Life = (Tf - Tmr) / Controlling CR (with API 653 2x factor for Tank Bottom)
            effective_cr = (controlling_cr * Decimal("2.0")) if is_tank_bottom else controlling_cr
            if min_t is not None and effective_cr > 0:
                rl = (cv - min_t) / effective_cr if cv > min_t else Decimal("0")
                rl_str = f"{rl:.2f}"
                # shortest life governs; when several points are equal (e.g. all already below t_min -> 0)
                # the one corroding fastest is reported as the governing point
                if rl < worst_rl or (rl == worst_rl and controlling_cr > worst_cr):
                    worst_rl = rl
                    worst_cr = controlling_cr
                    worst_idx = i
            else:
                rl_str = "-"

            # Show Long-Term Loss in the table (as per Excel row 22)
            loss_list.append(f"{long_loss:.2f}")
            cr_list.append(f"{controlling_cr:.4f}")
            rl_list.append(rl_str)
            st_cr_list.append(f"{short_cr:.4f}")
            lt_cr_list.append(f"{long_cr:.4f}")
            cr_type_list.append(cr_type)

        elif cv is not None:
            loss_list.append("0.00")
            cr_list.append("0.0000")
            rl_list.append("-")
            st_cr_list.append("0.0000")
            lt_cr_list.append("0.0000")
            cr_type_list.append("LT")
        else:
            loss_list.append("")
            cr_list.append("")
            rl_list.append("")
            st_cr_list.append("")
            lt_cr_list.append("")
            cr_type_list.append("")

    return loss_list, cr_list, rl_list, st_cr_list, lt_cr_list, cr_type_list, worst_rl, worst_cr, worst_idx


def _apply_status(reading: InspectionReading, worst_rl: Optional[Decimal], worst_cr: Decimal, worst_idx: int,
                  min_t: Optional[Decimal] = None):
    """Set status, controlling_cr, controlling_rl on a reading object.

    Status values: CRITICAL (RL <= 2), WARNING (RL <= 5), SAFE, NO_TMIN (no minimum required
    thickness is set for this zone, so remaining life cannot be calculated)."""
    if worst_idx >= 0 and worst_rl is not None and worst_rl < Decimal("999"):
        reading.controlling_cr = worst_cr
        reading.controlling_rl = worst_rl
        reading.controlling_point_index = worst_idx

        if reading.controlling_rl <= 2:
            reading.status = "CRITICAL"
        elif reading.controlling_rl <= 5:
            reading.status = "WARNING"
        else:
            reading.status = "SAFE"
    else:
        reading.controlling_rl = None
        reading.controlling_point_index = None
        reading.controlling_cr = worst_cr if (worst_cr and worst_cr > 0) else None
        reading.status = "NO_TMIN" if min_t is None else "SAFE"


def calculate_api510_intervals(controlling_rl: Decimal, latest_date: datetime):
    """
    API 510 Next Inspection Interval Rules (client Rev02 rows 57-58):

    Internal Inspection:
      - RL < 2 yrs  → Interval = RL (Emergency)
      - 2 ≤ RL ≤ 4  → Interval = 2 years
      - RL > 4       → Interval = min(10 years, RL/2)

    External Inspection:
      - Interval = min(5 years, Internal Interval)

    Returns (next_internal_due: datetime, next_external_due: datetime)
    """
    if controlling_rl is None:
        rl = 999.0
    else:
        rl = float(controlling_rl)

    # Internal interval (years) per client rules:
    if rl <= 0.0:
        internal_interval = 0.0
    elif rl < 2.0:
        internal_interval = rl
    elif rl <= 4.0:
        internal_interval = 2.0
    else:
        internal_interval = min(10.0, rl / 2.0)

    # External interval (years)
    external_interval = min(5.0, internal_interval)

    base = latest_date.replace(tzinfo=None) if latest_date else datetime.utcnow()
    next_internal = base + timedelta(days=int(internal_interval * 365))
    next_external = base + timedelta(days=int(external_interval * 365))

    return next_internal, next_external


def add_reading_to_tml(db: Session, tml_id: uuid.UUID, data: InspectionReadingCreate) -> InspectionReading:
    tml = db.query(InspectionTML).filter(InspectionTML.id == tml_id).first()
    if not tml:
        raise ValueError("TML not found")

    # Get ALL prior readings sorted ascending (strictly before this day)
    insp_date = data.inspection_date.date() if isinstance(data.inspection_date, datetime) else data.inspection_date
    start_of_day = datetime.combine(insp_date, datetime.min.time())
    end_of_day = datetime.combine(insp_date, datetime.max.time())

    prior_readings = db.query(InspectionReading).filter(
        InspectionReading.tml_id == tml_id,
        InspectionReading.inspection_date < start_of_day
    ).order_by(InspectionReading.inspection_date.asc()).all()

    existing_reading = db.query(InspectionReading).filter(
        InspectionReading.tml_id == tml_id,
        InspectionReading.inspection_date >= start_of_day,
        InspectionReading.inspection_date <= end_of_day
    ).first()

    reading = existing_reading or InspectionReading(tml_id=tml_id)
    reading.inspection_date = data.inspection_date
    reading.inspector_name = data.inspector_name
    reading.inspection_type = data.inspection_type
    reading.unit = data.unit
    reading.notes = data.notes
    reading.reading_1 = data.reading_1
    reading.reading_2 = data.reading_2
    reading.reading_3 = data.reading_3
    reading.reading_4 = data.reading_4
    reading.reading_5 = data.reading_5
    reading.reading_6 = data.reading_6

    current_vals = [data.reading_1, data.reading_2, data.reading_3,
                    data.reading_4, data.reading_5, data.reading_6]
    valid_current = [v for v in current_vals if v is not None]

    if valid_current:
        reading.min_reading = min(valid_current)

    equipment = db.query(Equipment).filter(Equipment.id == tml.equipment_id).first()
    min_t = _get_min_t_for_tml(tml, equipment)

    if prior_readings:
        initial_reading = prior_readings[0]   # Ti — very first reading
        prev_reading = prior_readings[-1]     # Tp — most recent prior reading

        initial_vals = [initial_reading.reading_1, initial_reading.reading_2, initial_reading.reading_3,
                        initial_reading.reading_4, initial_reading.reading_5, initial_reading.reading_6]
        prev_vals = [prev_reading.reading_1, prev_reading.reading_2, prev_reading.reading_3,
                     prev_reading.reading_4, prev_reading.reading_5, prev_reading.reading_6]

        loss_list, cr_list, rl_list, st_cr_list, lt_cr_list, cr_type_list, worst_rl, worst_cr, worst_idx = _compute_readings(
            current_vals, initial_vals, prev_vals,
            initial_reading.inspection_date, prev_reading.inspection_date, data.inspection_date,
            min_t
        )
    else:
        # No prior reading — first ever reading, just placeholders
        loss_list, cr_list, rl_list, st_cr_list, lt_cr_list, cr_type_list = [], [], [], [], [], []
        for v in current_vals:
            if v is not None:
                loss_list.append("0.00")
                cr_list.append("0.0000")
                rl_list.append("999.00")
                st_cr_list.append("0.0000")
                lt_cr_list.append("0.0000")
                cr_type_list.append("LT")
            else:
                loss_list.append("")
                cr_list.append("")
                rl_list.append("")
                st_cr_list.append("")
                lt_cr_list.append("")
                cr_type_list.append("")
        worst_rl = Decimal('999')
        worst_cr = Decimal('0')
        worst_idx = 0 if valid_current else -1

    reading.thickness_lost_csv = ",".join(loss_list)
    reading.corrosion_rate_csv = ",".join(cr_list)
    reading.remaining_life_csv = ",".join(rl_list)
    reading.short_term_cr_csv = ",".join(st_cr_list)
    reading.long_term_cr_csv = ",".join(lt_cr_list)
    reading.cr_type_csv = ",".join(cr_type_list)

    _apply_status(reading, worst_rl, worst_cr, worst_idx, min_t)
    if not prior_readings:
        reading.status = "BASELINE"   # first reading (Ti): no corrosion rate / life yet

    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


def reset_equipment_inspection_results(equipment: Equipment) -> None:
    """Clear everything the inspection engine derives (used when no inspection is left)."""
    equipment.remaining_life = None
    equipment.corrosion_rate = None
    equipment.shell_controlling_life = None
    equipment.head_controlling_life = None
    equipment.tube_controlling_life = None
    equipment.roof_controlling_life = None
    equipment.bottom_controlling_life = None
    equipment.next_internal_inspection_date = None
    equipment.next_external_inspection_date = None
    equipment.inspection_date_current = None


def recalculate_equipment_cmls(db: Session, equipment_uuid: uuid.UUID,
                                new_min_t: Decimal = None, new_dish_min_t: Decimal = None):
    """
    Recalculate all CML readings when equipment min thickness changes.
    Shell TMLs use new_min_t; Dish/Head TMLs use new_dish_min_t.
    Uses MAX(Short CR, Long CR) — same as client's Excel method.

    After recalculating per-reading values, this function also:
    - Groups TMLs by tml_type (Shell / Head / Tube)
    - Computes governing life per component (MIN of controlling_rl across all readings)
    - Computes overall equipment life (MIN of all component lives)
    - Calculates API 510 Internal & External next inspection due dates
    - Saves all values to both equipment and latest inspection_record
    """
    from app.models.inspection import InspectionRecord

    equipment = db.query(Equipment).filter(Equipment.id == equipment_uuid).first()
    if equipment is None:
        return

    eq_type = (equipment.equipment_type or "").lower() if equipment else ""
    eq_cat  = (equipment.equipment_category or "").lower() if equipment else ""
    is_tank = "tank" in eq_type or "tank" in eq_cat

    shell_min_t  = new_min_t if new_min_t is not None else equipment.min_required_thickness
    dish_min_t   = new_dish_min_t if new_dish_min_t is not None else equipment.dish_min_thickness
    tube_min_t   = equipment.tube_min_thickness
    roof_min_t   = equipment.roof_min_thickness or equipment.floating_roof_min_thickness
    bottom_min_t = equipment.bottom_min_thickness

    def _is_head_type(t):   return t and t.lower() in ('dish', 'head', 'channel head', 'dish / head')
    def _is_tube_type(t):   return t and t.lower() in ('tube', 'tubes', 'tube bundle')
    def _is_roof_type(t):   return t and t.lower() in ('roof', 'fixed roof', 'floating roof', 'tank roof')
    def _is_bottom_type(t): return t and t.lower() in ('bottom', 'tank bottom', 'bottom plate')

    # Re-sync each TML's stored min_required_thickness strictly with Equipment Profile
    tmls = db.query(InspectionTML).filter(InspectionTML.equipment_id == equipment_uuid).all()
    for tml in tmls:
        if _is_head_type(tml.tml_type):
            tml.min_required_thickness = dish_min_t
        elif _is_tube_type(tml.tml_type):
            tml.min_required_thickness = tube_min_t
        elif _is_roof_type(tml.tml_type):
            tml.min_required_thickness = roof_min_t
        elif _is_bottom_type(tml.tml_type):
            tml.min_required_thickness = bottom_min_t
        elif is_zone_specific_type(tml.tml_type):
            pass                                   # keep the t_min entered on this zone
        else:
            tml.min_required_thickness = shell_min_t

    db.commit()

    # Recalculate all non-initial readings + track per-component worst RL
    shell_rls  = []
    head_rls   = []
    tube_rls   = []
    roof_rls   = []
    bottom_rls = []
    latest_date = None
    governing = []   # (remaining life, corrosion rate) of each zone's latest reading
    # API 653: Track bottom data for special 2× CR tank bottom calculation
    bottom_653_min_currents = []
    bottom_653_max_crs = []

    for tml in tmls:
        min_t = _get_min_t_for_tml(tml, equipment)

        readings = db.query(InspectionReading).filter(
            InspectionReading.tml_id == tml.id
        ).order_by(InspectionReading.inspection_date.asc()).all()

        if not readings:
            continue

        initial_reading = readings[0]  # Ti
        initial_reading.status = "BASELINE"
        initial_reading.controlling_cr = None
        initial_reading.controlling_rl = None
        initial_reading.controlling_point_index = None

        for idx, reading in enumerate(readings[1:], start=1):
            prev_reading = readings[idx - 1]  # Most recent prior reading

            initial_vals = [initial_reading.reading_1, initial_reading.reading_2, initial_reading.reading_3,
                            initial_reading.reading_4, initial_reading.reading_5, initial_reading.reading_6]
            prev_vals = [prev_reading.reading_1, prev_reading.reading_2, prev_reading.reading_3,
                         prev_reading.reading_4, prev_reading.reading_5, prev_reading.reading_6]
            cvs = [reading.reading_1, reading.reading_2, reading.reading_3,
                   reading.reading_4, reading.reading_5, reading.reading_6]

            loss_list, cr_list, rl_list, st_cr_list, lt_cr_list, cr_type_list, worst_rl, worst_cr, worst_idx = _compute_readings(
                cvs, initial_vals, prev_vals,
                initial_reading.inspection_date, prev_reading.inspection_date, reading.inspection_date,
                min_t,
                is_tank_bottom=(is_tank and _is_bottom_type(tml.tml_type))
            )

            reading.thickness_lost_csv = ",".join(loss_list)
            reading.corrosion_rate_csv = ",".join(cr_list)
            reading.remaining_life_csv = ",".join(rl_list)
            reading.short_term_cr_csv  = ",".join(st_cr_list)
            reading.long_term_cr_csv   = ",".join(lt_cr_list)
            reading.cr_type_csv        = ",".join(cr_type_list)

            _apply_status(reading, worst_rl, worst_cr, worst_idx, min_t)

            # Track latest date across all readings
            r_date = reading.inspection_date
            if r_date and (latest_date is None or r_date > latest_date):
                latest_date = r_date

        # Accumulate per-component governing life from the LATEST reading of this TML
        if len(readings) > 1:
            latest_rd = readings[-1]
            last_rl = latest_rd.controlling_rl
            last_cr = latest_rd.controlling_cr or Decimal("0")
            last_idx = latest_rd.controlling_point_index if latest_rd.controlling_point_index is not None else -1

            if last_idx >= 0 and last_rl is not None and last_rl < Decimal("999"):
                governing.append((last_rl, last_cr))
                if _is_head_type(tml.tml_type):
                    head_rls.append(last_rl)
                elif _is_tube_type(tml.tml_type):
                    tube_rls.append(last_rl)
                elif _is_roof_type(tml.tml_type):
                    roof_rls.append(last_rl)
                elif _is_bottom_type(tml.tml_type):
                    bottom_rls.append(last_rl)
                    if is_tank:
                        bottom_653_max_crs.append(last_cr)
                else:
                    shell_rls.append(last_rl)

            # API 653: Collect min current reading for tank bottom TMLs (latest reading)
            if is_tank and _is_bottom_type(tml.tml_type):
                last_cvs = [latest_rd.reading_1, latest_rd.reading_2, latest_rd.reading_3,
                            latest_rd.reading_4, latest_rd.reading_5, latest_rd.reading_6]
                valid_cvs = [v for v in last_cvs if v is not None]
                if valid_cvs:
                    bottom_653_min_currents.append(min(valid_cvs))

    db.commit()

    # ── Component-wise governing life ──
    shell_life  = min(shell_rls)  if shell_rls  else None
    head_life   = min(head_rls)   if head_rls   else None
    tube_life   = min(tube_rls)   if tube_rls   else None
    roof_life   = min(roof_rls)   if roof_rls   else None
    bottom_life = min(bottom_rls) if bottom_rls else None

    # API 653 Tank Bottom override: RL = (MIN_current - t_min) / (2 × MAX_CR)
    # Per client Excel formula: =((MIN(readings))-t_min)/(2*(MAX(CR)))
    if is_tank and bottom_653_min_currents and bottom_653_max_crs:
        overall_min_current = min(bottom_653_min_currents)
        overall_max_cr = max(bottom_653_max_crs)
        if bottom_min_t is not None and overall_min_current > bottom_min_t and overall_max_cr > 0:
            bottom_life = (overall_min_current - bottom_min_t) / (2 * overall_max_cr)
        elif bottom_min_t is None:
            bottom_life = None

    # Overall governing life = min of available component lives
    all_lives = [v for v in [shell_life, head_life, tube_life, roof_life, bottom_life] if v is not None]
    overall_life = min(all_lives) if all_lives else None

    # Corrosion rate of the governing (shortest-life) location
    overall_cr = None
    if governing:
        overall_cr = min(governing, key=lambda g: g[0])[1]
    if is_tank and bottom_653_max_crs and bottom_life is not None and bottom_life == overall_life:
        overall_cr = max(bottom_653_max_crs)

    # ── API 510 / 653 Dual Inspection Due Dates ──
    next_internal = None
    next_external = None
    if overall_life is not None and latest_date is not None:
        next_internal, next_external = calculate_api510_intervals(overall_life, latest_date)

    # ── Persist to Equipment row ──
    equipment.shell_controlling_life  = shell_life
    equipment.head_controlling_life   = head_life
    equipment.tube_controlling_life   = tube_life
    equipment.roof_controlling_life   = roof_life
    equipment.bottom_controlling_life = bottom_life
    equipment.remaining_life = overall_life        # None until at least 2 readings exist
    equipment.corrosion_rate = overall_cr
    if next_internal:
        equipment.next_internal_inspection_date = next_internal
    if next_external:
        equipment.next_external_inspection_date = next_external

    # ── Each inspection record gets the life / CR calculated for its own date ──
    all_readings = (
        db.query(InspectionReading)
        .filter(InspectionReading.tml_id.in_([t.id for t in tmls]))
        .all()
    ) if tmls else []
    for rec in db.query(InspectionRecord).filter(InspectionRecord.equipment_id == equipment_uuid).all():
        if rec.inspection_date is None:
            continue
        rec_day = rec.inspection_date.date()
        day_rd = [r for r in all_readings
                  if r.inspection_date and r.inspection_date.date() == rec_day
                  and r.controlling_rl is not None and r.controlling_rl < Decimal("999")]
        if day_rd:
            worst = min(day_rd, key=lambda r: r.controlling_rl)
            rec.remaining_life = worst.controlling_rl
            rec.corrosion_rate = worst.controlling_cr
        else:
            rec.remaining_life = None     # baseline / not calculable
            rec.corrosion_rate = None
            rec.next_inspection_due = None
            rec.next_internal_due = None
            rec.next_external_due = None

    # ── Persist to the latest InspectionRecord for this equipment ──
    latest_record = db.query(InspectionRecord).filter(
        InspectionRecord.equipment_id == equipment_uuid
    ).order_by(InspectionRecord.inspection_date.desc()).first()

    if latest_record:
        latest_record.shell_remaining_life  = shell_life
        latest_record.head_remaining_life   = head_life
        latest_record.tube_remaining_life   = tube_life
        latest_record.roof_remaining_life   = roof_life
        latest_record.bottom_remaining_life = bottom_life
        if overall_life is not None:
            latest_record.remaining_life = overall_life
            latest_record.corrosion_rate = overall_cr
        if next_internal:
            latest_record.next_internal_due = next_internal
            latest_record.next_inspection_due = next_internal
        if next_external:
            latest_record.next_external_due = next_external

    db.commit()
