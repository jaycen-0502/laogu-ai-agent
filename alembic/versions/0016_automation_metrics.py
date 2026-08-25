"""Store per-account desktop automation counters."""

from alembic import op
import sqlalchemy as sa


revision = "0016_automation_metrics"
down_revision = "0015_agent_device_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_metrics",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("x_account_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("account_tag", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ERROR"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("follows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scanned_posts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("own_followers", sa.Integer(), nullable=True),
        sa.Column("own_following", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_automation_metrics_run_id", "automation_metrics", ["run_id"], unique=True)
    op.create_index("ix_automation_metrics_workspace_id", "automation_metrics", ["workspace_id"])
    op.create_index("ix_automation_metrics_agent_id", "automation_metrics", ["agent_id"])
    op.create_index("ix_automation_metrics_profile_id", "automation_metrics", ["profile_id"])
    op.create_index("ix_automation_metrics_x_account_id", "automation_metrics", ["x_account_id"])
    op.create_index("ix_automation_metrics_metric_date", "automation_metrics", ["metric_date"])
    op.create_index("ix_automation_metrics_finished_at", "automation_metrics", ["finished_at"])
    op.create_index("ix_automation_metrics_agent_profile_date", "automation_metrics", ["agent_id", "profile_id", "metric_date"])


def downgrade() -> None:
    op.drop_table("automation_metrics")
