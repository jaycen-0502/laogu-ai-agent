"""Add encrypted Telegram translation bot bindings and allowlists."""

from alembic import op
import sqlalchemy as sa


revision = "0017_telegram_translation"
down_revision = "0016_automation_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_bot_bindings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("bot_token_encrypted", sa.Text(), nullable=False),
        sa.Column("bot_token_last4", sa.String(length=4), nullable=False),
        sa.Column("admin_telegram_user_id", sa.String(length=32), nullable=False),
        sa.Column("default_target_language", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )
    op.create_index(
        "ix_telegram_bot_bindings_workspace_id",
        "telegram_bot_bindings",
        ["workspace_id"],
        unique=True,
    )
    op.create_index(
        "ix_telegram_bot_bindings_created_by",
        "telegram_bot_bindings",
        ["created_by"],
    )
    op.create_index(
        "ix_telegram_bot_bindings_admin_telegram_user_id",
        "telegram_bot_bindings",
        ["admin_telegram_user_id"],
    )
    op.create_index(
        "ix_telegram_bot_bindings_enabled",
        "telegram_bot_bindings",
        ["enabled"],
    )
    op.create_table(
        "telegram_allowed_users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("binding_id", sa.String(length=32), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["telegram_bot_bindings.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "binding_id",
            "telegram_user_id",
            name="uq_telegram_allowed_binding_user",
        ),
    )
    op.create_index(
        "ix_telegram_allowed_users_binding_id",
        "telegram_allowed_users",
        ["binding_id"],
    )
    op.create_index(
        "ix_telegram_allowed_users_telegram_user_id",
        "telegram_allowed_users",
        ["telegram_user_id"],
    )
    op.create_index(
        "ix_telegram_allowed_users_enabled",
        "telegram_allowed_users",
        ["enabled"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_allowed_users_enabled",
        table_name="telegram_allowed_users",
    )
    op.drop_index(
        "ix_telegram_allowed_users_telegram_user_id",
        table_name="telegram_allowed_users",
    )
    op.drop_index(
        "ix_telegram_allowed_users_binding_id",
        table_name="telegram_allowed_users",
    )
    op.drop_table("telegram_allowed_users")
    op.drop_index(
        "ix_telegram_bot_bindings_enabled",
        table_name="telegram_bot_bindings",
    )
    op.drop_index(
        "ix_telegram_bot_bindings_admin_telegram_user_id",
        table_name="telegram_bot_bindings",
    )
    op.drop_index(
        "ix_telegram_bot_bindings_created_by",
        table_name="telegram_bot_bindings",
    )
    op.drop_index(
        "ix_telegram_bot_bindings_workspace_id",
        table_name="telegram_bot_bindings",
    )
    op.drop_table("telegram_bot_bindings")
