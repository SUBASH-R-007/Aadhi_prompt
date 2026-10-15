"""Administrator-set runtime switches (``app_settings``).

One row per switch (``aadhi.runtime_flags``), today only ``dynamic_enabled``: the Dynamic video mode, which an
administrator turns on in the Studio instead of in the environment. ``value`` is a JSON scalar so a later switch
needs no migration. Absent row = the environment's value rules, which is exactly the behaviour before this table,
so an un-upgraded database keeps working.

Idempotent: a development SQLite database whose ``create_all`` already created the table (the API creates missing
tables at startup in development) is upgraded without errors.

Revision ID: 0007_app_settings
Revises: 0006_visual_reviews
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0007_app_settings"
down_revision: str | None = "0006_visual_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    """Existing table names (empty offline: ``--sql`` emits the DDL unconditionally)."""
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "app_settings" in _tables():
        return
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_app_settings_updated_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
