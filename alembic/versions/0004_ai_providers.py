"""AI provider registry and encrypted credentials."""

from alembic import op
import sqlalchemy as sa


revision = "0004_ai_providers"
down_revision = "0003_script_center"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("provider_type", sa.String(40), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("api_key_last4", sa.String(4), nullable=False),
        sa.Column("default_model", sa.String(160), nullable=False),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("last_test_status", sa.String(20), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_ai_provider_name_workspace"),
    )
    op.create_index("ix_ai_providers_workspace_id", "ai_providers", ["workspace_id"])
    op.create_index("ix_ai_providers_status", "ai_providers", ["status"])
    op.create_index("ix_ai_providers_is_default", "ai_providers", ["is_default"])
    op.create_index("ix_ai_providers_created_by", "ai_providers", ["created_by"])
    op.create_index(
        "uq_ai_provider_default_workspace",
        "ai_providers",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
        sqlite_where=sa.text("is_default = 1"),
    )


def downgrade():
    op.drop_index("uq_ai_provider_default_workspace", table_name="ai_providers")
    op.drop_index("ix_ai_providers_created_by", table_name="ai_providers")
    op.drop_index("ix_ai_providers_is_default", table_name="ai_providers")
    op.drop_index("ix_ai_providers_status", table_name="ai_providers")
    op.drop_index("ix_ai_providers_workspace_id", table_name="ai_providers")
    op.drop_table("ai_providers")
