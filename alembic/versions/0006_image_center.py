"""AI image generation records.

Revision ID: 0006_image_center
Revises: 0005_chat_center
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_image_center"
down_revision = "0005_chat_center"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_images",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("resolution", sa.String(length=10), nullable=False),
        sa.Column("size", sa.String(length=30), nullable=False),
        sa.Column("quality", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("mime_type", sa.String(length=50), nullable=False),
        sa.Column("file_name", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("image_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("workspace_id", "user_id", "provider_id", "status", "created_at"):
        op.create_index(f"ix_ai_images_{column}", "ai_images", [column], unique=False)


def downgrade() -> None:
    op.drop_table("ai_images")
