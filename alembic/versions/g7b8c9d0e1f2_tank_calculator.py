"""tank t_min calculator: material table, and two result columns on the equipment profile

* tank_material: the material table of the client's `tank calculations.xlsx` (filled by app.seed.seed_tank_materials)
* equipment.bottom_critical_zone_min_thickness / annular_min_thickness: results of the tank calculator
  (the sheet's "t-min Bottom at critical zone" and "t-min annular plates"). Both nullable; nothing else changes.

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'g7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_EQUIPMENT_COLUMNS = ['bottom_critical_zone_min_thickness', 'annular_min_thickness']


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'tank_material' not in insp.get_table_names():
        op.create_table(
            'tank_material',
            sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column('name', sa.String(80), nullable=False, unique=True),
            sa.Column('spec', sa.String(40), nullable=False),
            sa.Column('grade', sa.String(40)),
            sa.Column('is_unknown', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('yield_mpa', sa.Numeric(8, 2)),
            sa.Column('tensile_mpa', sa.Numeric(8, 2)),
            sa.Column('s_lower_mpa', sa.Numeric(8, 2)),
            sa.Column('s_upper_mpa', sa.Numeric(8, 2)),
            sa.Column('st_lower_mpa', sa.Numeric(8, 2)),
            sa.Column('st_upper_mpa', sa.Numeric(8, 2)),
            sa.Column('sheet_row', sa.Integer()),
        )
    have = {c['name'] for c in insp.get_columns('equipment')}
    for name in NEW_EQUIPMENT_COLUMNS:
        if name not in have:
            op.add_column('equipment', sa.Column(name, sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    for name in NEW_EQUIPMENT_COLUMNS:
        op.drop_column('equipment', name)
    op.drop_table('tank_material')
