"""add model_id and promoted_from to model_version

Both are part of Schemas.md's original model_version spec but neither was ever actually
migrated — only artifact_sha256/artifact_uri/user_id/args/status/created_at exist in the
real table today. This adds the two columns Fury needs: model_id links a version to its
logical model (Fury's identity layer); promoted_from records which eval_run justified a
production promotion, so "why is this version live" is always answerable from the row
itself, not from memory of how the pipeline behaved that day.

Revision ID: d4a729c6e153
Revises: c8e51f4d9a06
Create Date: 2026-08-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a729c6e153'
down_revision: Union[str, Sequence[str], None] = 'c8e51f4d9a06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "model_version",
        sa.Column("model_id", sa.Text(), sa.ForeignKey("model.id"), nullable=True),
    )
    op.add_column(
        "model_version",
        sa.Column("promoted_from", sa.Text(), sa.ForeignKey("eval_run.id"), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("model_version", "promoted_from")
    op.drop_column("model_version", "model_id")
