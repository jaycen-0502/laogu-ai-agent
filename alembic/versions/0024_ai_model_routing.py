"""Add ai_model_registry, ai_relay_config, and x_account_personas tables.

Revision ID: 0024_ai_model_routing
Revises: 0023_app_releases
Create Date: 2026-10-12 21:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_ai_model_routing"
down_revision = "0023_app_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_model_registry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("model_code", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_model_registry_model_code", "ai_model_registry", ["model_code"], unique=True)
    op.create_index("ix_ai_model_registry_status", "ai_model_registry", ["status"])

    op.create_table(
        "ai_relay_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("base_url", sa.String(length=500), nullable=False, server_default="https://api.openai.com/v1"),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("default_model", sa.String(length=120), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("fallback_model", sa.String(length=120), nullable=False, server_default="deepseek-chat"),
        sa.Column("timeout", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "x_account_personas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(length=80), nullable=False),
        sa.Column("persona_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("custom_model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.75"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_x_account_personas_account_id", "x_account_personas", ["account_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_x_account_personas_account_id", table_name="x_account_personas")
    op.drop_table("x_account_personas")
    op.drop_table("ai_relay_config")
    op.drop_index("ix_ai_model_registry_status", table_name="ai_model_registry")
    op.drop_index("ix_ai_model_registry_model_code", table_name="ai_model_registry")
    op.drop_table("ai_model_registry")
