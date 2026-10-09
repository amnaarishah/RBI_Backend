"""
ASME Section VIII, Division 1 — t_min Calculation Engine
Formulas sourced strictly from client's T min calculation.xlsx
(rows 22-29) and ASME BPVC 2023 Section II Part D Table 1A.

NO changes to any existing Equipment, CML, or Inspection code.
"""
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.asme import AsmeMaterialStress
from app.schemas.asme import (
    TminCalculateRequest, TminCalculateResponse,
    AsmeMaterialListResponse, AsmeMaterialStressResponse,
    StressLookupResponse, StressTablePoint,
)


# ── Unit Conversion Helpers ───────────────────────────────────────────────────

def _pressure_to_bar(value: Decimal, unit: str) -> Decimal:
    """Convert pressure to bar (working unit for ASME formulas)."""
    unit_clean = unit.lower().replace(" ", "").replace("²", "2")
    if unit_clean == "bar":
        return value
    elif unit_clean == "psi":
        return (value * Decimal("0.0689476")).quantize(Decimal("0.000001"))
    elif unit_clean in ("mpa", "n/mm2"):
        return (value * Decimal("10")).quantize(Decimal("0.000001"))
    elif unit_clean in ("kg_cm2", "kgf/cm2", "kg/cm2"):
        return (value * Decimal("0.980665")).quantize(Decimal("0.000001"))
    elif unit_clean == "kpa":
        return (value * Decimal("0.01")).quantize(Decimal("0.000001"))
    raise ValueError(f"Unknown pressure unit: {unit}")


def _dim_to_mm(value: Decimal, unit: str) -> Decimal:
    """Convert dimension to mm."""
    unit_clean = unit.lower().strip()
    if unit_clean in ("mm", "millimeter", "millimeters"):
        return value
    elif unit_clean in ("in", "inch", "inches"):
        return (value * Decimal("25.4")).quantize(Decimal("0.0001"))
    elif unit_clean in ("m", "meter", "meters"):
        return (value * Decimal("1000")).quantize(Decimal("0.0001"))
    raise ValueError(f"Unknown dimension unit: {unit}")


def _temp_to_c(value: Decimal, unit: str) -> Decimal:
    """Convert temperature to Celsius."""
    unit_clean = unit.lower().replace(" ", "").replace("°", "").replace("deg", "")
    if unit_clean in ("c", "celsius"):
        return value
    elif unit_clean in ("f", "fahrenheit"):
        return ((value - Decimal("32")) * Decimal("5") / Decimal("9")).quantize(Decimal("0.01"))
    raise ValueError(f"Unknown temperature unit: {unit}")


def _temp_to_f(value_c: Decimal) -> int:
    """Convert Celsius to Fahrenheit (for DB lookup)."""
    return int((value_c * Decimal("9") / Decimal("5") + Decimal("32")).to_integral_value(ROUND_HALF_UP))


def _mpa_to_bar(mpa: Decimal) -> Decimal:
    return (mpa * Decimal("10")).quantize(Decimal("0.0001"))


def _mm_to_inches(mm: Decimal) -> Decimal:
    return (mm / Decimal("25.4")).quantize(Decimal("0.0001"))


def _bar_to_mpa(bar: Decimal) -> Decimal:
    return (bar / Decimal("10")).quantize(Decimal("0.0001"))


# ── Allowable Stress Lookup ───────────────────────────────────────────────────

def _lookup_stress(
    db: Session,
    spec_number: str,
    grade: str,
    product_form: str,
    temp_f: int,
) -> Optional[AsmeMaterialStress]:
    """
    Look up allowable stress S from DB at the table temperature (F) equal to, or else the next higher than, temp_f.
    A table line marked NP (not permitted) in Section VIII-1, or a temperature above its VIII-1 limit, is refused.
    """
    sample = (
        db.query(AsmeMaterialStress)
        .filter(
            AsmeMaterialStress.spec_number == spec_number.upper(),
            AsmeMaterialStress.grade == grade,
            AsmeMaterialStress.product_form == product_form,
        )
        .first()
    )
    if sample is not None:
        _check_viii1(sample, temp_f)

    # Exact match first
    exact = (
        db.query(AsmeMaterialStress)
        .filter(
            AsmeMaterialStress.spec_number == spec_number.upper(),
            AsmeMaterialStress.grade == grade,
            AsmeMaterialStress.product_form == product_form,
            AsmeMaterialStress.temp_f == temp_f,
        )
        .first()
    )
    if exact:
        return exact

    # NO interpolation (client requirement): a temperature between two table columns takes the value of the next
    # HIGHER column (lower stress, conservative). A cell the book leaves blank has no row, so the next listed
    # higher column is used. A temperature below the first column uses the first column.
    upper = (
        db.query(AsmeMaterialStress)
        .filter(
            AsmeMaterialStress.spec_number == spec_number.upper(),
            AsmeMaterialStress.grade == grade,
            AsmeMaterialStress.product_form == product_form,
            AsmeMaterialStress.temp_f >= temp_f,
        )
        .order_by(AsmeMaterialStress.temp_f.asc())
        .first()
    )
    if upper:
        return upper

    # Above the highest temperature for which the table lists a stress: the material is not permitted at this
    # temperature, so never fall back to the last value silently.
    max_f = (
        db.query(func.max(AsmeMaterialStress.temp_f))
        .filter(
            AsmeMaterialStress.spec_number == spec_number.upper(),
            AsmeMaterialStress.grade == grade,
            AsmeMaterialStress.product_form == product_form,
        )
        .scalar()
    )
    if max_f is not None:
        raise ValueError(_above_max_message(spec_number, grade, temp_f, max_f))
    return None


