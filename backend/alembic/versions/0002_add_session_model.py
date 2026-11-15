"""Add model column to chat_sessions for per-session LLM selection.

Revision ID: 0002_add_session_model
Revises: 0001_initial
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_add_session_model"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable model column to chat_sessions."""
    op.add_column(
        "chat_sessions",
        sa.Column("model", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    """Remove model column from chat_sessions."""
    op.drop_column("chat_sessions", "model")
