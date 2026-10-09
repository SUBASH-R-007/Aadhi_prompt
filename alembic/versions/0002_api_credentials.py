"""API keys saved in the Studio (``api_credentials``) and ``usage_events.billed_to``.

* ``api_credentials``: one encrypted key per (owner, provider); owner ``server`` (admin, used for
  everyone) or ``user:<id>`` (personal, used only for that user's jobs; deleted with the user).
* ``usage_events.billed_to``: ``server`` (existing rows) or ``user`` (paid with a personal key, which
  the daily budget ignores).

Idempotent: a development SQLite database whose ``create_all`` already created the table or column
(the API creates missing tables at startup in development) is upgraded without errors.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> tuple[set[str], set[str], set[str]]:
    """(tables, api_credentials index names, usage_events column names); all empty offline (``--sql``)."""
    if context.is_offline_mode():
        return set(), set(), set()
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    indexes = {ix["name"] for ix in insp.get_indexes("api_credentials")} if "api_credentials" in tables else set()
    columns = {c["name"] for c in insp.get_columns("usage_events")} if "usage_events" in tables else set()
    return tables, indexes, columns


def upgrade() -> None:
    tables, indexes, columns = _existing()
    if "api_credentials" not in tables:
        op.create_table(
            "api_credentials",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_key", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("provider", sa.String(length=16), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("key_hint", sa.String(length=32), nullable=False),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(length=300), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["created_by_id"],
                ["users.id"],
                name=op.f("fk_api_credentials_created_by_id_users"),
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], name=op.f("fk_api_credentials_user_id_users"), ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_api_credentials")),
            sa.UniqueConstraint("owner_key", "provider", name="uq_api_credentials_owner_provider"),
        )
    if "ix_api_credentials_user_id" not in indexes:
        with op.batch_alter_table("api_credentials", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_api_credentials_user_id"), ["user_id"], unique=False)

    if "billed_to" not in columns:
        with op.batch_alter_table("usage_events", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("billed_to", sa.String(length=8), server_default="server", nullable=False)
            )


def downgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_column("billed_to")

    with op.batch_alter_table("api_credentials", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_credentials_user_id"))

    op.drop_table("api_credentials")
