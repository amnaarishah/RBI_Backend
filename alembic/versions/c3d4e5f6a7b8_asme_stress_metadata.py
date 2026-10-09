"""asme_material_stress: source/identity metadata columns

Adds clean material identity and the source position in ASME II-D (table, PDF page, line)
so the UI no longer has to parse garbled text. Also creates the table on a fresh database
(it used to be created only by the seed script).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_COLUMNS = [
    ('table_name', sa.String(10)),
    ('pdf_page', sa.Integer()),
    ('line_no', sa.Integer()),
    ('uns_no', sa.String(20)),
    ('clean_spec', sa.String(50)),
    ('clean_grade', sa.String(60)),
    ('product_type', sa.String(40)),
    ('condition_text', sa.String(200)),
    ('description', sa.String(300)),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'asme_material_stress' not in insp.get_table_names():
        op.create_table(
            'asme_material_stress',
            sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column('spec_number', sa.String(50), nullable=False, index=True),
            sa.Column('grade', sa.String(30), nullable=False),
            sa.Column('product_form', sa.String(80), nullable=False),
            sa.Column('material_group', sa.String(50)),
            sa.Column('code_edition', sa.String(40), nullable=False),
            sa.Column('temp_f', sa.Integer(), nullable=False),
            sa.Column('temp_c', sa.Integer(), nullable=False),
            sa.Column('allowable_stress_ksi', sa.Numeric(10, 3)),
            sa.Column('allowable_stress_psi', sa.Numeric(12, 2)),
            sa.Column('allowable_stress_mpa', sa.Numeric(10, 3)),
            sa.Column('allowable_stress_bar', sa.Numeric(10, 4)),
            sa.Column('min_tensile_strength_ksi', sa.Numeric(10, 2)),
            sa.Column('min_yield_strength_ksi', sa.Numeric(10, 2)),
            sa.Column('notes', sa.String(300)),
            sa.UniqueConstraint('spec_number', 'grade', 'product_form', 'temp_f', 'code_edition',
                                name='uq_asme_material_stress'),
        )
    existing = {c['name'] for c in sa.inspect(bind).get_columns('asme_material_stress')}
    for name, typ in NEW_COLUMNS:
        if name not in existing:
            op.add_column('asme_material_stress', sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('asme_material_stress')}
    for name, _ in NEW_COLUMNS:
        if name in existing:
            op.drop_column('asme_material_stress', name)
