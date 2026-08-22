"""create deployment table

Where a promoted version actually runs. Schemas.md listed this under V6 until
api-fication pulled it forward to V1, because V1 now serves.

A version that is promoted, replaced, and later re-promoted gets a NEW row each time
rather than reusing one: the history of what served when is worth keeping, and reusing a
row would erase it. Only `status` and `stopped_at` are ever updated in place.

`error` exists for the same reason `eval_run.error` does. A deploy can fail for reasons
that are not a metric missing a threshold — image build failure, dependency resolution,
an artifact that loads in the training environment but not the built one — and those
reasons belong on record rather than in a log line.

Revision ID: c3b8e15d47af
Revises: a7f19c4e02b3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3b8e15d47af"
down_revision: Union[str, Sequence[str], None] = "a7f19c4e02b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "deployment",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("image_tag", sa.Text(), nullable=False),
        # Null while `building`, and stays null if the build never produced a container.
        sa.Column("container_id", sa.Text(), nullable=True),
        sa.Column("host_port", sa.Integer(), nullable=True),
        sa.Column("endpoint_url", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The proxy resolves a live deployment on every prediction, keyed on this column.
    op.create_index("ix_deployment_model_version_id", "deployment", ["model_version_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_deployment_model_version_id", table_name="deployment")
    op.drop_table("deployment")