def _source_text(table_name: str, line, design_f, column_f) -> str:
    """Where S comes from: edition, table, the book's line number, and the temperature column that was used
    (the next higher column when the design temperature is not a column of the book)."""
    ref = f"ASME II-D 2023, {table_name}" + (f" ({line})" if line else "")
    if column_f == design_f:
        return f"{ref} · {column_f}°F"
    return f"{ref} · design {design_f}°F → {column_f}°F column"


def _above_max_message(spec_number: str, grade: str, temp_f, max_f, viii1: bool = False) -> str:
    max_c = int(round((float(max_f) - 32) * 5 / 9))
    where = ("the highest temperature permitted for this material in Section VIII Division 1 (ASME II-D Table 1A/1B, "
             "applicability column)") if viii1 else "the highest temperature listed in ASME Section II Part D for this material"
    return (
        f"Design temperature {temp_f}°F ({int(round((float(temp_f) - 32) * 5 / 9))}°C) is above {where} "
        f"({max_f}°F / {max_c}°C). "
        f"No allowable stress is available — use a different material or a custom stress value."
    )


def _not_permitted_message() -> str:
    return ("This table line is marked NP (not permitted) for Section VIII Division 1 in ASME II-D. "
            "Choose another material, or use a custom stress value and state its source.")


def _check_viii1(sample, temp_f) -> None:
    """Section VIII-1 applicability of a table line (read from the strength/applicability page of the book)."""
    if sample.viii1_permitted is False:
        raise ValueError(_not_permitted_message())
    if sample.viii1_limit_f is not None and temp_f > sample.viii1_limit_f:
        raise ValueError(_above_max_message(sample.spec_number, sample.grade, temp_f, sample.viii1_limit_f, viii1=True))


_LINE_RE = re.compile(r"L(\d+)_Pg(\d+)")


def _parse_page_line(grade: str):
    """The PDF extraction stored 'L<line>_Pg<page>' in the grade column."""
    m = _LINE_RE.search(grade or "")
    return (int(m.group(2)), int(m.group(1))) if m else (None, None)


def _stress_values(row) -> dict:
    mpa = row.allowable_stress_mpa
    return {
        "stress_mpa": mpa,
        "stress_ksi": row.allowable_stress_ksi,
        "stress_psi": row.allowable_stress_psi,
        "stress_bar": row.allowable_stress_bar,
    }


def get_stress_lookup(db: Session, spec_number: str, grade: str, product_form: str,
                      temperature: Decimal, temperature_unit: str) -> StressLookupResponse:
    """Allowable stress S for a material at a design temperature, plus the full table,
    the source (Table 1A/1B, page, line) and any interpolation note."""
    T_c = _temp_to_c(temperature, temperature_unit)
    T_f = _temp_to_f(T_c)

    rows = (
        db.query(AsmeMaterialStress)
        .filter(
            AsmeMaterialStress.spec_number == spec_number.upper(),
            AsmeMaterialStress.grade == grade,
            AsmeMaterialStress.product_form == product_form,
        )
        .order_by(AsmeMaterialStress.temp_f)
        .all()
    )
    if not rows:
        raise ValueError(f"Material '{spec_number} {grade}' not found in ASME Section II Part D tables.")

    page, line = _parse_page_line(grade)
    table_name = "Table 1A" if (rows[0].material_group or "").lower().startswith("ferrous") else "Table 1B"
    sample = rows[0]
    limit8 = sample.viii1_limit_f
    min_f, table_max_f = rows[0].temp_f, rows[-1].temp_f
    # the highest temperature we may use: the last stress column, or the Section VIII-1 limit if that is lower
    max_f = min(table_max_f, limit8) if limit8 is not None else table_max_f
    table = [
        StressTablePoint(temp_f=r.temp_f, temp_c=r.temp_c, stress_ksi=r.allowable_stress_ksi,
                         stress_mpa=r.allowable_stress_mpa)
        for r in rows if r.temp_f <= max_f
    ]
    base = dict(
        table_name=table_name, page=page, line=line, spec_number=spec_number, grade=grade,
        product_form=product_form, temperature_c=T_c, temperature_f=Decimal(T_f),
        min_temp_f=min_f, max_temp_f=max_f, max_temp_c=int(round((max_f - 32) * 5 / 9)), table=table,
        viii1_limit_f=limit8, min_tensile_ksi=sample.min_tensile_ksi, min_yield_ksi=sample.min_yield_ksi,
        notes_text=sample.notes_text, size_thickness=sample.size_thickness,
    )

    if sample.viii1_permitted is False:
        return StressLookupResponse(status="not_permitted", message=_not_permitted_message(), **base)
    if T_f > max_f:
        return StressLookupResponse(
            status="above_max_temperature",
            message=_above_max_message(spec_number, grade, T_f, max_f, viii1=limit8 is not None and limit8 <= table_max_f),
            **base)

    row = _lookup_stress(db, spec_number, grade, product_form, T_f)
    source = _source_text(table_name, line, T_f, row.temp_f)
    return StressLookupResponse(status="ok", interpolated=False, table_temperature_f=row.temp_f, source=source,
                                **_stress_values(row), **base)


