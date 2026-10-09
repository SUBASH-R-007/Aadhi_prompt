"""Cross-process generation claims (``asset_claims``).

One row per paid asset generation in flight (``aadhi.storage.assets``): the worker process producing
asset ``key`` holds it, so identical requests in other worker processes wait for the asset instead of
paying again. Rows are short-lived (deleted when the producer ends, renewed while it runs); expired
leftovers of crashed workers are swept by the ``cleanup`` job.

Idempotent: a development SQLite database whose ``create_all`` already created the table (the API
creates missing tables at startup in development) is upgraded without errors.

Revision ID: 0003_asset_claims
Revises: 0002
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0003_asset_claims"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> tuple[set[str], set[str]]:
    """(tables, asset_claims index names); both empty offline (``--sql``)."""
    if context.is_offline_mode():
        return set(), set()
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    indexes = {ix["name"] for ix in insp.get_indexes("asset_claims")} if "asset_claims" in tables else set()
    return tables, indexes


def upgrade() -> None:
    tables, indexes = _existing()
    if "asset_claims" not in tables:
        op.create_table(
            "asset_claims",
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("holder", sa.String(length=64), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=True),
            sa.Column("worker_id", sa.String(length=160), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("key", name=op.f("pk_asset_claims")),
        )
    if "ix_asset_claims_expires_at" not in indexes:
        with op.batch_alter_table("asset_claims", schema=None) as batch_op:
            batch_op.create_index("ix_asset_claims_expires_at", ["expires_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("asset_claims", schema=None) as batch_op:
        batch_op.drop_index("ix_asset_claims_expires_at")

    op.drop_table("asset_claims")
