"""Visual Review sign-offs (``visual_reviews``).

One row per (version, scene): the teacher's state for the scene's visual (pending, approved, changed, removed),
the fingerprint of what the scene asked its visual to show when the state was set, an optional note and who set
it (``aadhi.review``). Advisory and kept outside the screenplay, so approving never changes a scene hash or a
build cache. Rows go with their version (CASCADE); a deleted user leaves ``updated_by`` NULL.

Idempotent: a development SQLite database whose ``create_all`` already created the table (the API creates
missing tables at startup in development) is upgraded without errors.

Revision ID: 0006_visual_reviews
Revises: 0005_library_items
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "0006_visual_reviews"
down_revision: str | None = "0005_library_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    """Existing table names (empty offline: ``--sql`` emits the DDL unconditionally)."""
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "visual_reviews" in _tables():
        return
    op.create_table(
        "visual_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("scene_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["project_versions.id"],
            name=op.f("fk_visual_reviews_version_id_project_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_visual_reviews_updated_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visual_reviews")),
        sa.UniqueConstraint("version_id", "scene_id", name="uq_visual_reviews_version_scene"),
    )


def downgrade() -> None:
    op.drop_table("visual_reviews")
