"""create eval_run table

Nat's output, append-only. Per Schemas.md, `thresholds` is stored **as applied**, not
referenced: a later threshold change must never retroactively alter what a past gate
decided, so the row carries the conditions it was judged against.

Two deliberate extensions over the Schemas.md column list, for the same reason the
shipped `manifest` table extends its spec:

  - `error`   — `verdict` may be `error`, and the reason does not belong in `failed_on`,
                which means "which metrics missed".
  - `fixture` — the typed fixture descriptor (kind, uri, sha256, spec). `test_set_ref`
                stays the spec-faithful text pointer and equals `fixture->>'uri'`. The
                `kind` is what lets later versions add corpus_index / environment_ref
                fixtures without reshaping this table.

Revision ID: b2d5f8c14e77
Revises: a1c4e7b90d33
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d5f8c14e77'
down_revision: Union[str, Sequence[str], None] = 'a1c4e7b90d33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "eval_run",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("mechanism", sa.Text(), nullable=True),
        sa.Column("metric_set", sa.JSON(), nullable=True),
        sa.Column("scores", sa.JSON(), nullable=True),
        sa.Column("thresholds", sa.JSON(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("failed_on", sa.JSON(), nullable=True),
        sa.Column("test_set_ref", sa.Text(), nullable=True),
        sa.Column("fixture", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_eval_run_model_version_id", "eval_run", ["model_version_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_eval_run_model_version_id", table_name="eval_run")
    op.drop_table("eval_run")
