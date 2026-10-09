"""
ASME Calculator Schemas — Pydantic models for request/response.
Strictly isolated from all existing schemas.
"""
from typing import Optional, List
from decimal import Decimal
from pydantic import BaseModel


# ── Material Stress Lookup ────────────────────────────────────────────────────

class AsmeMaterialStressResponse(BaseModel):
    id: str
    spec_number: str
    grade: str
    product_form: str
    material_group: Optional[str]
    code_edition: str
    temp_c: int
    temp_f: int
    allowable_stress_ksi: Optional[Decimal]
    allowable_stress_psi: Optional[Decimal]
    allowable_stress_mpa: Optional[Decimal]
    allowable_stress_bar: Optional[Decimal]
    min_tensile_strength_ksi: Optional[Decimal]
    min_yield_strength_ksi: Optional[Decimal]
    notes: Optional[str]

    model_config = {"from_attributes": True}


class AsmeMaterialListResponse(BaseModel):
    """Distinct material list for dropdown search"""
    spec_number: str
    grade: str
    product_form: str
    material_group: Optional[str]
    display_label: str   # e.g. "Carbon steel Plate SA-516 70 — Ferrous"
    clean_spec: str      # e.g. "SA-516"  (human-readable, for saving to profile)
    clean_grade: str     # e.g. "70"      (human-readable, for saving to profile)
    table_name: Optional[str] = None      # "Table 1A" | "Table 1B"
    pdf_page: Optional[int] = None
    line_no: Optional[int] = None
    uns_no: Optional[str] = None
    product_type: Optional[str] = None    # Plate/Sheet | Pipe | Tube | Forging | Casting | ...
    composition: Optional[str] = None     # e.g. "Carbon steel Plate"
    condition_text: Optional[str] = None  # class / size / thickness condition from the table
    # strength / Section VIII-1 applicability of the table line (page after the specification page of the book)
    min_tensile_ksi: Optional[Decimal] = None
    min_yield_ksi: Optional[Decimal] = None
    viii1_limit_f: Optional[int] = None   # highest temperature allowed in Section VIII-1, F
    notes_text: Optional[str] = None      # the book's notes of the line (G3, W6, ...)
    size_thickness: Optional[str] = None  # Table 1B size / thickness range, in.

    model_config = {"from_attributes": True}


# ── Stress lookup (shown in the UI before calculating) ───────────────────────

class StressTablePoint(BaseModel):
    temp_f: int
    temp_c: int
    stress_ksi: Optional[Decimal]
    stress_mpa: Optional[Decimal]


class StressLookupResponse(BaseModel):
    status: str                  # "ok" | "above_max_temperature" | "not_permitted" (NP in Section VIII-1)
    message: Optional[str] = None
    table_name: str              # "Table 1A" (ferrous) | "Table 1B" (non-ferrous)
    page: Optional[int] = None   # PDF page / table line of the material in ASME II-D (from the extraction)
    line: Optional[int] = None
    spec_number: str
    grade: str
    product_form: str
    temperature_c: Decimal
    temperature_f: Decimal
    min_temp_f: int
    max_temp_f: int
    max_temp_c: int
    interpolated: bool = False          # always False: no interpolation, the next higher table temperature is used
    table_temperature_f: Optional[int] = None   # the table temperature (F) whose value is used
    stress_ksi: Optional[Decimal] = None
    stress_psi: Optional[Decimal] = None
    stress_mpa: Optional[Decimal] = None
    stress_bar: Optional[Decimal] = None
    source: Optional[str] = None
    viii1_limit_f: Optional[int] = None   # Section VIII-1 temperature limit of this line, F
    min_tensile_ksi: Optional[Decimal] = None
    min_yield_ksi: Optional[Decimal] = None
    notes_text: Optional[str] = None
    size_thickness: Optional[str] = None
    table: List[StressTablePoint] = []


# ── t_min Calculation Request ─────────────────────────────────────────────────

