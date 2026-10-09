"""
Storage-tank minimum thickness calculator — implements the client's `tank calculations.xlsx` exactly
(sheet 'Calculations' columns N-R and the lookup tables of sheet 'links').

  N  t-min (Bottom Shell)            = 4.9 x (H - 0.3) x D x G / (S x E)      H, D in metres, S in MPa -> mm
  O  t-min Bottom                    = 1.27 if the bottom has leak protection (API 652), else 2.54
  P  t-min Bottom at critical zone   = MAX(3, N / 2)
  Q  t-min annular plates            = table lookup (G < 1 / G >= 1, band of N, band of S)
  R  t-min roof                      = 2.2 (stored value of the sheet)

S is the sheet's "Allowable Product Stress, Lower Two Courses" of the chosen material (column O of the table).
The sheet is followed as written: nothing is added (no corrosion allowance, no temperature effect) and nothing is guessed.
Independent of the ASME vessel calculator, Equipment, CML and Inspection code.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.tank import TankMaterial
from app.schemas.tank import TankCalculateRequest, TankCalculateResponse

Q4 = Decimal("0.0001")
K_SHELL = Decimal("4.9")
HEAD_OFFSET_M = Decimal("0.3")
BOTTOM_WITH_PROTECTION_MM = Decimal("1.27")
BOTTOM_WITHOUT_PROTECTION_MM = Decimal("2.54")
CRITICAL_ZONE_FLOOR_MM = Decimal("3")
ROOF_MIN_MM = Decimal("2.2")
S_MAX_NOTE_MPA = Decimal("222")          # comment in cell J1 of the client's sheet

# Annular plate thickness (mm). Rows: shell thickness N bands (upper limit in mm, None = anything above the last limit);
# columns: S bands (upper limit in MPa, strictly below). Read from the formula of column Q / the 'links' sheet.
ANNULAR_LOW_G = {           # specific gravity G < 1
    "s_limits": [168, 186, 205, 223],
    "rows": [(19, [4.32, 5.08, 5.84, 7.62]),
             (25, [4.32, 5.59, 7.88, 9.65]),
             (32, [4.32, 6.6, 9.65, 12.19]),
             (38, [5.59, 8.64, 11.94, 14.99]),
             (None, [6.86, 10.16, 13.46, 17.27])],
}
ANNULAR_HIGH_G = {          # specific gravity G >= 1; thicker than 45 mm is "Out of Range"
    "s_limits": [190, 210, 220, 250],
    "rows": [(19, [6, 6, 7, 9]),
             (25, [6, 7, 10, 11]),
             (32, [6, 9, 12, 14]),
             (40, [8, 11, 14, 17]),
             (45, [9, 13, 16, 19])],
}


def annular_plate_min(specific_gravity: Decimal, shell_t_mm: Decimal, s_mpa: Decimal) -> Optional[Decimal]:
    """Column Q. None means the sheet's "Out of Range"."""
    table = ANNULAR_LOW_G if specific_gravity < 1 else ANNULAR_HIGH_G
    for limit, values in table["rows"]:
        if limit is None or shell_t_mm <= limit:
            for s_limit, v in zip(table["s_limits"], values):
                if s_mpa < s_limit:
                    return Decimal(str(v))
            return None                                   # S above the last band
    return None                                           # thicker than the last row


def _q4(x: Decimal) -> Decimal:
    return x.quantize(Q4, rounding=ROUND_HALF_UP)


def list_tank_materials(db: Session) -> List[TankMaterial]:
    return db.query(TankMaterial).order_by(TankMaterial.sheet_row, TankMaterial.name).all()


def calculate_tank_tmin(db: Session, req: TankCalculateRequest) -> TankCalculateResponse:
    mat = db.query(TankMaterial).filter(TankMaterial.name == req.material.strip()).first()
    if mat is None:
        raise ValueError(f"Material '{req.material}' is not in the tank material list of the client's sheet. "
                         f"Choose a material from the list.")
    if mat.s_lower_mpa is None or mat.s_lower_mpa <= 0:
        raise ValueError(f"No allowable stress is stored for '{mat.name}'.")
    S = Decimal(mat.s_lower_mpa)
    G, E = req.specific_gravity, req.joint_efficiency
    D_m = req.diameter_mm / Decimal(1000)
    H_m = req.height_mm / Decimal(1000)
    if H_m <= HEAD_OFFSET_M:
        raise ValueError("The tank height must be more than 0.3 m.")

    warnings: List[str] = []
    if mat.is_unknown:
        warnings.append("Unknown material: the sheet's 'Unknown Grade' row is used (S = 163 MPa, links sheet row 45). "
                        "The small table at the top of the same sheet lists 145 MPa for an unknown material; "
                        "please confirm which value applies.")
    if S > S_MAX_NOTE_MPA:
        warnings.append(f"S = {S:g} MPa is above 222 MPa, the maximum named in the note of cell J1 of the client's sheet.")

    n = _q4(K_SHELL * (H_m - HEAD_OFFSET_M) * D_m * G / (S * E))                       # N
    o = BOTTOM_WITH_PROTECTION_MM if req.bottom_leak_protection else BOTTOM_WITHOUT_PROTECTION_MM   # O
    p = _q4(max(CRITICAL_ZONE_FLOOR_MM, n / 2))                                          # P
    q = annular_plate_min(G, n, S)                                                      # Q
    if q is None:
        warnings.append(f"Annular plate: shell thickness {n} mm and S = {S:g} MPa are outside the table of the "
                        f"client's sheet ('Out of Range'); no value is given.")
    r = ROOF_MIN_MM                                                                     # R

    trace = (
        f"Tank: SG G = {G}, D = {D_m} m, H = {H_m} m, E = {E}\n"
        f"Material {mat.name}: S (lower two courses) = {S:g} MPa\n"
        f"  t-min (Bottom Shell)  = 4.9 x ({H_m} - 0.3) x {D_m} x {G} / ({S:g} x {E}) = {n} mm\n"
        f"  t-min Bottom          = {o} mm  (leak protection {'yes' if req.bottom_leak_protection else 'no'})\n"
        f"  t-min critical zone   = max(3, {n} / 2) = {p} mm\n"
        f"  t-min annular plates  = {q if q is not None else 'Out of Range'} mm\n"
        f"  t-min roof            = {r} mm\n"
    )
    return TankCalculateResponse(
        material=mat.name,
        is_unknown_material=bool(mat.is_unknown),
        allowable_stress_mpa=S,
        diameter_m=_q4(D_m),
        height_m=_q4(H_m),
        t_shell_bottom_course_mm=n,
        t_bottom_mm=o,
        t_bottom_critical_zone_mm=p,
        t_annular_mm=q,
        t_roof_mm=r,
        formula_shell="t = 4.9 × (H − 0.3) × D × G / (S × E)   (H and D in m, S in MPa, t in mm)",
        formula_bottom="1.27 mm if the bottom has leak detection / containment or a reinforced lining (API 652), otherwise 2.54 mm",
        formula_critical_zone="t = max(3, t_shell / 2)",
        formula_annular="From the sheet's table: specific gravity (below 1 / 1 or more), shell thickness band and S band",
        formula_roof="2.2 mm (value stored in the client's sheet)",
        warnings=warnings,
        calculation_trace=trace,
    )
