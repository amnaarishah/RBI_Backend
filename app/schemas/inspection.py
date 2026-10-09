from pydantic import BaseModel, field_validator
from app.core import unit_policy
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
import uuid


class DynamicTMLReadingInput(BaseModel):
    tml_id: Optional[uuid.UUID] = None
    tml_code: str
    tml_name: Optional[str] = None
    tml_type: Optional[str] = "Shell"
    direction_labels: Optional[str] = "N,S,E,W"
    reading_1: Optional[Decimal] = None
    reading_2: Optional[Decimal] = None
    reading_3: Optional[Decimal] = None
    reading_4: Optional[Decimal] = None
    reading_5: Optional[Decimal] = None
    reading_6: Optional[Decimal] = None
    min_required_thickness: Optional[Decimal] = None


class InspectionRecordBase(BaseModel):
    equipment_tag: str
    inspection_date: datetime
    inspector_name: Optional[str] = "Lead Inspector"
    inspection_type: Optional[str] = "Ultrasonic Thickness Measurement (UTM)"
    thickness_unit: Optional[str] = "mm"

    @field_validator("thickness_unit")
    @classmethod
    def _readings_in_mm(cls, v):
        return unit_policy.length_unit(v, "Thickness readings")

    # Shell 1 Readings
    s1_n: Optional[Decimal] = None
    s1_s: Optional[Decimal] = None
    s1_e: Optional[Decimal] = None
    s1_w: Optional[Decimal] = None

    # Shell 2 Readings
    s2_n: Optional[Decimal] = None
    s2_s: Optional[Decimal] = None
    s2_e: Optional[Decimal] = None
    s2_w: Optional[Decimal] = None

    # Dish 1 Readings
    d1_n: Optional[Decimal] = None
    d1_s: Optional[Decimal] = None
    d1_e: Optional[Decimal] = None
    d1_w: Optional[Decimal] = None

    # Dish 2 Readings
    d2_n: Optional[Decimal] = None
    d2_s: Optional[Decimal] = None
    d2_e: Optional[Decimal] = None
    d2_w: Optional[Decimal] = None

    # Dynamic TML readings list for custom zones
    tml_readings: Optional[List[DynamicTMLReadingInput]] = None

    remarks: Optional[str] = None

    model_config = {"from_attributes": True}


class InspectionRecordCreate(InspectionRecordBase):
    equipment_id: uuid.UUID
    created_by: Optional[str] = "System"


class InspectionRecordResponse(InspectionRecordBase):
    id: uuid.UUID
    equipment_id: uuid.UUID
    min_measured_thickness: Optional[Decimal] = None
    corrosion_rate: Optional[Decimal] = None
    remaining_life: Optional[Decimal] = None
    next_inspection_due: Optional[datetime] = None

    # Component-wise remaining life
    shell_remaining_life: Optional[Decimal] = None
    head_remaining_life: Optional[Decimal] = None
    tube_remaining_life: Optional[Decimal] = None
    roof_remaining_life: Optional[Decimal] = None
    bottom_remaining_life: Optional[Decimal] = None

    # API 510 dual inspection due dates
    next_internal_due: Optional[datetime] = None
    next_external_due: Optional[datetime] = None

    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class InspectionRecordListResponse(BaseModel):
    data: list[InspectionRecordResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class InspectionStats(BaseModel):
    total_inspections: int
    critical_life_count: int
    warning_life_count: int
    healthy_life_count: int
