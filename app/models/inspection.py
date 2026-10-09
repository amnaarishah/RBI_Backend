import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, Integer, DateTime, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class InspectionRecord(Base):
    __tablename__ = "inspection_records"

    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Linked Equipment
    equipment_id = Column(UUID(as_uuid=True), ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True)
    equipment_tag = Column(String(50), nullable=False, index=True)

    # Inspection Metadata
    inspection_date = Column(DateTime(timezone=True), nullable=False, default=func.now(), index=True)
    inspector_name = Column(String(200), default="Lead Inspector")
    inspection_type = Column(String(100), default="Ultrasonic Thickness Measurement (UTM)")
    thickness_unit = Column(String(20), default="mm")

    # Shell Course 1 Thickness Readings (N, S, E, W)
    s1_n = Column(Numeric(12, 4))
    s1_s = Column(Numeric(12, 4))
    s1_e = Column(Numeric(12, 4))
    s1_w = Column(Numeric(12, 4))

    # Shell Course 2 Thickness Readings (N, S, E, W)
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

    # Calculated API 510 Inspection Metrics
    min_measured_thickness = Column(Numeric(12, 4))
    corrosion_rate = Column(Numeric(12, 4))  # mm/year
    remaining_life = Column(Numeric(12, 4))  # years
    next_inspection_due = Column(DateTime(timezone=True))

    # Component-Wise Remaining Life (Heat Exchanger, Vessel, Tank)
    shell_remaining_life  = Column(Numeric(12, 4))  # years
    head_remaining_life   = Column(Numeric(12, 4))  # years
    tube_remaining_life   = Column(Numeric(12, 4))  # years
    roof_remaining_life   = Column(Numeric(12, 4))  # years (Tanks)
    bottom_remaining_life = Column(Numeric(12, 4))  # years (Tanks)

    # API 510 Dual Inspection Due Dates
    next_internal_due = Column(DateTime(timezone=True))
    next_external_due = Column(DateTime(timezone=True))

    remarks = Column(Text)

    # Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(200), default="System")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(200), default="System")
