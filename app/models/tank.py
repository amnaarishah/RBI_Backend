"""
Storage-tank material table of the client's `tank calculations.xlsx` ('links' sheet).
Used only by the tank t_min calculator; independent of Equipment, CML and Inspection.
"""
import uuid
from sqlalchemy import Boolean, Column, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class TankMaterial(Base):
    __tablename__ = "tank_material"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(80), nullable=False, unique=True)     # as in the sheet, e.g. "A285 Grade C"
    spec = Column(String(40), nullable=False)                  # A285
    grade = Column(String(40), nullable=True)                  # C
    is_unknown = Column(Boolean, nullable=False, default=False)  # the sheet's "Unknown Grade" row

    yield_mpa = Column(Numeric(8, 2))      # Y
    tensile_mpa = Column(Numeric(8, 2))    # T
    s_lower_mpa = Column(Numeric(8, 2))    # allowable product stress S, lower two courses (the value the sheet uses)
    s_upper_mpa = Column(Numeric(8, 2))    # allowable product stress S, upper courses
    st_lower_mpa = Column(Numeric(8, 2))   # allowable hydrostatic test stress St, lower two courses
    st_upper_mpa = Column(Numeric(8, 2))   # allowable hydrostatic test stress St, upper courses
    sheet_row = Column(Integer)            # row of the client's sheet this was read from
