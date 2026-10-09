from pydantic import BaseModel, field_validator
from app.core import unit_policy
from typing import Optional
from datetime import datetime
from decimal import Decimal
import uuid


class EquipmentBase(BaseModel):
    # Identification
    equipment_name: str
    equipment_type: str
    equipment_category: Optional[str] = None
    asset_tag: Optional[str] = None
    orientation: Optional[str] = None

    # Location
    plant: str = "Plant 01"
    unit: str = "Unit 01"
    area: Optional[str] = None
    location: Optional[str] = None

    description: Optional[str] = None
    status: str = "ACTIVE"
    criticality: Optional[str] = None

    # Technical & Shell
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    material: Optional[str] = None
    material_grade: Optional[str] = None
    construction_type: Optional[str] = None
    design_code: Optional[str] = None
    equipment_class: Optional[str] = None
    specification: Optional[str] = None

    # Shell Conditions
    design_pressure: Optional[Decimal] = None
    operating_pressure: Optional[Decimal] = None
    pressure_unit: Optional[str] = "bar"
    design_temperature: Optional[Decimal] = None
    operating_temperature: Optional[Decimal] = None
    temperature_unit: Optional[str] = "°C"
    service: Optional[str] = None
    fluid: Optional[str] = None
    flow_rate: Optional[Decimal] = None
    flow_rate_unit: Optional[str] = None

    # Shell Physical
    nominal_thickness: Optional[Decimal] = None
    min_required_thickness: Optional[Decimal] = None
    thickness_unit: Optional[str] = "mm"
    diameter: Optional[Decimal] = None
    diameter_unit: Optional[str] = "mm"
    length: Optional[Decimal] = None
    length_unit: Optional[str] = "mm"
    weight: Optional[Decimal] = None
    weight_unit: Optional[str] = "kg"
    capacity: Optional[Decimal] = None
    capacity_unit: Optional[str] = "m³"
    manway_size: Optional[Decimal] = None

    # Dish / Head Specs
    dish_type: Optional[str] = None
    dish_material: Optional[str] = None
    dish_grade: Optional[str] = None
    dish_nominal_thickness: Optional[Decimal] = None
    dish_min_thickness: Optional[Decimal] = None

    # Tank Specs
    bottom_material: Optional[str] = None
    bottom_nominal_thickness: Optional[Decimal] = None
    bottom_min_thickness: Optional[Decimal] = None
    roof_material: Optional[str] = None
    roof_nominal_thickness: Optional[Decimal] = None
    roof_min_thickness: Optional[Decimal] = None
    floating_roof_thickness: Optional[Decimal] = None
    floating_roof_min_thickness: Optional[Decimal] = None
    bottom_critical_zone_min_thickness: Optional[Decimal] = None
    annular_min_thickness: Optional[Decimal] = None
    # Inputs of the tank t_min calculator
    tank_specific_gravity: Optional[Decimal] = None
    tank_height: Optional[Decimal] = None                 # mm
    tank_joint_efficiency: Optional[Decimal] = None
    tank_bottom_leak_protection: Optional[bool] = None

    # Tube Bundle Specs
    tube_operating_pressure: Optional[Decimal] = None
    tube_operating_temperature: Optional[Decimal] = None
    tube_design_pressure: Optional[Decimal] = None
    tube_design_temperature: Optional[Decimal] = None
    tube_service: Optional[str] = None
    tube_material: Optional[str] = None
    no_of_tubes: Optional[int] = None
    tube_od: Optional[Decimal] = None
    tube_length: Optional[Decimal] = None
    tube_thickness: Optional[Decimal] = None
    tube_min_thickness: Optional[Decimal] = None

    # Insulation
    has_insulation: Optional[str] = None
    insulation_thickness: Optional[Decimal] = None

    # Attached PDF Document
    document_url: Optional[str] = None
    document_name: Optional[str] = None

    # Inspection & Corrosion Data (Rev01 Data)
    inspection_date_initial: Optional[datetime] = None
    inspection_date_current: Optional[datetime] = None

    s1_n: Optional[Decimal] = None
    s1_s: Optional[Decimal] = None
    s1_e: Optional[Decimal] = None
    s1_w: Optional[Decimal] = None

    s2_n: Optional[Decimal] = None
    s2_s: Optional[Decimal] = None
    s2_e: Optional[Decimal] = None
    s2_w: Optional[Decimal] = None

    d1_n: Optional[Decimal] = None
    d1_s: Optional[Decimal] = None
    d1_e: Optional[Decimal] = None
    d1_w: Optional[Decimal] = None

    d2_n: Optional[Decimal] = None
    d2_s: Optional[Decimal] = None
    d2_e: Optional[Decimal] = None
    d2_w: Optional[Decimal] = None

    corrosion_rate: Optional[Decimal] = None
    remaining_life: Optional[Decimal] = None

    # Lifecycle
    manufacturing_year: Optional[int] = None
    purchase_date: Optional[datetime] = None
    installation_date: Optional[datetime] = None
    commissioning_date: Optional[datetime] = None
    warranty_expiry: Optional[datetime] = None
    expected_service_life: Optional[int] = None

    # Responsibility
    department: Optional[str] = None
    responsible_department: Optional[str] = None
    asset_owner: Optional[str] = None
    responsible_team: Optional[str] = None

    # Notes
    notes: Optional[str] = None
    technical_remarks: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"ACTIVE", "INACTIVE", "UNDER_MAINTENANCE", "DECOMMISSIONED", "DELETED"}
        if v.upper() not in allowed:
            raise ValueError(f"Status must be one of: {allowed}")
        return v.upper()

    @field_validator("criticality")
    @classmethod
    def validate_criticality(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"Criticality must be one of: {allowed}")
        return v.upper()


    # One unit system everywhere: mm, bar, °C (see app/core/unit_policy.py)
    @field_validator("thickness_unit", "diameter_unit", "length_unit")
    @classmethod
    def _length_in_mm(cls, v, info):
        return unit_policy.length_unit(v, info.field_name.replace("_", " ").capitalize())

    @field_validator("pressure_unit")
    @classmethod
    def _pressure_in_bar(cls, v):
        return unit_policy.pressure_unit(v, "Pressure unit")

    @field_validator("temperature_unit")
    @classmethod
    def _temperature_in_celsius(cls, v):
        return unit_policy.temperature_unit(v, "Temperature unit")

    model_config = {"from_attributes": True}


