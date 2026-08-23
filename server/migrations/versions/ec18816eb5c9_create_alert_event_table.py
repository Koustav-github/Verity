"""create alert_event table

Falcon's notification, in-app half. Written before any email is attempted — this row is
the source of truth for whether an alert fired; email is best-effort delivery on top of
it, and emailed_at staying null is the only record that delivery didn't happen.

Revision ID: ec18816eb5c9
Revises: f25b10b1e9a6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ec18816eb5c9"
down_revision: Union[str, Sequence[str], None] = "f25b10b1e9a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),  # "systemic" | "quality"
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alert_event_model_version_id", "alert_event", ["model_version_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_event_model_version_id", table_name="alert_event")
    op.drop_table("alert_event")
