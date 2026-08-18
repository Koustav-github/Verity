"""create telemetry_event and monitoring_config

Falcon's two tables, introduced together.

telemetry_event was specced in Schemas.md from V1 and never created. inputs/prediction are
created here but deliberately not written at V1 — they exist for V7 drift detection, so
that work lands as a writer rather than as another migration.

monitoring_config is new: the README's pipeline diagram names a "monitoring config" as
Falcon's output but no table was ever specced for it. Added here and recorded in Schemas.md,
the same way Fury's deviations were documented rather than left to diverge silently.

Revision ID: e91a3d7c5b28
Revises: 708d94baed01
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e91a3d7c5b28'
down_revision: Union[str, Sequence[str], None] = '708d94baed01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "telemetry_event",
        # The only non-prefixed id in the schema — Schemas.md specs bigint here because
        # this table is append-only and high-volume, unlike the mv_/evr_/mdl_ tables.
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=True),
        sa.Column("prediction", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
    )
    # The read path's only access pattern: one version, one time window.
    op.create_index(
        "ix_telemetry_event_version_time",
        "telemetry_event",
        ["model_version_id", "occurred_at"],
    )

    op.create_table(
        "monitoring_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("eval_run_id", sa.Text(), sa.ForeignKey("eval_run.id"), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("eval_reference", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_monitoring_config_model_version_id",
        "monitoring_config",
        ["model_version_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_monitoring_config_model_version_id", table_name="monitoring_config")
    op.drop_table("monitoring_config")
    op.drop_index("ix_telemetry_event_version_time", table_name="telemetry_event")
    # Lossy and unrecoverable: this destroys all collected production telemetry.
    op.drop_table("telemetry_event")