class EquipmentCreate(EquipmentBase):
    equipment_id: str
    created_by: Optional[str] = "System"


class EquipmentUpdate(EquipmentBase):
    equipment_name: Optional[str] = None
    equipment_type: Optional[str] = None
    plant: Optional[str] = None
    unit: Optional[str] = None
    status: Optional[str] = None
    updated_by: Optional[str] = "System"


class EquipmentResponse(EquipmentBase):
    id: uuid.UUID
    equipment_id: str
    # Calculated by the inspection engine (read-only, never accepted on create / update)
    shell_controlling_life: Optional[Decimal] = None
    head_controlling_life: Optional[Decimal] = None
    tube_controlling_life: Optional[Decimal] = None
    roof_controlling_life: Optional[Decimal] = None
    bottom_controlling_life: Optional[Decimal] = None
    next_internal_inspection_date: Optional[datetime] = None
    next_external_inspection_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class InspectionDueItem(BaseModel):
    """One asset in the dashboard's 'inspections due' list."""
    id: uuid.UUID
    equipment_id: str
    equipment_name: str
    remaining_life: Optional[Decimal] = None
    next_internal_inspection_date: Optional[datetime] = None
    next_external_inspection_date: Optional[datetime] = None

    model_config = {"from_attributes": True}


class EquipmentListResponse(BaseModel):
    data: list[EquipmentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class EquipmentStats(BaseModel):
    total: int
    active: int
    inactive: int
    critical: int
    under_maintenance: int
    decommissioned: int
