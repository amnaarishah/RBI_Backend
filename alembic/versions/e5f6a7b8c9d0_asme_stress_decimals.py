"""asme_material_stress: number of decimals of each stress value as printed in the book

ASME II-D Table 1A / 1B note: an interpolated value is rounded to the same number of decimal places as the
value at the higher of the two temperatures. The book's own decimals (e.g. "20.0" = 1) are stored here.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('asme_material_stress')}
    if 'stress_decimals' not in cols:
        op.add_column('asme_material_stress', sa.Column('stress_decimals', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('asme_material_stress', 'stress_decimals')