# ── ASME Formulas (Client sheet rows 22-29, ASME Section VIII Div 1) ──────────

ENDS_16_67 = Decimal("16.6667")      # L/r of a standard flanged & dished head (r = 0.06 L)
Q4 = Decimal("0.0001")


def _calculate_tmin(
    component_type: str,
    P: Decimal,    # Design Pressure in bar
    R: Decimal,    # Inside Radius in mm
    D: Decimal,    # Inside Diameter in mm
    S: Decimal,    # Allowable Stress in bar
    E: Decimal,    # Joint Efficiency
    L: Optional[Decimal] = None,       # torispherical: inside crown radius (mm)
    r: Optional[Decimal] = None,       # torispherical: inside knuckle radius (mm)
    C: Optional[Decimal] = None,       # flat head: attachment factor (UG-34)
    alpha_deg: Optional[Decimal] = None,   # conical head: half apex angle (degrees)
):
    """
    Returns (t_min_mm, asme_clause, formula_string, extras)

    Formulas follow the client's T min calculation.xlsx (rows 22-29); torispherical uses the
    general UG-32 form with the factor M (the sheet's 0.885 is the special case L/r = 16 2/3).
    `extras` carries the factors actually used (L, r, M, C, alpha) and any warnings.
    """
    ct = component_type.lower()
    extras: dict = {"warnings": []}

    if ct == "cylindrical_shell":
        # ASME UG-27(c)(1): t = P*R / (S*E - 0.6*P)   (thin wall)
        thick_limit = Decimal("0.385") * S * E
        if P > thick_limit:
            # ASME Section VIII Div 1, Mandatory Appendix 1, paragraph 1-2 (the page the client sent):
            #   (a) circumferential stress, P > 0.385 S E :  t = R * [exp(P / SE) - 1]
            #   (b) longitudinal stress,    P > 1.25  S E :  t = R * (sqrt(Z) - 1),  Z = P / SE + 1
            # When both apply, the larger thickness is the one that satisfies both.
            ratio = P / (S * E)
            if ratio > 600:
                raise ValueError("Design pressure is far above S × E: the thick-wall equation has no practical solution.")
            t_a = R * (Decimal(str(math.exp(float(ratio)))) - Decimal("1"))
            extras["thick_wall_used"] = True
            t, clause = t_a, "ASME Section VIII Div 1 — Appendix 1-2(a) (circumferential stress, P > 0.385·S·E)"
            formula = "t = R × [exp(P / (S×E)) − 1]"
            if P > Decimal("1.25") * S * E:
                t_b = R * (Decimal(str(math.sqrt(float(ratio + 1)))) - Decimal("1"))
                extras["longitudinal_t_mm"] = t_b.quantize(Q4)
                formula += f"   |   1-2(b): t = R × (√Z − 1), Z = P/(S×E) + 1  →  {t_b.quantize(Q4)} mm"
                if t_b > t_a:
                    t, clause = t_b, "ASME Section VIII Div 1 — Appendix 1-2(b) (longitudinal stress, P > 1.25·S·E)"
            return (t.quantize(Q4), clause, formula, extras)
        denom = S * E - Decimal("0.6") * P
        t = (P * R) / denom
        return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-27(c)(1)", "t = (P × R) / (S × E − 0.6 × P)", extras)

    elif ct == "spherical_shell":
        # ASME UG-27(d): t = P*R / (2*S*E - 0.2*P)
        denom = Decimal("2") * S * E - Decimal("0.2") * P
        t = (P * R) / denom
        return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-27(d)", "t = (P × R) / (2 × S × E − 0.2 × P)", extras)

    elif ct == "ellipsoidal_head":
        # ASME UG-32(d): t = P*D / (2*S*E - 0.2*P)
        denom = Decimal("2") * S * E - Decimal("0.2") * P
        t = (P * D) / denom
        return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-32(d) [2:1 Ellipsoidal]",
                "t = (P × D) / (2 × S × E − 0.2 × P)", extras)

    elif ct == "hemispherical_head":
        # ASME UG-32(f): t = P*L / (2*S*E - 0.2*P), L = inside spherical radius = D/2
        denom = Decimal("2") * S * E - Decimal("0.2") * P
        t = (P * R) / denom
        return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-32(f) [Hemispherical]",
                "t = (P × L) / (2 × S × E − 0.2 × P),  L = D/2", extras)

    elif ct == "torispherical_head":
        # ASME UG-32(e): standard flanged & dished head, L = inside crown radius, r = knuckle radius
        L = L if L is not None else D                     # default: L = D
        r = r if r is not None else (Decimal("0.06") * D).quantize(Q4)   # default: r = 6 % of D
        if L <= 0 or r <= 0:
            raise ValueError("Crown radius L and knuckle radius r must be greater than zero.")
        if r > L:
            raise ValueError("Knuckle radius r cannot be larger than the crown radius L.")
        if r < Decimal("0.06") * D - Decimal("0.0001"):
            extras["warnings"].append(
                "Knuckle radius is smaller than 6 % of the diameter; ASME UG-32(e) requires r ≥ 0.06 × D "
                "(and ≥ 3 × t).")
        if L > D + Decimal("0.0001"):
            extras["warnings"].append("Crown radius L is larger than the diameter D (UG-32(e) limits L to D).")
        extras.update(crown_radius_mm=L, knuckle_radius_mm=r)
        ratio = L / r
        if abs(ratio - ENDS_16_67) < Decimal("0.001"):
            # L/r = 16 2/3  ->  M = 1.77  ->  client's formula
            t = (Decimal("0.885") * P * L) / (S * E - Decimal("0.1") * P)
            extras["m_factor"] = Decimal("1.77")
            return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-32(e) [Torispherical/F&D, L/r = 16 2/3]",
                    "t = (0.885 × P × L) / (S × E − 0.1 × P)", extras)
        M = (Decimal("3") + Decimal(str(math.sqrt(float(ratio))))) / Decimal("4")
        t = (P * L * M) / (Decimal("2") * S * E - Decimal("0.2") * P)
        extras["m_factor"] = M.quantize(Decimal("0.0001"))
        return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-32(e) [Torispherical/F&D]",
                "t = (P × L × M) / (2 × S × E − 0.2 × P),  M = ¼ × (3 + √(L/r))", extras)

    elif ct == "conical_head":
        # ASME UG-32(g): t = P*D / (2*cos(alpha)*(S*E - 0.6*P)), alpha <= 30 degrees
        if alpha_deg is None:
            raise ValueError("Enter the half apex angle (α) of the conical head.")
        if alpha_deg <= 0 or alpha_deg > 30:
            raise ValueError("Half apex angle α must be greater than 0° and not more than 30° "
                             "(UG-32(g)); larger angles need the Appendix 1-5 analysis.")
        cos_a = Decimal(str(math.cos(math.radians(float(alpha_deg)))))
        t = (P * D) / (Decimal("2") * cos_a * (S * E - Decimal("0.6") * P))
        extras["cone_half_angle_deg"] = alpha_deg
        return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-32(g) [Conical head, α ≤ 30°]",
                "t = (P × D) / (2 × cos α × (S × E − 0.6 × P))", extras)

    elif ct == "flat_head":
        # ASME UG-34: t = d * sqrt(C*P / (S*E)), d = inside diameter
        C_used = C if C is not None else Decimal("0.33")
        if C_used <= 0:
            raise ValueError("Flat head factor C must be greater than zero.")
        inner = (C_used * P) / (S * E)
        t = D * Decimal(str(math.sqrt(float(inner))))
        extras["c_factor"] = C_used
        extras["c_is_default"] = C is None
        return (t.quantize(Q4), "ASME Section VIII Div 1 — UG-34 [Flat Head]",
                f"t = d × √(C × P / (S × E))  [C = {C_used}]", extras)

    else:
        raise ValueError(f"Unknown component_type: {component_type}")


