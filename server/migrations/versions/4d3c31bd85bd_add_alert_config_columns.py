"""add alert config columns

alert_thresholds freezes the exact metric_set and thresholds a version was promoted
under, the same "as applied" philosophy as eval_run.thresholds: a later change to the
detection defaults must not retroactively change what an already-promoted version is
being watched against. alert_email is where a human receives a notification; nullable
because a model whose owner never registered one simply gets the in-app row alone.

Revision ID: 4d3c31bd85bd
Revises: ec18816eb5c9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d3c31bd85bd"
down_revision: Union[str, Sequence[str], None] = "ec18816eb5c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "monitoring_config", sa.Column("alert_thresholds", sa.JSON(), nullable=True)
    )
    op.add_column("model", sa.Column("alert_email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model", "alert_email")
    op.drop_column("monitoring_config", "alert_thresholds")
