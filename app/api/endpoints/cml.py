from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import uuid

from app.core.database import get_db
from app.schemas.cml import (
    InspectionTMLCreate,
    InspectionTMLResponse,
    InspectionReadingCreate,
    InspectionReadingResponse
)
from app.crud import cml as crud_cml

router = APIRouter()

@router.get("/equipment/{equipment_id}", response_model=List[InspectionTMLResponse])
def get_tmls(equipment_id: uuid.UUID, db: Session = Depends(get_db)):
    return crud_cml.get_tmls_by_equipment(db=db, equipment_id=equipment_id)


@router.post("/tml", response_model=InspectionTMLResponse, status_code=status.HTTP_201_CREATED)
def create_tml(data: InspectionTMLCreate, db: Session = Depends(get_db)):
    return crud_cml.create_tml(db=db, data=data)


@router.post("/tml/{tml_id}/reading", response_model=InspectionReadingResponse, status_code=status.HTTP_201_CREATED)
def add_reading(tml_id: uuid.UUID, data: InspectionReadingCreate, db: Session = Depends(get_db)):
    try:
        return crud_cml.add_reading_to_tml(db=db, tml_id=tml_id, data=data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
