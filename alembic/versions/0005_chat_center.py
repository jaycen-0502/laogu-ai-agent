"""AI chat sessions, messages, and request usage."""

from alembic import op
import sqlalchemy as sa


revision = "0005_chat_center"
down_revision = "0004_ai_providers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("ai_providers.id"), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_chat_sessions_workspace_id", ["workspace_id"]),
        ("ix_chat_sessions_user_id", ["user_id"]),
        ("ix_chat_sessions_provider_id", ["provider_id"]),
        ("ix_chat_sessions_created_at", ["created_at"]),
        ("ix_chat_sessions_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "chat_sessions", columns)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(32), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_chat_messages_session_id", ["session_id"]),
        ("ix_chat_messages_role", ["role"]),
        ("ix_chat_messages_status", ["status"]),
        ("ix_chat_messages_created_at", ["created_at"]),
    ):
        op.create_index(name, "chat_messages", columns)

    op.create_table(
        "ai_usage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace_id", sa.String(32), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("ai_providers.id"), nullable=False),
        sa.Column("session_id", sa.String(32), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(32), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_ai_usage_workspace_id", ["workspace_id"]),
        ("ix_ai_usage_user_id", ["user_id"]),
        ("ix_ai_usage_provider_id", ["provider_id"]),
        ("ix_ai_usage_session_id", ["session_id"]),
        ("ix_ai_usage_message_id", ["message_id"]),
        ("ix_ai_usage_status", ["status"]),
        ("ix_ai_usage_created_at", ["created_at"]),
    ):
        op.create_index(name, "ai_usage", columns)


def downgrade():
    for name in (
        "ix_ai_usage_created_at", "ix_ai_usage_status", "ix_ai_usage_message_id",
        "ix_ai_usage_session_id", "ix_ai_usage_provider_id", "ix_ai_usage_user_id",
        "ix_ai_usage_workspace_id",
    ):
        op.drop_index(name, table_name="ai_usage")
    op.drop_table("ai_usage")
    for name in (
        "ix_chat_messages_created_at", "ix_chat_messages_status",
        "ix_chat_messages_role", "ix_chat_messages_session_id",
    ):
        op.drop_index(name, table_name="chat_messages")
    op.drop_table("chat_messages")
    for name in (
        "ix_chat_sessions_updated_at", "ix_chat_sessions_created_at",
        "ix_chat_sessions_provider_id", "ix_chat_sessions_user_id",
        "ix_chat_sessions_workspace_id",
    ):
        op.drop_index(name, table_name="chat_sessions")
    op.drop_table("chat_sessions")