def _calculate_mawp(component_type: str, t_net: Decimal, R: Decimal, D: Decimal, S: Decimal, E: Decimal,
                    extras: dict) -> Decimal:
    """Maximum allowable working pressure (bar) for a given net thickness (t − CA).
    These are the exact inverses of the t_min equations above."""
    ct = component_type.lower()
    if ct == "cylindrical_shell":
        p_thin = (S * E * t_net) / (R + Decimal("0.6") * t_net)                # UG-27(c)(1)
        if t_net > R / 2 or p_thin > Decimal("0.385") * S * E:
            # thick wall: Mandatory Appendix 1-2(a)(2) (and 1-2(b)(4) when it applies); the lower pressure governs
            extras["mawp_thick"] = True
            p = S * E * Decimal(str(math.log(float((R + t_net) / R))))
            if t_net > R / 2 or p > Decimal("1.25") * S * E:
                p = min(p, S * E * (((R + t_net) / R) ** 2 - Decimal("1")))
            return p
        return p_thin
    if ct in ("spherical_shell", "hemispherical_head"):
        return (Decimal("2") * S * E * t_net) / (R + Decimal("0.2") * t_net)   # UG-27(d) / UG-32(f)
    if ct == "ellipsoidal_head":
        return (Decimal("2") * S * E * t_net) / (D + Decimal("0.2") * t_net)   # UG-32(d)
    if ct == "torispherical_head":
        L, M = extras["crown_radius_mm"], extras["m_factor"]
        return (Decimal("2") * S * E * t_net) / (L * M + Decimal("0.2") * t_net)  # UG-32(e)
    if ct == "conical_head":
        cos_a = Decimal(str(math.cos(math.radians(float(extras["cone_half_angle_deg"])))))
        return (Decimal("2") * S * E * t_net * cos_a) / (D + Decimal("1.2") * t_net * cos_a)  # UG-32(g)
    if ct == "flat_head":
        return (S * E * t_net * t_net) / (extras["c_factor"] * D * D)          # UG-34
    raise ValueError(f"Unknown component_type: {component_type}")


