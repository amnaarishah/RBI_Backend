"""tank t_min calculator: remember its inputs on the equipment profile

The calculator window used to open empty every time. Its inputs are now kept on the profile (all nullable, nothing else
changes): specific gravity G, total height H (mm), joint efficiency E and the bottom leak-protection answer.

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'h8c9d0e1f2a3'
down_revision: Union[str, None] = 'g7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    ('tank_specific_gravity', sa.Numeric(12, 4)),
    ('tank_height', sa.Numeric(12, 4)),
    ('tank_joint_efficiency', sa.Numeric(12, 4)),
    ('tank_bottom_leak_protection', sa.Boolean()),
]


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('equipment')}
    for name, type_ in COLUMNS:
        if name not in have:
            op.add_column('equipment', sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in COLUMNS:
        op.drop_column('equipment', name)
