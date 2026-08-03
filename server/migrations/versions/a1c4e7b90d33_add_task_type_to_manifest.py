"""add task_type to manifest

Hawkeye's coarse read of what kind of problem the model solves. Nullable because it is
genuinely unrecoverable from some artifacts, and a guessed task type would send Nat to
the wrong row of the Atlas. Binary vs. multiclass is not decided here — the eval
mechanism refines it from the fixture's label cardinality.

Revision ID: a1c4e7b90d33
Revises: 90fc6eeb5714
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4e7b90d33'
down_revision: Union[str, Sequence[str], None] = '90fc6eeb5714'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("manifest", sa.Column("task_type", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("manifest", "task_type")