def _mawp_formula(component_type: str, extras: Optional[dict] = None) -> str:
    """The MAWP equation as shown to the user: the t_min equation of the same component, turned round
    (pressure from the thickness), with t = given thickness − CA."""
    f = {
        "cylindrical_shell": "P = (S × E × t) / (R + 0.6 × t)",
        "spherical_shell": "P = (2 × S × E × t) / (R + 0.2 × t)",
        "hemispherical_head": "P = (2 × S × E × t) / (R + 0.2 × t)",
        "ellipsoidal_head": "P = (2 × S × E × t) / (D + 0.2 × t)",
        "torispherical_head": "P = (2 × S × E × t) / (L × M + 0.2 × t)",
        "conical_head": "P = (2 × S × E × t × cos α) / (D + 1.2 × t × cos α)",
        "flat_head": "P = (S × E × t²) / (C × d²)",
    }[component_type.lower()]
    if component_type.lower() == "cylindrical_shell" and (extras or {}).get("mawp_thick"):
        f = "P = S × E × ln((R + t) / R)   (Appendix 1-2(a), thick wall)"
    return f"{f},  t = given thickness − CA"


def _to_mm(value: Decimal, unit: Optional[str]) -> Decimal:
    return _dim_to_mm(value, unit or "mm")


# ── Main Calculator Entry Point ───────────────────────────────────────────────

