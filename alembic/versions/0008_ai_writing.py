"""AI writing analysis and reply draft records.

Revision ID: 0008_ai_writing
Revises: 0007_ai_analysis
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_ai_writing"
down_revision = "0007_ai_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_writing_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=True),
        sa.Column("record_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("workspace_id", "user_id", "provider_id", "account_id", "record_type", "status", "created_at"):
        op.create_index(f"ix_ai_writing_records_{column}", "ai_writing_records", [column], unique=False)


def downgrade() -> None:
    op.drop_table("ai_writing_records")
