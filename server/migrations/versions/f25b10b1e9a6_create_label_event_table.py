"""create label_event table

A delayed outcome the customer reports against a prediction_id, keyed to one instance
inside that prediction's batch. Kept as its own table rather than a column on
telemetry_event so a label can arrive fully asynchronously — days or weeks later —
without touching an append-only row.

Unique on (telemetry_event_id, instance_index): reporting the same instance twice is a
correction, upserted rather than accumulated as a duplicate that would double-count in
the next quality check.

Revision ID: f25b10b1e9a6
Revises: 48814b26b964
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f25b10b1e9a6"
down_revision: Union[str, Sequence[str], None] = "48814b26b964"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "label_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "telemetry_event_id",
            sa.BigInteger(),
            sa.ForeignKey("telemetry_event.id"),
            nullable=False,
        ),
        sa.Column("instance_index", sa.Integer(), nullable=False),
        sa.Column("actual", sa.JSON(), nullable=False),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint(
        "uq_label_event_telemetry_event_instance",
        "label_event",
        ["telemetry_event_id", "instance_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_label_event_telemetry_event_instance", "label_event", type_="unique"
    )
    op.drop_table("label_event")
