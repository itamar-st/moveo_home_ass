"""Initial schema for Weather Risk Intelligence Agent.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UIntType = sa.Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")
UBigIntType = sa.Integer().with_variant(mysql.BIGINT(unsigned=True), "mysql")
UTinyIntType = sa.SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")
MediumTextType = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


def upgrade() -> None:
    """Create all 6 core tables, indexes, and foreign keys."""
    op.create_table(
        "hubs",
        sa.Column("id", UIntType, autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("state", sa.CHAR(length=2), nullable=False),
        sa.Column(
            "region",
            sa.Enum(
                "Midwest",
                "Northeast",
                "Southeast",
                "South Central",
                "Mountain",
                "West",
                name="hub_region",
            ),
            nullable=False,
        ),
        sa.Column("latitude", sa.Numeric(precision=8, scale=5), nullable=False),
        sa.Column("longitude", sa.Numeric(precision=8, scale=5), nullable=False),
        sa.Column("county_fips", sa.CHAR(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=40), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("nri", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    op.create_table(
        "weather_daily",
        sa.Column("hub_id", UIntType, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("temp_max_c", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("temp_min_c", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("precip_mm", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("snowfall_cm", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("gust_max_kmh", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("weather_code", UTinyIntType, nullable=True),
        sa.ForeignKeyConstraint(["hub_id"], ["hubs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("hub_id", "date"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    op.create_table(
        "risk_snapshots",
        sa.Column("id", UBigIntType, autoincrement=True, nullable=False),
        sa.Column("hub_id", UIntType, nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("overall_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("operational_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["hub_id"], ["hubs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_snap_hub_time", "risk_snapshots", ["hub_id", "computed_at"], unique=False)

    op.create_table(
        "risk_alerts",
        sa.Column("id", UBigIntType, autoincrement=True, nullable=False),
        sa.Column("hub_id", UIntType, nullable=False),
        sa.Column(
            "alert_type",
            sa.Enum(
                "tier_change",
                "score_delta",
                "new_severe_weather",
                name="risk_alert_type",
            ),
            nullable=False,
        ),
        sa.Column("old_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("new_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("message", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["hub_id"], ["hubs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_alert_time", "risk_alerts", ["created_at"], unique=False)

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", UBigIntType, autoincrement=True, nullable=False),
        sa.Column("session_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "role",
            sa.Enum("user", "assistant", "tool", name="chat_message_role"),
            nullable=False,
        ),
        sa.Column("content", MediumTextType, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_msg_session", "chat_messages", ["session_id", "id"], unique=False)


def downgrade() -> None:
    """Drop all 6 core tables in reverse dependency order."""
    op.drop_index("ix_msg_session", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_index("ix_alert_time", table_name="risk_alerts")
    op.drop_table("risk_alerts")
    op.drop_index("ix_snap_hub_time", table_name="risk_snapshots")
    op.drop_table("risk_snapshots")
    op.drop_table("weather_daily")
    op.drop_table("hubs")
