"""
Tank t_min calculator API (client's `tank calculations.xlsx`).
Completely isolated — does not touch Equipment, CML, Inspection or ASME vessel routes.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.crud.tank_calculator import calculate_tank_tmin, list_tank_materials
from app.schemas.tank import TankCalculateRequest, TankCalculateResponse, TankMaterialResponse

router = APIRouter(prefix="/tank", tags=["Tank t_min Calculator"])


@router.get("/materials", response_model=List[TankMaterialResponse])
def tank_materials(db: Session = Depends(get_db)):
    """The material list of the sheet (dropdown of the tank calculator)."""
    return list_tank_materials(db)


@router.post("/calculate", response_model=TankCalculateResponse)
def tank_calculate(req: TankCalculateRequest, db: Session = Depends(get_db)):
    """The five t-min values of the sheet. Stores nothing."""
    try:
        return calculate_tank_tmin(db, req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
