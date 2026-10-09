"""asme_material_stress: strength / Section VIII-1 applicability / notes of each table line

Page after the specification page of ASME II-D Table 1A / 1B: min tensile and yield strength, the Section VIII-1
limit (a temperature, or NP = not permitted), the notes (G3, W6, ...) and, in Table 1B, the size/thickness range.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    ('min_tensile_ksi', sa.Numeric(6, 2)),
    ('min_yield_ksi', sa.Numeric(6, 2)),
    ('viii1_limit_f', sa.SmallInteger()),
    ('viii1_permitted', sa.Boolean()),
    ('notes_text', sa.String(200)),
    ('size_thickness', sa.String(40)),
]


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('asme_material_stress')}
    for name, type_ in COLUMNS:
        if name not in have:
            op.add_column('asme_material_stress', sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in COLUMNS:
        op.drop_column('asme_material_stress', name)
