"""add index on model_version.model_id

d4a729c6e153 added the model_id column but no index for it, breaking this codebase's own
precedent (b2d5f8c14e77 indexes its own FK column, ix_eval_run_model_version_id, in the
same style). Both find_model_version_by_hash and find_production_version in
storage/models/supabase.py filter on model_id, and both run on every single upload — this
index is on the hot path.

Revision ID: 708d94baed01
Revises: d4a729c6e153
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '708d94baed01'
down_revision: Union[str, Sequence[str], None] = 'd4a729c6e153'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_model_version_model_id", "model_version", ["model_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_model_version_model_id", table_name="model_version")
