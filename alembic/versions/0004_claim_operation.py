"""``asset_claims.operation``: the paid provider job a generation claim's holder started.

When the holder of a claim stops (crash, OOM kill, lost lease) after a resumable provider (Veo) accepted the
paid job, the job that takes the claim over reads the operation from the claim row and polls it instead of
paying for a new clip (``aadhi.storage.assets``, ``aadhi.providers.operations``). A nullable JSON column
(JSONB on PostgreSQL); existing rows get NULL (short-lived rows: nothing to backfill).

Idempotent: a development SQLite database whose ``create_all`` / ``add_missing_columns`` already added the
column is upgraded without errors.

Revision ID: 0004_claim_operation
Revises: 0003_asset_claims
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0004_claim_operation"
down_revision: str | None = "0003_asset_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _columns() -> set[str] | None:
    """Column names of ``asset_claims`` (None offline: ``--sql`` emits the DDL unconditionally)."""
    if context.is_offline_mode():
        return None
    insp = sa.inspect(op.get_bind())
    if "asset_claims" not in set(insp.get_table_names()):
        return set()
    return {c["name"] for c in insp.get_columns("asset_claims")}


def upgrade() -> None:
    columns = _columns()
    if columns is None or "operation" not in columns:
        with op.batch_alter_table("asset_claims", schema=None) as batch_op:
            batch_op.add_column(sa.Column("operation", JSON_DOC, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset_claims", schema=None) as batch_op:
        batch_op.drop_column("operation")
