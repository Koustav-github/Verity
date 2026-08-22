"""add serving columns to manifest

io_schema and serving_pattern were specced in Schemas.md from the very first draft and
never created. This adds them, plus `environment`.

`environment` is an addition to that spec rather than an original member of it, made for
the same reason `eval_run.fixture` was: without it, io_schema describes a model whose
runtime requirements are unrecorded, and a serving image could not be rebuilt
reproducibly from stored rows alone. It lives on the manifest because it describes the
artifact *as identified*, and because a redeploy has to work without the original upload
still being in memory.

All three are nullable: every manifest written before api-fication legitimately has none
of them, and backfilling a guess would be worse than a null.

Revision ID: a7f19c4e02b3
Revises: e91a3d7c5b28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7f19c4e02b3"
down_revision: Union[str, Sequence[str], None] = "e91a3d7c5b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("manifest", sa.Column("io_schema", sa.JSON(), nullable=True))
    op.add_column("manifest", sa.Column("environment", sa.JSON(), nullable=True))
    op.add_column("manifest", sa.Column("serving_pattern", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("manifest", "serving_pattern")
    op.drop_column("manifest", "environment")
    op.drop_column("manifest", "io_schema")
