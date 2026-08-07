"""create model table

The logical model, grouping versions across uploads. Fury's identity layer — every
model_version links here via model_id (added in the next migration) so "which versions
belong to the same model" has a real answer instead of being inferred.

Revision ID: c8e51f4d9a06
Revises: b2d5f8c14e77
Create Date: 2026-08-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e51f4d9a06'
down_revision: Union[str, Sequence[str], None] = 'b2d5f8c14e77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model_class", sa.Text(), nullable=True),
        sa.Column("task_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint("uq_model_user_id_name", "model", ["user_id", "name"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_model_user_id_name", "model", type_="unique")
    op.drop_table("model")
