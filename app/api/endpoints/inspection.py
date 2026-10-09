from fastapi import APIRouter, Depends, HTTPException, Query, status, File, UploadFile
from sqlalchemy.orm import Session
from typing import Optional
import uuid

from app.core.database import get_db
from app.core.config import settings
from app.api.deps import Principal, authorize
from app.schemas.inspection import (
    InspectionRecordCreate,
    InspectionRecordResponse,
    InspectionRecordListResponse,
    InspectionStats,
)
from app.crud import inspection as crud_inspection
from app.crud.inspection_import import import_inspections_from_excel
from app.core.uploads import read_upload, SPREADSHEET_TYPES, MAX_SPREADSHEET_BYTES

router = APIRouter()


def _server_error(prefix: str, err: Exception) -> HTTPException:
    """500 response. RuntimeError messages are written for the user and are shown; the text of any other
    unexpected error is shown only in development (it can contain internal details)."""
    if isinstance(err, RuntimeError):
        detail = str(err)
    elif settings.APP_ENV == "development":
        detail = f"{prefix}: {err}"
    else:
        detail = f"{prefix}. Please try again or contact support."
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


@router.get("", response_model=InspectionRecordListResponse)
def get_inspection_list(
    equipment_tag: Optional[str] = Query(None, description="Filter by Equipment Tag"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db)
):
    items, total, total_pages = crud_inspection.get_inspection_list(
        db=db,
        equipment_tag=equipment_tag,
        page=page,
        page_size=page_size
    )
    return InspectionRecordListResponse(
        data=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


@router.get("/stats", response_model=InspectionStats)
def get_inspection_stats(db: Session = Depends(get_db)):
    return crud_inspection.get_inspection_stats(db=db)


@router.post("", response_model=InspectionRecordResponse, status_code=status.HTTP_201_CREATED)
def create_inspection_record(
    data: InspectionRecordCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(authorize),
):
    data.created_by = principal.username
    try:
        return crud_inspection.create_inspection_record(db=db, data=data)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    except Exception as err:
        raise _server_error("Failed to save inspection record", err)


@router.get("/{record_id}", response_model=InspectionRecordResponse)
def get_inspection_record(
    record_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    record = crud_inspection.get_inspection_record(db=db, record_id=record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection record not found")
    return record


@router.put("/{record_id}", response_model=InspectionRecordResponse)
def update_inspection_record(
    record_id: uuid.UUID,
    data: InspectionRecordCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(authorize),
):
    data.created_by = principal.username
    try:
        return crud_inspection.update_inspection_record(db=db, record_id=record_id, data=data)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    except Exception as err:
        raise _server_error("Failed to update inspection record", err)


@router.delete("/{record_id}", status_code=status.HTTP_200_OK)
def delete_inspection_record(
    record_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    try:
        crud_inspection.delete_inspection_record(db=db, record_id=record_id)
        return {"status": "success", "message": "Inspection record deleted successfully"}
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    except Exception as err:
        raise _server_error("Failed to delete inspection record", err)


@router.post("/import-excel")
async def import_inspection_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Bulk import inspection thickness records from Excel (.xlsx) or CSV file."""
    contents, _ext = await read_upload(file, SPREADSHEET_TYPES, MAX_SPREADSHEET_BYTES)

    try:
        result = import_inspections_from_excel(
            db=db,
            file_contents=contents,
            filename=file.filename
        )
        return result
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))
    except Exception as err:
        raise _server_error("Failed to import file", err)


@router.get("/sample-template")
def download_sample_template():
    """Generate and return a downloadable sample Excel template for inspection logging."""
    import io
    from openpyxl import Workbook
    from fastapi.responses import StreamingResponse

    wb = Workbook()
    ws = wb.active
    ws.title = "Inspection Data Template"

    headers = [
        "Equipment Tag", "Inspection Date", "Inspector Name", "TML Code", "TML Name",
        "TML Type", "Direction Labels", "Reading 1", "Reading 2", "Reading 3", "Reading 4", "Remarks"
    ]
    ws.append(headers)

    sample_rows = [
        ["00-V-15", "2026-08-08", "John Doe", "S-1", "Shell Course 1", "Shell", "0, 90, 180, 270", 11.97, 11.95, 11.86, 11.17, "Routine UT Measurement"],
        ["00-V-15", "2026-08-08", "John Doe", "S-2", "Shell Course 2", "Shell", "0, 90, 180, 270", 12.06, 11.94, 11.91, 11.89, ""],
        ["00-V-15", "2026-08-08", "John Doe", "D-1", "Upper Dish Head", "Dish", "0, 90, 180, 270", 11.77, 11.77, 11.78, 11.60, ""],
        ["00-V-15", "2026-08-08", "John Doe", "D-2", "Lower Dish Head", "Dish", "0, 90, 180, 270", 11.56, 11.54, 11.41, 11.09, "Controlling location"],
        ["00-V-002", "2026-08-08", "Sarah Smith", "S-1", "Shell Course 1", "Shell", "N, S, E, W", 6.10, 6.08, 6.02, 5.95, "Baseline inspection"],
        ["00-V-002", "2026-08-08", "Sarah Smith", "D-1", "Upper Dish Head", "Dish", "N, S, E, W", 6.05, 6.04, 6.03, 6.02, ""],
    ]

    for r in sample_rows:
        ws.append(r)

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    headers_response = {
        'Content-Disposition': 'attachment; filename="Inspection_Import_Sample_Template.xlsx"'
    }
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_response
    )

