"""create manifest table

Revision ID: 90fc6eeb5714
Revises: f632a2d9125a
Create Date: 2026-08-02 21:05:38.774393

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '90fc6eeb5714'
down_revision: Union[str, Sequence[str], None] = 'f632a2d9125a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "manifest",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("model_version_id", sa.Text(), sa.ForeignKey("model_version.id"), nullable=False),
        sa.Column("framework", sa.Text(), nullable=False),
        sa.Column("detected_via", sa.Text(), nullable=True),
        sa.Column("model_class", sa.Text(), nullable=True),
        sa.Column("hyperparameters", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("manifest")
