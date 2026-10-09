"""add tank component life columns

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-21 14:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add Tank component life columns to equipment table
    op.add_column('equipment', sa.Column('roof_controlling_life', sa.Numeric(12, 4), nullable=True))
    op.add_column('equipment', sa.Column('bottom_controlling_life', sa.Numeric(12, 4), nullable=True))

    # Add Tank component life columns to inspection_records table
    op.add_column('inspection_records', sa.Column('roof_remaining_life', sa.Numeric(12, 4), nullable=True))
    op.add_column('inspection_records', sa.Column('bottom_remaining_life', sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    op.drop_column('inspection_records', 'bottom_remaining_life')
    op.drop_column('inspection_records', 'roof_remaining_life')

    op.drop_column('equipment', 'bottom_controlling_life')
    op.drop_column('equipment', 'roof_controlling_life')