class TminCalculateRequest(BaseModel):
    """
    All inputs required to calculate t_min via ASME Section VIII Div 1.
    Mirrors client's T min calculation.xlsx row inputs.
    """
    # Component type (determines which formula to use)
    component_type: str          # cylindrical_shell | spherical_shell | ellipsoidal_head |
                                 # hemispherical_head | torispherical_head | conical_head | flat_head

    # Design inputs
    design_pressure: Decimal     # P
    pressure_unit: str           # bar | psi | mpa | kg_cm2

    inside_diameter: Decimal     # D (for shells) or d (for heads)
    dimension_unit: str          # mm | inches

    design_temperature: Decimal  # T
    temperature_unit: str        # celsius | fahrenheit

    # Joint Efficiency — from client dropdown: 1.00 | 0.85 | 0.70 | 0.65 | 0.60
    joint_efficiency: Decimal    # E

    # Material (used for S lookup from DB)
    spec_number: str             # e.g. SA-516
    grade: str                   # e.g. Gr. 70
    product_form: str            # e.g. Plate

    # Optional: user can override S manually (client's "Custom Value" option)
    custom_stress_mpa: Optional[Decimal] = None   # S override in MPa
    custom_stress_psi: Optional[Decimal] = None   # S override in psi
    # Generic override: any unit (ksi | psi | mpa | bar). Takes priority over the two above.
    custom_stress: Optional[Decimal] = None
    custom_stress_unit: Optional[str] = None
    # Required whenever a user-entered stress is used (e.g. "Client datasheet", "Code Case 2345")
    custom_stress_source: Optional[str] = None

    # Corrosion allowance: only used for the design thickness (t + CA) and the MAWP net thickness
    corrosion_allowance_mm: Optional[Decimal] = None   # CA in mm (legacy)
    corrosion_allowance: Optional[Decimal] = None      # CA in corrosion_allowance_unit (mm | inches)
    corrosion_allowance_unit: Optional[str] = "mm"

    # Component-specific inputs (all optional; sensible defaults are used and reported)
    crown_radius: Optional[Decimal] = None        # torispherical L, in dimension_unit (default L = D)
    knuckle_radius: Optional[Decimal] = None      # torispherical r, in dimension_unit (default r = 0.06 D)
    flat_head_c: Optional[Decimal] = None         # flat head attachment factor C (default 0.33)
    cone_half_angle_deg: Optional[Decimal] = None # conical head half apex angle, degrees (<= 30)

    # MAWP is calculated from the GIVEN (nominal) thickness: net = given - CA
    given_thickness: Optional[Decimal] = None
    given_thickness_unit: Optional[str] = "mm"


# ── t_min Calculation Response ────────────────────────────────────────────────

class TminCalculateResponse(BaseModel):
    """
    Full calculation breakdown — all intermediate values shown for transparency.
    """
    # What formula was applied
    component_type: str
    asme_clause: str             # e.g. "ASME Section VIII Div 1 — UG-27(c)(1)"

    # Inputs normalized to mm and bar
    pressure_bar: Decimal
    radius_mm: Decimal
    diameter_mm: Decimal
    temperature_c: Decimal
    joint_efficiency: Decimal

    # Allowable stress used
    allowable_stress_mpa: Decimal
    allowable_stress_bar: Decimal
    stress_source: str           # "ASME Table 1A — ASME 2023" or "User Override"

    # Formula intermediate check
    thick_wall_flag: bool        # True if P > 0.385 * S * E (cylinder: Appendix 1-2(a)) or P > 0.665 * S * E (sphere: warning only)
    thick_wall_warning: Optional[str]

    # Results
    t_min_mm: Decimal            # Final t_min in mm (to populate Equipment Profile)
    t_min_inches: Decimal        # t_min in inches
    mawp_bar: Optional[Decimal]  # MAWP if CA provided

    # Full formula string (shown to user)
    formula_used: str            # e.g. "t = (P × R) / (S × E − 0.6 × P)"
    mawp_formula: Optional[str] = None   # e.g. "P = (S × E × t) / (R + 0.6 × t), t = given thickness − CA" (only with MAWP)
    calculation_trace: str       # Step-by-step human readable trace

    # Corrosion allowance / design thickness (t_min itself never includes CA)
    corrosion_allowance_mm: Optional[Decimal] = None
    design_thickness_mm: Optional[Decimal] = None      # t_min + CA
    design_thickness_inches: Optional[Decimal] = None

    # Factors actually used
    crown_radius_mm: Optional[Decimal] = None
    knuckle_radius_mm: Optional[Decimal] = None
    m_factor: Optional[Decimal] = None
    c_factor: Optional[Decimal] = None
    c_is_default: Optional[bool] = None
    cone_half_angle_deg: Optional[Decimal] = None
    warnings: List[str] = []
