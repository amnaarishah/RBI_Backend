"""
ASME t_min Calculator API Endpoints.
Completely isolated — does not touch Equipment, CML, or Inspection routes.
"""
from typing import List
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.asme import (
    TminCalculateRequest, TminCalculateResponse,
    AsmeMaterialListResponse, AsmeMaterialStressResponse, StressLookupResponse,
)
from app.crud.asme_calculator import calculate_tmin, get_materials_list, get_stress_lookup, suggest_materials
from app.models.asme import AsmeMaterialStress

router = APIRouter(prefix="/asme", tags=["ASME t_min Calculator"])


@router.get("/materials", response_model=List[AsmeMaterialListResponse])
def list_materials(db: Session = Depends(get_db)):
    """
    Returns distinct list of materials available in ASME Table 1A DB.
    Used to populate the searchable Material dropdown in the UI.
    """
    return get_materials_list(db)


@router.get("/materials/suggest", response_model=List[AsmeMaterialListResponse])
def suggest_similar_materials(spec: str = Query(...), grade: str = Query(""), db: Session = Depends(get_db)):
    """Closest materials to a spec / grade that is not in the tables ("Did you mean ...?")."""
    return suggest_materials(db, spec, grade)


@router.get("/stress", response_model=StressLookupResponse)
def lookup_allowable_stress(
    spec_number: str = Query(...),
    grade: str = Query(...),
    product_form: str = Query(...),
    temperature: Decimal = Query(...),
    temperature_unit: str = Query("celsius"),
    db: Session = Depends(get_db),
):
    """
    Allowable stress S for a material at a design temperature (ASME II-D Table 1A / 1B),
    with source (table, page, line), interpolation note and the full stress-vs-temperature
    table. Used by the calculator UI to show S before calculating.
    """
    try:
        return get_stress_lookup(db, spec_number, grade, product_form, temperature, temperature_unit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/materials/{spec_number}/{grade}/stress", response_model=List[AsmeMaterialStressResponse])
def get_material_stress_table(
    spec_number: str,
    grade: str,
    product_form: str = "Plate",
    db: Session = Depends(get_db),
):
    """
    Returns the full stress vs temperature table for a given material.
    Shown in the calculator modal so user can see all temperature values.
    """
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
        raise HTTPException(
            status_code=404,
            detail=f"Material '{spec_number} {grade}' ({product_form}) not found in ASME Table 1A.",
        )
    return rows


@router.post("/calculate-tmin", response_model=TminCalculateResponse)
def calculate_minimum_thickness(
    req: TminCalculateRequest,
    db: Session = Depends(get_db),
):
    """
    Main ASME t_min calculation endpoint.
    Accepts design inputs, looks up S from ASME Table 1A,
    runs the appropriate ASME Section VIII Div 1 formula,
    returns full calculation breakdown with trace.
    """
    try:
        result = calculate_tmin(db, req)
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ZeroDivisionError:
        raise HTTPException(
            status_code=422,
            detail="Calculation error: Division by zero. Please check your input values (e.g. S × E − 0.6 × P must be > 0).",
        )