def calculate_tmin(
    db: Session,
    req: TminCalculateRequest,
) -> TminCalculateResponse:
    """
    Main entry point: takes user inputs, looks up S from DB,
    runs ASME formula, returns full breakdown.

    t_min is the pressure-design thickness WITHOUT corrosion allowance (this is the value used
    for remaining-life calculations). The design thickness t + CA is returned separately.
    """
    # 1. Convert inputs to working units (bar, mm, °C)
    P_bar = _pressure_to_bar(req.design_pressure, req.pressure_unit)
    D_mm  = _dim_to_mm(req.inside_diameter, req.dimension_unit)
    R_mm  = (D_mm / Decimal("2")).quantize(Decimal("0.0001"))
    T_c   = _temp_to_c(req.design_temperature, req.temperature_unit)
    T_f   = _temp_to_f(T_c)
    E     = req.joint_efficiency
    if P_bar <= 0 or D_mm <= 0:
        raise ValueError("Design pressure and inside diameter must be greater than zero.")
    if not (Decimal("0") < E <= Decimal("1")):
        raise ValueError("Joint efficiency E must be greater than 0 and not more than 1.")

    # 2. Resolve Allowable Stress S
    stress_source = f"ASME Table 1A — {req.spec_number} {req.grade}, {T_f}°F"
    using_custom = bool(req.custom_stress or req.custom_stress_mpa or req.custom_stress_psi)
    custom_source = (req.custom_stress_source or "").strip()
    if using_custom and not custom_source:
        raise ValueError("Please state where the stress value comes from (for example: client datasheet or "
                         "Code Case number). A stress that is not taken from the ASME table needs a source.")
    if req.custom_stress:
        factor = {"ksi": Decimal("6.89476"), "psi": Decimal("0.00689476"),
                  "mpa": Decimal("1"), "bar": Decimal("0.1")}.get((req.custom_stress_unit or "mpa").lower())
        if factor is None:
            raise ValueError(f"Unknown stress unit: {req.custom_stress_unit}")
        S_mpa = (req.custom_stress * factor).quantize(Decimal("0.001"))
        S_bar = _mpa_to_bar(S_mpa)
        stress_source = (f"User value ({req.custom_stress} {(req.custom_stress_unit or 'MPa')}) — NOT from the ASME "
                         f"table. Source: {custom_source}")
    elif req.custom_stress_mpa:
        S_mpa = req.custom_stress_mpa
        S_bar = _mpa_to_bar(S_mpa)
        stress_source = f"User value ({req.custom_stress_mpa} MPa) — NOT from the ASME table. Source: {custom_source}"
    elif req.custom_stress_psi:
        S_mpa = (req.custom_stress_psi * Decimal("0.00689476")).quantize(Decimal("0.001"))
        S_bar = _mpa_to_bar(S_mpa)
        stress_source = f"User value ({req.custom_stress_psi} psi) — NOT from the ASME table. Source: {custom_source}"
    else:
        row = _lookup_stress(db, req.spec_number, req.grade, req.product_form, T_f)
        if not row or not row.allowable_stress_mpa:
            raise ValueError(
                f"Material '{req.spec_number} {req.grade}' not found in ASME Table 1A "
                f"at temperature {T_f}°F ({int(T_c)}°C). "
                f"Please use 'Custom S Override' to enter the value manually."
            )
        S_mpa = row.allowable_stress_mpa
        S_bar = row.allowable_stress_bar or _mpa_to_bar(S_mpa)
        page, line = _parse_page_line(req.grade)
        group = db.query(AsmeMaterialStress.material_group).filter(
            AsmeMaterialStress.spec_number == req.spec_number.upper(),
            AsmeMaterialStress.grade == req.grade,
            AsmeMaterialStress.product_form == req.product_form,
        ).limit(1).scalar()
        table_name = "Table 1A" if (group or "").lower().startswith("ferrous") else "Table 1B"
        stress_source = _source_text(table_name, line, T_f, row.temp_f)

    # 3. Thick-wall applicability: cylindrical shell P > 0.385 S E (Appendix 1-2, calculated);
    #    spherical shell / hemispherical head P > 0.665 S E (UG-27(d), not calculated here).
    #    Other heads have no such limit in the client's list.
    limit_factor = {"cylindrical_shell": Decimal("0.385"), "spherical_shell": Decimal("0.665"),
                    "hemispherical_head": Decimal("0.665")}.get(req.component_type.lower())
    thick_wall_limit = (limit_factor * S_bar * E) if limit_factor else None
    thick_wall_flag  = bool(thick_wall_limit is not None and P_bar > thick_wall_limit)
    thick_wall_warn  = None

    # 4. Run ASME formula
    L_mm = _to_mm(req.crown_radius, req.dimension_unit) if req.crown_radius else None
    r_mm = _to_mm(req.knuckle_radius, req.dimension_unit) if req.knuckle_radius else None
    t_mm, clause, formula_str, extras = _calculate_tmin(
        req.component_type, P_bar, R_mm, D_mm, S_bar, E,
        L=L_mm, r=r_mm, C=req.flat_head_c, alpha_deg=req.cone_half_angle_deg,
    )
    t_inches = _mm_to_inches(t_mm)
    if thick_wall_flag:
        if extras.get("thick_wall_used"):
            thick_wall_warn = (f"P ({P_bar:.4f} bar) > 0.385 × S × E ({thick_wall_limit:.4f} bar): "
                               f"the Appendix 1-2(a) thick-wall equation (circumferential stress) was used.")
            if extras.get("longitudinal_t_mm") is not None:
                thick_wall_warn += (f" P > 1.25 × S × E: the longitudinal equation 1-2(b) gives "
                                    f"{extras['longitudinal_t_mm']} mm; the larger thickness governs.")
        else:
            thick_wall_warn = (f"⚠️ P ({P_bar:.4f} bar) > 0.665 × S × E ({thick_wall_limit:.4f} bar). "
                               f"The thin-wall spherical equation (UG-27(d)) is not valid here; the ASME "
                               f"Appendix 1-3 thick-wall equation is required and is not calculated.")

    # 5. Corrosion allowance: design thickness (t + CA) is shown separately from t_min
    if req.corrosion_allowance is not None:
        ca_mm = _to_mm(req.corrosion_allowance, req.corrosion_allowance_unit)
    elif req.corrosion_allowance_mm is not None:
        ca_mm = req.corrosion_allowance_mm
    else:
        ca_mm = None
    if ca_mm is not None and ca_mm < 0:
        raise ValueError("Corrosion allowance cannot be negative.")
    design_mm = (t_mm + (ca_mm or Decimal("0"))).quantize(Q4)

    # 6. MAWP from the GIVEN (nominal) thickness: net thickness = t_given − CA
    mawp_bar = None
    if req.given_thickness:
        t_given = _to_mm(req.given_thickness, req.given_thickness_unit)
        t_net = t_given - (ca_mm or Decimal("0"))
        if t_net <= 0:
            raise ValueError("Given thickness minus corrosion allowance must be greater than zero.")
        mawp_bar = _calculate_mawp(req.component_type, t_net, R_mm, D_mm, S_bar, E, extras).quantize(Q4)

    # 7. Build human-readable calculation trace
    trace = (
        f"INPUTS:\n"
        f"  Design Pressure P     = {P_bar:.6f} bar\n"
        f"  Inside Radius R       = {R_mm:.4f} mm\n"
        f"  Inside Diameter D     = {D_mm:.4f} mm\n"
        f"  Design Temperature T  = {T_c:.1f} °C ({T_f} °F)\n"
        f"  Joint Efficiency E    = {E}\n"
        f"  Allowable Stress S    = {S_mpa:.3f} MPa = {S_bar:.4f} bar\n"
        f"  Source: {stress_source}\n"
    )
    if "crown_radius_mm" in extras:
        trace += (f"  Crown radius L        = {extras['crown_radius_mm']:.4f} mm\n"
                  f"  Knuckle radius r      = {extras['knuckle_radius_mm']:.4f} mm\n"
                  f"  M factor              = {extras['m_factor']}\n")
    if "c_factor" in extras:
        trace += f"  Flat head factor C    = {extras['c_factor']}" + (" (default)" if extras.get("c_is_default") else " (entered)") + "\n"
    if "cone_half_angle_deg" in extras:
        trace += f"  Half apex angle α     = {extras['cone_half_angle_deg']}°\n"
    trace += (
        f"\nFORMULA [{clause}]:\n"
        f"  {formula_str}\n\n"
        f"RESULT:\n"
        f"  t_min = {t_mm:.4f} mm  ({t_inches:.4f} inches)   [without corrosion allowance]\n"
    )
    if ca_mm is not None:
        trace += f"  CA    = {ca_mm:.4f} mm  ->  design thickness t + CA = {design_mm:.4f} mm\n"
    if mawp_bar is not None:
        trace += f"  MAWP  = {mawp_bar:.4f} bar  (from given thickness − CA)\n"
    if thick_wall_warn:
        trace += f"\n{thick_wall_warn}\n"
    for w in extras["warnings"]:
        trace += f"\n⚠️ {w}\n"

    return TminCalculateResponse(
        component_type=req.component_type,
        asme_clause=clause,
        pressure_bar=P_bar,
        radius_mm=R_mm,
        diameter_mm=D_mm,
        temperature_c=T_c,
        joint_efficiency=E,
        allowable_stress_mpa=S_mpa,
        allowable_stress_bar=S_bar,
        stress_source=stress_source,
        thick_wall_flag=thick_wall_flag,
        thick_wall_warning=thick_wall_warn,
        t_min_mm=t_mm,
        t_min_inches=t_inches,
        mawp_bar=mawp_bar,
        formula_used=formula_str,
        mawp_formula=_mawp_formula(req.component_type, extras) if mawp_bar is not None else None,
        calculation_trace=trace,
        corrosion_allowance_mm=ca_mm,
        design_thickness_mm=design_mm,
        design_thickness_inches=_mm_to_inches(design_mm),
        crown_radius_mm=extras.get("crown_radius_mm"),
        knuckle_radius_mm=extras.get("knuckle_radius_mm"),
        m_factor=extras.get("m_factor"),
        c_factor=extras.get("c_factor"),
        c_is_default=extras.get("c_is_default"),
        cone_half_angle_deg=extras.get("cone_half_angle_deg"),
        warnings=extras["warnings"],
    )


