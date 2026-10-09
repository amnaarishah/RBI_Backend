from pydantic import BaseModel, ConfigDict, field_validator
from app.core import unit_policy
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
import uuid


# --- Inspection Reading Schemas ---

class InspectionReadingBase(BaseModel):
    inspection_date: datetime
    inspector_name: Optional[str] = "Lead Inspector"
    inspection_type: Optional[str] = "Ultrasonic Thickness Measurement (UTM)"
    unit: Optional[str] = "mm"
    notes: Optional[str] = None

    @field_validator("unit")
    @classmethod
    def _readings_in_mm(cls, v):
        return unit_policy.length_unit(v, "Thickness readings")

    reading_1: Optional[Decimal] = None
    reading_2: Optional[Decimal] = None
    reading_3: Optional[Decimal] = None
    reading_4: Optional[Decimal] = None
    reading_5: Optional[Decimal] = None
    reading_6: Optional[Decimal] = None


class InspectionReadingCreate(InspectionReadingBase):
    pass


class InspectionReadingResponse(InspectionReadingBase):
    id: uuid.UUID
    tml_id: uuid.UUID
    min_reading: Optional[Decimal] = None
    thickness_lost_csv: Optional[str] = None
    corrosion_rate_csv: Optional[str] = None
    remaining_life_csv: Optional[str] = None
    short_term_cr_csv: Optional[str] = None
    long_term_cr_csv: Optional[str] = None
    cr_type_csv: Optional[str] = None
    controlling_cr: Optional[Decimal] = None
    controlling_rl: Optional[Decimal] = None
    controlling_point_index: Optional[int] = None
    status: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Inspection TML Schemas ---

class InspectionTMLBase(BaseModel):
    tml_code: str
    tml_name: Optional[str] = None
    tml_type: Optional[str] = None
    direction_labels: Optional[str] = "N,S,E,W"
    nominal_thickness: Optional[Decimal] = None
    min_required_thickness: Optional[Decimal] = None
    sort_order: Optional[int] = 0


class InspectionTMLCreate(InspectionTMLBase):
    equipment_id: uuid.UUID
    equipment_tag: str


class InspectionTMLResponse(InspectionTMLBase):
    id: uuid.UUID
    equipment_id: uuid.UUID
    equipment_tag: str
    readings: List[InspectionReadingResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
