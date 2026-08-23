"""add prediction_id to telemetry_event

The proxy mints its own correlation key at request time, because TelemetrySink queues
writes and the database-assigned bigint id does not exist yet when the response is
returned. Nullable: SDK-reported events from a customer-hosted model have no
correlation key for delayed labels, and that is an honest absence, not a bug.

Revision ID: 48814b26b964
Revises: c3b8e15d47af
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "48814b26b964"
down_revision: Union[str, Sequence[str], None] = "c3b8e15d47af"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("telemetry_event", sa.Column("prediction_id", sa.Text(), nullable=True))
    op.create_index(
        "ix_telemetry_event_prediction_id", "telemetry_event", ["prediction_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_telemetry_event_prediction_id", table_name="telemetry_event")
    op.drop_column("telemetry_event", "prediction_id")