# ── Material List for Dropdown ────────────────────────────────────────────────

def _composition(description: Optional[str]) -> str:
    """Text before the specification in the table row, e.g. 'Carbon steel Plate'."""
    from app.seed.asme_parse import SPEC_RE
    if not description:
        return ""
    description = re.sub(r"ð\s*\d+\s*Þ", " ", description)    # book revision marker "(23)"
    m = SPEC_RE.search(description)
    words = (description[:m.start()] if m else description).split()
    junk = {"?", "-", "�", "…", "ð", "Þ"}
    while words and (words[0] in junk or re.fullmatch(r"\d{1,2}", words[0])):   # stray line numbers
        words.pop(0)
    while words and words[-1] in junk:
        words.pop()
    return " ".join(w for w in words if w not in ("?", "�")).strip()


def get_materials_list(db: Session, include_not_permitted: bool = False) -> List[AsmeMaterialListResponse]:
    """Return distinct materials (one per table line of ASME II-D Table 1A / 1B) for the searchable dropdown.
    Identity columns come from the extraction (see app.seed.backfill_asme_metadata). Lines marked NP (not
    permitted) in Section VIII-1 are left out: this software is for Section VIII Division 1."""
    M = AsmeMaterialStress
    q = (
        db.query(
            M.spec_number, M.grade, M.product_form, M.material_group, M.table_name, M.pdf_page,
            M.line_no, M.uns_no, M.clean_spec, M.clean_grade, M.product_type, M.condition_text,
            M.description, M.min_tensile_ksi, M.min_yield_ksi, M.viii1_limit_f, M.viii1_permitted,
            M.notes_text, M.size_thickness,
        )
        .distinct()
    )
    if not include_not_permitted:
        q = q.filter(or_(M.viii1_permitted.is_(None), M.viii1_permitted.is_(True)))
    rows = q.order_by(M.material_group, M.clean_spec, M.clean_grade, M.pdf_page, M.line_no).all()

    entries = []
    for r in rows:
        comp = _composition(r.description)
        spec = r.clean_spec or r.spec_number
        grade = r.clean_grade or ""
        bits = [f"{spec} {grade}".strip()]
        if r.uns_no:
            bits.append(r.uns_no)
        if comp:
            bits.append(comp)
        if r.condition_text and re.search(r"[A-Za-z]", r.condition_text):   # skip bare P-No./Group numbers
            bits.append(r.condition_text)
        entries.append((r, spec, grade, comp, bits))

    # Two table lines can have the same name (same spec, grade, UNS, form). Tell them apart with what really
    # differs in the book: size/thickness, minimum strength, the Section VIII-1 limit, then the notes.
    groups = {}
    for e in entries:        # punctuation / spacing alone ("16Cr 12Ni" vs "16Cr - 12Ni") is not a difference
        groups.setdefault(re.sub(r"[\s\-.,]+", "", " ".join(e[4]).lower()), []).append(e)

    def parts(r):
        return {
            "size": f"{r.size_thickness} in." if r.size_thickness else None,
            "yield": f"yield {float(r.min_yield_ksi):g} ksi" if r.min_yield_ksi is not None else None,
            "tensile": f"tensile {float(r.min_tensile_ksi):g} ksi" if r.min_tensile_ksi is not None else None,
            "limit": f"VIII-1 to {r.viii1_limit_f}°F" if r.viii1_limit_f is not None else None,
            "notes": f"notes {r.notes_text}" if r.notes_text else None,
        }

    extra = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        ps = [parts(m[0]) for m in members]
        keys = []
        for k in ("size", "yield", "tensile", "limit", "notes"):
            if len({p[k] for p in ps}) > 1:
                keys.append(k)
                if len({tuple(p[x] for x in keys) for p in ps}) == len(ps):
                    break
        for m, p in zip(members, ps):
            extra[id(m)] = [p[k] for k in keys if p[k]]

    names = {id(e): " · ".join(e[4] + extra.get(id(e), [])) for e in entries}
    def _key(n):
        return re.sub(r"[\s\-.,]+", "", n.lower())

    taken = {}
    for n in names.values():
        taken[_key(n)] = taken.get(_key(n), 0) + 1

    res = []
    for e in entries:
        r, spec, grade, comp, bits = e
        name = names[id(e)]
        if taken[_key(name)] > 1 and r.line_no:       # still identical (e.g. Class 1 / Class 2): only the book line tells them apart
            name += f" (line {r.line_no})"
        display_label = f"{name} — {r.material_group}"
        res.append(AsmeMaterialListResponse(
            spec_number=r.spec_number, grade=r.grade, product_form=r.product_form,
            material_group=r.material_group, display_label=display_label,
            clean_spec=spec, clean_grade=grade, table_name=r.table_name, pdf_page=r.pdf_page,
            line_no=r.line_no, uns_no=r.uns_no, product_type=r.product_type, composition=comp or None,
            condition_text=r.condition_text, min_tensile_ksi=r.min_tensile_ksi, min_yield_ksi=r.min_yield_ksi,
            viii1_limit_f=r.viii1_limit_f, notes_text=r.notes_text, size_thickness=r.size_thickness,
        ))
    return res


