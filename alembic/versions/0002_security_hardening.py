"""Agent tokens and security audit log."""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timedelta, timezone
from uuid import uuid4

revision = "0002_security"
down_revision = "0001_stage7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_tokens", sa.Column("token_id", sa.String(32), primary_key=True), sa.Column("agent_id", sa.String(32), sa.ForeignKey("agents.id"), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("last_used_at", sa.DateTime(timezone=True)), sa.Column("status", sa.String(20), nullable=False))
    op.create_index("ix_agent_tokens_agent_id", "agent_tokens", ["agent_id"]); op.create_index("ix_agent_tokens_token_hash", "agent_tokens", ["token_hash"]); op.create_index("ix_agent_tokens_status", "agent_tokens", ["status"])
    connection = op.get_bind()
    agents = sa.table("agents", sa.column("id", sa.String), sa.column("token_hash", sa.String))
    tokens = sa.table("agent_tokens", sa.column("token_id", sa.String), sa.column("agent_id", sa.String), sa.column("token_hash", sa.String), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("expires_at", sa.DateTime(timezone=True)), sa.column("revoked_at", sa.DateTime(timezone=True)), sa.column("last_used_at", sa.DateTime(timezone=True)), sa.column("status", sa.String))
    current = datetime.now(timezone.utc)
    for row in connection.execute(sa.select(agents.c.id, agents.c.token_hash)):
        if row.token_hash:
            connection.execute(tokens.insert().values(token_id=uuid4().hex, agent_id=row.id, token_hash=row.token_hash, created_at=current, expires_at=current + timedelta(days=365), revoked_at=None, last_used_at=None, status="ACTIVE"))
    connection.execute(agents.update().values(token_hash=""))
    op.create_table("audit_logs", sa.Column("audit_id", sa.String(32), primary_key=True), sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("user_id", sa.String(32)), sa.Column("workspace_id", sa.String(32)), sa.Column("agent_id", sa.String(32)), sa.Column("action", sa.String(80), nullable=False), sa.Column("resource_type", sa.String(50), nullable=False), sa.Column("resource_id", sa.String(100), nullable=False), sa.Column("result", sa.String(20), nullable=False), sa.Column("ip", sa.String(80), nullable=False), sa.Column("user_agent", sa.String(300), nullable=False), sa.Column("message", sa.String(500), nullable=False))
    for name, cols in (("ix_audit_logs_timestamp", ["timestamp"]), ("ix_audit_logs_user_id", ["user_id"]), ("ix_audit_logs_workspace_id", ["workspace_id"]), ("ix_audit_logs_agent_id", ["agent_id"]), ("ix_audit_logs_action", ["action"])): op.create_index(name, "audit_logs", cols)


def downgrade():
    connection = op.get_bind()
    agents = sa.table("agents", sa.column("id", sa.String), sa.column("token_hash", sa.String))
    tokens = sa.table(
        "agent_tokens",
        sa.column("agent_id", sa.String),
        sa.column("token_hash", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
        sa.column("status", sa.String),
    )
    connection.execute(agents.update().values(token_hash=""))
    restored = set()
    current = datetime.now(timezone.utc)
    rows = connection.execute(
        sa.select(tokens.c.agent_id, tokens.c.token_hash)
        .where(tokens.c.status == "ACTIVE", tokens.c.revoked_at.is_(None), tokens.c.expires_at > current)
        .order_by(tokens.c.created_at.desc())
    )
    for row in rows:
        if row.agent_id not in restored:
            connection.execute(agents.update().where(agents.c.id == row.agent_id).values(token_hash=row.token_hash))
            restored.add(row.agent_id)
    op.drop_table("audit_logs")
    op.drop_table("agent_tokens")
