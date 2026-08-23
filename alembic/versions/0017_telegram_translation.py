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
    for name, columns, unique in (
        ("ix_telegram_bot_bindings_workspace_id", ["workspace_id"], True),
        ("ix_telegram_bot_bindings_created_by", ["created_by"], False),
        ("ix_telegram_bot_bindings_admin_telegram_user_id", ["admin_telegram_user_id"], False),
        ("ix_telegram_bot_bindings_enabled", ["enabled"], False),
    ):
        op.create_index(name, "telegram_bot_bindings", columns, unique=unique)
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
        sa.ForeignKeyConstraint(["binding_id"], ["telegram_bot_bindings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", "telegram_user_id", name="uq_telegram_allowed_binding_user"),
    )
    for name, columns in (
        ("ix_telegram_allowed_users_binding_id", ["binding_id"]),
        ("ix_telegram_allowed_users_telegram_user_id", ["telegram_user_id"]),
        ("ix_telegram_allowed_users_enabled", ["enabled"]),
    ):
        op.create_index(name, "telegram_allowed_users", columns)


def downgrade() -> None:
    for name in ("ix_telegram_allowed_users_enabled", "ix_telegram_allowed_users_telegram_user_id", "ix_telegram_allowed_users_binding_id"):
        op.drop_index(name, table_name="telegram_allowed_users")
    op.drop_table("telegram_allowed_users")
    for name in ("ix_telegram_bot_bindings_enabled", "ix_telegram_bot_bindings_admin_telegram_user_id", "ix_telegram_bot_bindings_created_by", "ix_telegram_bot_bindings_workspace_id"):
        op.drop_index(name, table_name="telegram_bot_bindings")
    op.drop_table("telegram_bot_bindings")
