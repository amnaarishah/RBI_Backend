"""
CML / TML Models — Inspection Thickness Measurement Locations
Two tables:
  - inspection_tml  : defines zones per equipment (S-1, S-2, D-1, D-2, etc.)
  - inspection_reading : stores thickness readings per zone per inspection date
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, Integer, DateTime, Text, ForeignKey, Boolean, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


class InspectionTML(Base):
    """Thickness Measurement Location (TML) / Corrosion Monitoring Location (CML)
    One row per zone per equipment (e.g., Shell-1, Dish Head Upper)."""
    __tablename__ = "inspection_tml"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Parent Equipment
    equipment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("equipment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    equipment_tag = Column(String(50), nullable=False, index=True)

    # TML Identity
    tml_code = Column(String(50), nullable=False)   # e.g. "S-1", "S-2", "D-1", "D-2"
    tml_name = Column(String(150))                   # e.g. "Shell Course 1", "Upper Dish Head"
    tml_type = Column(String(50))                    # Shell | Dish | Bottom | Roof | Nozzle | Other

    # Direction/angle labels stored as comma-separated string
    # e.g. "N,S,E,W"  or  "0°,90°,180°,270°"
    direction_labels = Column(String(100), default="N,S,E,W")

    # Reference Thicknesses (inherited from equipment)
    nominal_thickness = Column(Numeric(12, 4))        # mm
    min_required_thickness = Column(Numeric(12, 4))   # mm (t_min / Tmr)

    # Display order
    sort_order = Column(Integer, default=0)

    # Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(200), default="System")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(200), default="System")

    # Relationship to readings
    readings = relationship(
        "InspectionReading",
        back_populates="tml",
        order_by="InspectionReading.inspection_date",
        cascade="all, delete-orphan",
    )


class InspectionReading(Base):
    """One row = one inspection event for one TML.
    Supports up to 6 reading points (for complex geometries).
    Calculations (CR, RL) are stored after being computed on save."""
    __tablename__ = "inspection_reading"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Parent TML
    tml_id = Column(
        UUID(as_uuid=True),
        ForeignKey("inspection_tml.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Inspection Metadata
    inspection_date = Column(DateTime(timezone=True), nullable=False, index=True)
    inspector_name = Column(String(200))
    inspection_type = Column(String(100), default="Ultrasonic Thickness Measurement (UTM)")
    unit = Column(String(10), default="mm")
    notes = Column(Text)

    # Thickness readings — up to 6 directional points per TML
    reading_1 = Column(Numeric(12, 4))   # N  / 0°
    reading_2 = Column(Numeric(12, 4))   # S  / 90°
    reading_3 = Column(Numeric(12, 4))   # E  / 180°
    reading_4 = Column(Numeric(12, 4))   # W  / 270°
    reading_5 = Column(Numeric(12, 4))   # Optional 5th point
    reading_6 = Column(Numeric(12, 4))   # Optional 6th point

    # Derived: minimum reading of this inspection event
    min_reading = Column(Numeric(12, 4))

    # Calculated API 510 per-point values (stored as comma-separated strings for simplicity)
    # Format: "val1,val2,val3,val4" matching direction_labels order
    thickness_lost_csv = Column(String(200))   # Ti - Tf per point
    corrosion_rate_csv = Column(String(200))   # Controlling CR per point (mm/yr)
    remaining_life_csv = Column(String(200))   # RL per point (years)
    short_term_cr_csv = Column(String(200))    # Short-term CR per point (mm/yr)
    long_term_cr_csv = Column(String(200))     # Long-term CR per point (mm/yr)
    cr_type_csv = Column(String(100))          # ST or LT per point

    # Overall worst-case for this reading
    controlling_cr = Column(Numeric(12, 4))    # worst corrosion rate (mm/yr)
    controlling_rl = Column(Numeric(12, 4))    # worst remaining life (years)
    controlling_point_index = Column(Integer)  # 0-based index of worst point

    # Status derived from RL
    status = Column(String(20))  # SAFE | WARNING | CRITICAL

    # Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(200), default="System")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(String(200), default="System")

    # Relationship back to TML
    tml = relationship("InspectionTML", back_populates="readings")
