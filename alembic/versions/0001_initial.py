"""Initial production schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("password_salt", sa.String(length=64), nullable=False),
        sa.Column("style_preference", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "cache_records",
        sa.Column("kind", sa.String(length=40), primary_key=True),
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "planning_sessions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("task", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("events", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "support_threads",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_threads_user_id", "support_threads", ["user_id"])
    op.create_table(
        "support_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_messages_thread_id", "support_messages", ["thread_id"])
    op.create_table(
        "attraction_tasks",
        sa.Column("job_id", sa.String(length=32), primary_key=True),
        sa.Column("poi_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("events", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_attraction_tasks_poi_id", "attraction_tasks", ["poi_id"])
    op.create_index("ix_attraction_tasks_idempotency_key", "attraction_tasks", ["idempotency_key"], unique=True)
    op.create_index("ix_attraction_tasks_status", "attraction_tasks", ["status"])
    op.create_index("ix_attraction_tasks_created_at", "attraction_tasks", ["created_at"])
    op.create_index("ix_attraction_tasks_updated_at", "attraction_tasks", ["updated_at"])


def downgrade() -> None:
    op.drop_table("attraction_tasks")
    op.drop_table("support_messages")
    op.drop_table("support_threads")
    op.drop_table("agent_runs")
    op.drop_table("planning_sessions")
    op.drop_table("cache_records")
    op.drop_table("preferences")
    op.drop_table("auth_sessions")
    op.drop_table("users")
