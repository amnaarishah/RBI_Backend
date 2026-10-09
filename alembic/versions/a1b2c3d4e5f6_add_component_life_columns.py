"""add_component_life_columns

Revision ID: a1b2c3d4e5f6
Revises: 8b07a4c49170
Create Date: 2026-09-15 17:00:00.000000

Adds component-wise remaining life and API 510 dual inspection date columns
to both equipment and inspection_records tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '2c4df4e36f7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Equipment table: component-wise life + API 510 inspection due dates
    op.add_column('equipment', sa.Column('shell_controlling_life',  sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('equipment', sa.Column('head_controlling_life',   sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('equipment', sa.Column('tube_controlling_life',   sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('equipment', sa.Column('next_internal_inspection_date', sa.DateTime(timezone=True), nullable=True))
    op.add_column('equipment', sa.Column('next_external_inspection_date', sa.DateTime(timezone=True), nullable=True))

    # Inspection Records table: same fields per-record
    op.add_column('inspection_records', sa.Column('shell_remaining_life',  sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('inspection_records', sa.Column('head_remaining_life',   sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('inspection_records', sa.Column('tube_remaining_life',   sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('inspection_records', sa.Column('next_internal_due',     sa.DateTime(timezone=True), nullable=True))
    op.add_column('inspection_records', sa.Column('next_external_due',     sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('inspection_records', 'next_external_due')
    op.drop_column('inspection_records', 'next_internal_due')
    op.drop_column('inspection_records', 'tube_remaining_life')
    op.drop_column('inspection_records', 'head_remaining_life')
    op.drop_column('inspection_records', 'shell_remaining_life')

    op.drop_column('equipment', 'next_external_inspection_date')
    op.drop_column('equipment', 'next_internal_inspection_date')
    op.drop_column('equipment', 'tube_controlling_life')
    op.drop_column('equipment', 'head_controlling_life')
    op.drop_column('equipment', 'shell_controlling_life')
