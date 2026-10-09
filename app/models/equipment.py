import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, String, Numeric, Integer, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class Equipment(Base):
    __tablename__ = "equipment"

    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Asset Identification
    equipment_id = Column(String(50), unique=True, nullable=False, index=True)
    equipment_name = Column(String(200), nullable=False)
    equipment_type = Column(String(100), nullable=False)
    equipment_category = Column(String(100))
    asset_tag = Column(String(100))
    orientation = Column(String(50))  # Horizontal / Vertical

    # Location
    plant = Column(String(100), nullable=False, index=True, default="Plant 01")
    unit = Column(String(100), nullable=False, default="Unit 01")
    area = Column(String(100))
    location = Column(String(200))

    # Description & Status
    description = Column(Text)
    status = Column(String(50), nullable=False, default="ACTIVE", index=True)
    criticality = Column(String(50), index=True)

    # Technical Info & Shell Data
    manufacturer = Column(String(200))
    model = Column(String(200))
    serial_number = Column(String(200))
    material = Column(String(100))
    material_grade = Column(String(100))
    construction_type = Column(String(100))
    design_code = Column(String(200))
    equipment_class = Column(String(100))
    specification = Column(Text)

    # Shell Design & Operating Conditions
    design_pressure = Column(Numeric(12, 4))
    operating_pressure = Column(Numeric(12, 4))
    pressure_unit = Column(String(20), default="bar")

    design_temperature = Column(Numeric(12, 4))
    operating_temperature = Column(Numeric(12, 4))
    temperature_unit = Column(String(20), default="°C")

    service = Column(String(200))
    fluid = Column(String(200))
    flow_rate = Column(Numeric(12, 4))
    flow_rate_unit = Column(String(20))

    # Shell Physical & Dimensional Information
    nominal_thickness = Column(Numeric(12, 4))
    min_required_thickness = Column(Numeric(12, 4))
    thickness_unit = Column(String(20), default="mm")

    diameter = Column(Numeric(12, 4))
    diameter_unit = Column(String(20), default="mm")

    length = Column(Numeric(12, 4))
    length_unit = Column(String(20), default="mm")

    weight = Column(Numeric(12, 4))
    weight_unit = Column(String(20), default="kg")

    capacity = Column(Numeric(12, 4))
    capacity_unit = Column(String(20), default="m³")

    manway_size = Column(Numeric(12, 4))  # inch

    # Dish / Head Specs (Vessels)
    dish_type = Column(String(100))
    dish_material = Column(String(100))
    dish_grade = Column(String(100))
    dish_nominal_thickness = Column(Numeric(12, 4))
    dish_min_thickness = Column(Numeric(12, 4))

    # Tank Specs (Tanks)
    bottom_material = Column(String(100))
    bottom_nominal_thickness = Column(Numeric(12, 4))
    bottom_min_thickness = Column(Numeric(12, 4))

    roof_material = Column(String(100))
    roof_nominal_thickness = Column(Numeric(12, 4))
    roof_min_thickness = Column(Numeric(12, 4))

    floating_roof_thickness = Column(Numeric(12, 4))
    floating_roof_min_thickness = Column(Numeric(12, 4))
    # Results of the tank t_min calculator (client's tank calculations.xlsx)
    bottom_critical_zone_min_thickness = Column(Numeric(12, 4))
    annular_min_thickness = Column(Numeric(12, 4))
    # Inputs of the tank t_min calculator, kept so the calculator window opens with them again
    tank_specific_gravity = Column(Numeric(12, 4))
    tank_height = Column(Numeric(12, 4))                 # mm
    tank_joint_efficiency = Column(Numeric(12, 4))
    tank_bottom_leak_protection = Column(Boolean)

    # Tube Bundle Specs (Heat Exchangers)
    tube_operating_pressure = Column(Numeric(12, 4))
    tube_operating_temperature = Column(Numeric(12, 4))
    tube_design_pressure = Column(Numeric(12, 4))
    tube_design_temperature = Column(Numeric(12, 4))
    tube_service = Column(String(200))
    tube_material = Column(String(100))
    no_of_tubes = Column(Integer)
    tube_od = Column(Numeric(12, 4))
    tube_length = Column(Numeric(12, 4))
    tube_thickness = Column(Numeric(12, 4))
    tube_min_thickness = Column(Numeric(12, 4))

    # Insulation
    has_insulation = Column(String(50))  # Yes / No / NA
    insulation_thickness = Column(Numeric(12, 4))  # mm

    # Attached PDF Document
    document_url = Column(String(500))
    document_name = Column(String(300))

    # Inspection & Corrosion Data (Rev01 Data & API 510 Calculations)
    inspection_date_initial = Column(DateTime(timezone=True))
    inspection_date_current = Column(DateTime(timezone=True))

    # Shell 1 Thickness Readings (N, S, E, W)
    s1_n = Column(Numeric(12, 4))
    s1_s = Column(Numeric(12, 4))
    s1_e = Column(Numeric(12, 4))
    s1_w = Column(Numeric(12, 4))

    # Shell 2 Thickness Readings (N, S, E, W)
    s2_n = Column(Numeric(12, 4))
    s2_s = Column(Numeric(12, 4))
    s2_e = Column(Numeric(12, 4))
    s2_w = Column(Numeric(12, 4))

    # Dish 1 Thickness Readings (N, S, E, W)
    d1_n = Column(Numeric(12, 4))
    d1_s = Column(Numeric(12, 4))
    d1_e = Column(Numeric(12, 4))
    d1_w = Column(Numeric(12, 4))

    # Dish 2 Thickness Readings (N, S, E, W)
    d2_n = Column(Numeric(12, 4))
    d2_s = Column(Numeric(12, 4))
    d2_e = Column(Numeric(12, 4))
    d2_w = Column(Numeric(12, 4))

    # Calculated API 510 Metrics
    corrosion_rate = Column(Numeric(12, 4))  # mm/year
    remaining_life = Column(Numeric(12, 4))  # years

    # Component-Wise Remaining Life (Heat Exchanger, Vessel, Tank)
    shell_controlling_life  = Column(Numeric(12, 4))   # years — worst of all Shell TMLs
    head_controlling_life   = Column(Numeric(12, 4))   # years — worst of all Head/Dish TMLs
    tube_controlling_life   = Column(Numeric(12, 4))   # years — worst of all Tube TMLs
    roof_controlling_life   = Column(Numeric(12, 4))   # years — worst of all Roof TMLs (Tanks)
    bottom_controlling_life = Column(Numeric(12, 4))   # years — worst of all Bottom TMLs (Tanks)

    # API 510 Next Inspection Due Dates
    next_internal_inspection_date = Column(DateTime(timezone=True))
    next_external_inspection_date = Column(DateTime(timezone=True))

    # Lifecycle
    manufacturing_year = Column(Integer)
    purchase_date = Column(DateTime(timezone=True))
    installation_date = Column(DateTime(timezone=True))
    commissioning_date = Column(DateTime(timezone=True))
    warranty_expiry = Column(DateTime(timezone=True))
    expected_service_life = Column(Integer)  # years

    # Responsibility
    department = Column(String(200))
    responsible_department = Column(String(200))
    asset_owner = Column(String(200))
    responsible_team = Column(String(200))

    # Notes
    notes = Column(Text)
    technical_remarks = Column(Text)

    # Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(200), default="System")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(200), default="System")
