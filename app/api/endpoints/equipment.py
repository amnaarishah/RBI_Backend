import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional
from app.core.database import get_db
from app.api.deps import Principal, authorize
from app.schemas.equipment import (
    EquipmentCreate, EquipmentUpdate,
    EquipmentResponse, EquipmentListResponse, EquipmentStats, InspectionDueItem
)
from app.crud import equipment as crud
from app.core.uploads import read_upload, safe_filename, DOCUMENT_TYPES, MAX_DOCUMENT_BYTES

router = APIRouter(prefix="/equipment", tags=["Equipment"])

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "uploads", "documents")
os.makedirs(UPLOADS_DIR, exist_ok=True)


@router.post("/upload-document")
async def upload_document(file: UploadFile = File(...)):
    # size limit + real file type check (not just the extension); the stored name never uses the raw upload name
    content, _ext = await read_upload(file, DOCUMENT_TYPES, MAX_DOCUMENT_BYTES)

    unique_filename = f"doc_{uuid.uuid4().hex[:8]}_{safe_filename(file.filename)}"
    file_path = os.path.join(UPLOADS_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(content)

    document_url = f"/uploads/documents/{unique_filename}"
    return {
        "document_name": file.filename,
        "document_url": document_url,
    }


@router.get("/stats", response_model=EquipmentStats)
def get_stats(db: Session = Depends(get_db)):
    return crud.get_stats(db)


@router.get("/inspections-due", response_model=list[InspectionDueItem])
def inspections_due(limit: int = Query(5, ge=1, le=50), db: Session = Depends(get_db)):
    """Assets ordered by their next (internal / external) inspection date, earliest first."""
    return crud.get_inspections_due(db, limit)


@router.get("/check-id/{equipment_id}")
def check_equipment_id(equipment_id: str, db: Session = Depends(get_db)):
    existing = crud.get_equipment_by_equipment_id(db, equipment_id)
    return {"exists": existing is not None}


@router.get("", response_model=EquipmentListResponse)
def list_equipment(
    search: Optional[str] = Query(None),
    equipment_type: Optional[str] = Query(None),
    plant: Optional[str] = Query(None),
    unit: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    criticality: Optional[str] = Query(None),
    sort_by: str = Query("equipment_id"),
    sort_order: str = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, total, total_pages = crud.get_equipment_list(
        db=db,
        search=search,
        equipment_type=equipment_type,
        plant=plant,
        unit=unit,
        status=status,
        criticality=criticality,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return EquipmentListResponse(
        data=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{uuid}", response_model=EquipmentResponse)
def get_equipment(uuid: str, db: Session = Depends(get_db)):
    equipment = crud.get_equipment_by_uuid(db, uuid)
    if not equipment:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return equipment


@router.post("", response_model=EquipmentResponse, status_code=201)
def create_equipment(data: EquipmentCreate, db: Session = Depends(get_db),
                     principal: Principal = Depends(authorize)):
    existing = crud.get_equipment_by_equipment_id(db, data.equipment_id)
    if existing:
        raise HTTPException(status_code=400, detail="Equipment ID already exists")
    data.created_by = principal.username          # the signed-in user, never what the client sends
    return crud.create_equipment(db, data)


@router.put("/{uuid}", response_model=EquipmentResponse)
def update_equipment(uuid: str, data: EquipmentUpdate, db: Session = Depends(get_db),
                     principal: Principal = Depends(authorize)):
    data.updated_by = principal.username
    equipment = crud.update_equipment(db, uuid, data)
    if not equipment:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return equipment


@router.delete("/{uuid}", response_model=EquipmentResponse)
def delete_equipment(uuid: str, db: Session = Depends(get_db), principal: Principal = Depends(authorize)):
    equipment = crud.soft_delete_equipment(db, uuid, deleted_by=principal.username)
    if not equipment:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return equipment
