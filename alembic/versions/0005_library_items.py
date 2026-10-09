"""Each user's own media library (``library_items``).

One row per (user, asset key): the user's title, description and keywords for a picture or clip they
uploaded or had generated for one of their lectures (``aadhi.library``). The media stays the shared
content-addressed ``assets`` row, so ``asset_key`` deliberately has no foreign key and nothing per user is
written to ``assets``. Rows go with their user (CASCADE); a deleted lecture leaves ``origin_project_id``
NULL. Nothing to backfill: uploads join the library from now on.

Idempotent: a development SQLite database whose ``create_all`` already created the table (the API creates
missing tables at startup in development) is upgraded without errors.

Revision ID: 0005_library_items
Revises: 0004_claim_operation
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0005_library_items"
down_revision: str | None = "0004_claim_operation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _existing() -> tuple[set[str], set[str]]:
    """(tables, library_items index names); both empty offline (``--sql``)."""
    if context.is_offline_mode():
        return set(), set()
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    indexes = {ix["name"] for ix in insp.get_indexes("library_items")} if "library_items" in tables else set()
    return tables, indexes


def upgrade() -> None:
    tables, indexes = _existing()
    if "library_items" not in tables:
        op.create_table(
            "library_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("asset_key", sa.String(length=128), nullable=False),
            sa.Column("kind", sa.String(length=8), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("description", sa.String(length=1000), nullable=False),
            sa.Column("keywords", JSON_DOC, nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("origin_project_id", sa.Integer(), nullable=True),
            sa.Column("origin_scene_id", sa.String(length=64), nullable=True),
            sa.Column("prompt", sa.String(length=1200), nullable=True),
            sa.Column("provider", sa.String(length=32), nullable=True),
            sa.Column("model", sa.String(length=128), nullable=True),
            sa.Column("search_text", sa.Text(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["origin_project_id"],
                ["projects.id"],
                name=op.f("fk_library_items_origin_project_id_projects"),
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], name=op.f("fk_library_items_user_id_users"), ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_library_items")),
            sa.UniqueConstraint("user_id", "asset_key", name="uq_library_items_user_asset"),
        )
    with op.batch_alter_table("library_items", schema=None) as batch_op:
        if "ix_library_items_asset_key" not in indexes:
            batch_op.create_index(batch_op.f("ix_library_items_asset_key"), ["asset_key"], unique=False)
        if "ix_library_items_user_kind" not in indexes:
            batch_op.create_index("ix_library_items_user_kind", ["user_id", "kind"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("library_items", schema=None) as batch_op:
        batch_op.drop_index("ix_library_items_user_kind")
        batch_op.drop_index(batch_op.f("ix_library_items_asset_key"))

    op.drop_table("library_items")
