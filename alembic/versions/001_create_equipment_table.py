"""Script template"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("equipment_id", sa.String(50), nullable=False),
        sa.Column("equipment_name", sa.String(200), nullable=False),
        sa.Column("equipment_type", sa.String(100), nullable=False),
        sa.Column("equipment_category", sa.String(100)),
        sa.Column("asset_tag", sa.String(100)),
        sa.Column("plant", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(100), nullable=False),
        sa.Column("area", sa.String(100)),
        sa.Column("location", sa.String(200)),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
        sa.Column("criticality", sa.String(50)),
        sa.Column("manufacturer", sa.String(200)),
        sa.Column("model", sa.String(200)),
        sa.Column("serial_number", sa.String(200)),
        sa.Column("material", sa.String(100)),
        sa.Column("material_grade", sa.String(100)),
        sa.Column("construction_type", sa.String(100)),
        sa.Column("design_code", sa.String(200)),
        sa.Column("equipment_class", sa.String(100)),
        sa.Column("specification", sa.Text()),
        sa.Column("design_pressure", sa.Numeric(12, 4)),
        sa.Column("operating_pressure", sa.Numeric(12, 4)),
        sa.Column("pressure_unit", sa.String(20)),
        sa.Column("design_temperature", sa.Numeric(12, 4)),
        sa.Column("operating_temperature", sa.Numeric(12, 4)),
        sa.Column("temperature_unit", sa.String(20)),
        sa.Column("service", sa.String(200)),
        sa.Column("fluid", sa.String(200)),
        sa.Column("flow_rate", sa.Numeric(12, 4)),
        sa.Column("flow_rate_unit", sa.String(20)),
        sa.Column("nominal_thickness", sa.Numeric(12, 4)),
        sa.Column("thickness_unit", sa.String(20)),
        sa.Column("diameter", sa.Numeric(12, 4)),
        sa.Column("diameter_unit", sa.String(20)),
        sa.Column("length", sa.Numeric(12, 4)),
        sa.Column("length_unit", sa.String(20)),
        sa.Column("weight", sa.Numeric(12, 4)),
        sa.Column("weight_unit", sa.String(20)),
        sa.Column("capacity", sa.Numeric(12, 4)),
        sa.Column("capacity_unit", sa.String(20)),
        sa.Column("manufacturing_year", sa.Integer()),
        sa.Column("purchase_date", sa.DateTime(timezone=True)),
        sa.Column("installation_date", sa.DateTime(timezone=True)),
        sa.Column("commissioning_date", sa.DateTime(timezone=True)),
        sa.Column("warranty_expiry", sa.DateTime(timezone=True)),
        sa.Column("expected_service_life", sa.Integer()),
        sa.Column("department", sa.String(200)),
        sa.Column("responsible_department", sa.String(200)),
        sa.Column("asset_owner", sa.String(200)),
        sa.Column("responsible_team", sa.String(200)),
        sa.Column("notes", sa.Text()),
        sa.Column("technical_remarks", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(200), server_default="System"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("updated_by", sa.String(200), server_default="System"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("equipment_id"),
    )
    op.create_index("ix_equipment_equipment_id", "equipment", ["equipment_id"])
    op.create_index("ix_equipment_plant", "equipment", ["plant"])
    op.create_index("ix_equipment_status", "equipment", ["status"])
    op.create_index("ix_equipment_criticality", "equipment", ["criticality"])


def downgrade() -> None:
    op.drop_table("equipment")
