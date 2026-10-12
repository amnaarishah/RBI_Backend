"""inspection_reading: the three corrosion-rate text columns that the code uses

short_term_cr_csv, long_term_cr_csv and cr_type_csv exist in the model (and in the development database) but no
earlier migration created them, so a database built only by the migrations (Railway) failed when an inspection was
saved or read. Added only when missing; nullable; existing data is not touched.

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'i9d0e1f2a3b4'
down_revision: Union[str, None] = 'h8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    ('short_term_cr_csv', sa.String(200)),
    ('long_term_cr_csv', sa.String(200)),
    ('cr_type_csv', sa.String(100)),
]


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('inspection_reading')}
    for name, type_ in COLUMNS:
        if name not in have:
            op.add_column('inspection_reading', sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in COLUMNS:
        op.drop_column('inspection_reading', name)