# ── "Did you mean ...?" for a material that is not in the ASME tables ─────────

def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _norm_grade(g: str) -> str:
    g = re.sub(r"[\s\-.]", "", (g or "").upper())
    return re.sub(r"^(TYPE|GRADE|GR|TP|CLASS|CL)", "", g)


def _spec_digits(spec: str) -> str:
    m = re.search(r"\d+", spec or "")
    return m.group(0) if m else ""


def suggest_materials(db: Session, spec: str, grade: str, limit: int = 6) -> List[AsmeMaterialListResponse]:
    """Materials that look like a spec / grade that is not in the tables (typing mistakes such as
    "SA-375 Grade C" for "SA-285 Grade C"). Nothing is chosen for the user; these are only suggestions."""
    want_digits, want_grade = _spec_digits(spec), _norm_grade(grade)
    if not want_digits:
        return []
    scored, seen = [], set()
    for m in get_materials_list(db):
        key = (m.clean_spec, m.clean_grade)
        if key in seen:
            continue
        dist = _levenshtein(want_digits, _spec_digits(m.clean_spec))
        grade_hit = bool(want_grade) and _norm_grade(m.clean_grade) == want_grade
        if dist > 3 or not (grade_hit or dist <= 1):
            continue
        seen.add(key)
        # similar number + same grade; plate gets a small bonus (vessel shells and heads are mostly plate)
        score = (3 if grade_hit else 0) + (4 - dist) + (1 if m.product_type == "Plate/Sheet" else 0)
        scored.append((score, grade_hit, m))
    scored.sort(key=lambda t: (-t[0], not t[1], t[2].clean_spec, t[2].clean_grade))
    return [m for _, _, m in scored[:limit]]
