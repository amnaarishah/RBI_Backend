"""Tank t_min calculator — request / response (client's `tank calculations.xlsx`)."""
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TankMaterialResponse(BaseModel):
    id: UUID
    name: str                       # e.g. "A285 Grade C"
    spec: str
    grade: Optional[str] = None
    is_unknown: bool = False
    yield_mpa: Optional[Decimal] = None
    tensile_mpa: Optional[Decimal] = None
    s_lower_mpa: Optional[Decimal] = None   # allowable stress S used by the sheet (lower two courses)

    model_config = {"from_attributes": True}


class TankCalculateRequest(BaseModel):
    """Inputs of the sheet: SG (C), diameter (E), height (F), joint efficiency (H), material (I), bottom leak protection (M).
    Diameter and height are in mm (the one unit system of the software); the sheet's formula uses metres."""
    material: str                                   # name from the material list, e.g. "A285 Grade C"
    specific_gravity: Decimal = Field(gt=0)         # G
    diameter_mm: Decimal = Field(gt=0)              # D
    height_mm: Decimal = Field(gt=0)                # H
    joint_efficiency: Decimal = Field(default=Decimal("1"), gt=0, le=1)   # E
    bottom_leak_protection: bool                    # M: yes -> 1.27 mm, no -> 2.54 mm


class TankCalculateResponse(BaseModel):
    material: str
    is_unknown_material: bool
    allowable_stress_mpa: Decimal                   # S (lower two courses), from the material table
    diameter_m: Decimal
    height_m: Decimal

    t_shell_bottom_course_mm: Decimal               # N  t-min (Bottom Shell)
    t_bottom_mm: Decimal                            # O  t-min Bottom
    t_bottom_critical_zone_mm: Decimal              # P  t-min Bottom at critical zone
    t_annular_mm: Optional[Decimal] = None          # Q  t-min annular plates (None = "Out of Range")
    t_roof_mm: Decimal                              # R  t-min roof

    formula_shell: str
    formula_bottom: str
    formula_critical_zone: str
    formula_annular: str
    formula_roof: str
    warnings: List[str] = []
    calculation_trace: str
