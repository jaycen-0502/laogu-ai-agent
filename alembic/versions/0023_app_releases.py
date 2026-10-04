"""Add app_releases table for desktop client OTA updates.

Revision ID: 0023_app_releases
Revises: 0022_studio_visited_targets
Create Date: 2026-10-04 17:25:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_app_releases"
down_revision = "0022_studio_visited_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_releases",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="stable"),
        sa.Column("release_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("package_path", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("package_filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("package_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version", "channel", name="uq_app_release_version_channel"),
    )
    op.create_index("ix_app_releases_version", "app_releases", ["version"])
    op.create_index("ix_app_releases_channel", "app_releases", ["channel"])
    op.create_index("ix_app_releases_is_active", "app_releases", ["is_active"])
    op.create_index("ix_app_releases_created_by", "app_releases", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_app_releases_created_by", table_name="app_releases")
    op.drop_index("ix_app_releases_is_active", table_name="app_releases")
    op.drop_index("ix_app_releases_channel", table_name="app_releases")
    op.drop_index("ix_app_releases_version", table_name="app_releases")
    op.drop_table("app_releases")
