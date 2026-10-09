"""
ASME Material Allowable Stress Table (Section II, Part D — Table 1A)
Strictly isolated from all existing Equipment, CML, and Inspection models.
"""
import uuid
from sqlalchemy import Boolean, Column, String, Numeric, Integer, SmallInteger, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class AsmeMaterialStress(Base):
    """
    Stores allowable stress values (S) from ASME BPVC Section II Part D Table 1A.
    Used exclusively by the t_min ASME calculator feature.
    """
    __tablename__ = "asme_material_stress"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Material Identification
    spec_number     = Column(String(50),  nullable=False, index=True)   # e.g. SA-516
    grade           = Column(String(30),  nullable=False)                # e.g. Gr. 70
    product_form    = Column(String(80),  nullable=False)                # e.g. Plate, Pipe
    material_group  = Column(String(50),  nullable=True)                 # Carbon Steel / SS / Alloy
    code_edition    = Column(String(40),  nullable=False, default="ASME 2023 Section II-D")

    # Temperature & Stress (both units stored for user flexibility)
    temp_f          = Column(Integer,     nullable=False)                # Temperature in °F
    temp_c          = Column(Integer,     nullable=False)                # Temperature in °C (derived)

    # Allowable Stress values in all common units
    allowable_stress_ksi  = Column(Numeric(10, 3), nullable=True)        # ksi (ASME customary)
    allowable_stress_psi  = Column(Numeric(12, 2), nullable=True)        # psi
    allowable_stress_mpa  = Column(Numeric(10, 3), nullable=True)        # MPa (SI)
    allowable_stress_bar  = Column(Numeric(10, 4), nullable=True)        # bar

    # Optional supplementary info
    min_tensile_strength_ksi = Column(Numeric(10, 2), nullable=True)
    min_yield_strength_ksi   = Column(Numeric(10, 2), nullable=True)
    notes           = Column(String(300), nullable=True)

    # Clean identity + source position in ASME II-D (filled by app.seed.backfill_asme_metadata)
    table_name      = Column(String(10),  nullable=True)    # "Table 1A" | "Table 1B"
    pdf_page        = Column(Integer,     nullable=True)    # PDF page of the specification row
    line_no         = Column(Integer,     nullable=True)    # line number inside the table
    uns_no          = Column(String(20),  nullable=True)    # e.g. K02700, S30403
    clean_spec      = Column(String(50),  nullable=True)    # e.g. SA-516
    clean_grade     = Column(String(60),  nullable=True)    # e.g. 70, TP304L
    product_type    = Column(String(40),  nullable=True)    # Plate/Sheet | Pipe | Tube | Forging | ...
    condition_text  = Column(String(200), nullable=True)    # class / condition / size text
    description     = Column(String(300), nullable=True)    # full row text from the PDF
    stress_decimals = Column(SmallInteger, nullable=True)   # decimals of this stress value as printed in the book ("20.0" -> 1)
    # strength / applicability page of the table (the page after the specification page)
    min_tensile_ksi = Column(Numeric(6, 2), nullable=True)
    min_yield_ksi   = Column(Numeric(6, 2), nullable=True)
    viii1_limit_f   = Column(SmallInteger, nullable=True)   # Section VIII-1 temperature limit, F
    viii1_permitted = Column(Boolean, nullable=True)        # False = "NP": not permitted in Section VIII-1
    notes_text      = Column(String(200), nullable=True)    # the book's notes of the line (G3, W6, ...)
    size_thickness  = Column(String(40), nullable=True)     # Table 1B size / thickness range, in.

    __table_args__ = (
        UniqueConstraint("spec_number", "grade", "product_form", "temp_f", "code_edition",
                         name="uq_asme_material_stress"),
    )
