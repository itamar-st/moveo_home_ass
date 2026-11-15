"""SQLAlchemy 2.0 ORM models mirroring the schema in AGENTS.md §5.2."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    CHAR,
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

HUB_REGIONS = (
    "Midwest",
    "Northeast",
    "Southeast",
    "South Central",
    "Mountain",
    "West",
)

ALERT_TYPES = (
    "tier_change",
    "score_delta",
    "new_severe_weather",
)

CHAT_ROLES = (
    "user",
    "assistant",
    "tool",
)

# Dialect-compatible integer types: SQLite requires INTEGER for AUTOINCREMENT,
# while MySQL uses UNSIGNED INT / BIGINT.
UIntType = Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")
UBigIntType = Integer().with_variant(mysql.BIGINT(unsigned=True), "mysql")
UTinyIntType = SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")
MediumTextType = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Hub(Base):
    """Distribution hub reference data and static FEMA NRI hazard scores."""

    __tablename__ = "hubs"
    __table_args__: ClassVar[dict[str, str]] = {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
    }

    id: Mapped[int] = mapped_column(UIntType, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    region: Mapped[str] = mapped_column(
        Enum(*HUB_REGIONS, name="hub_region"),
        nullable=False,
    )
    latitude: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    county_fips: Mapped[str] = mapped_column(CHAR(5), nullable=False)
    timezone: Mapped[str] = mapped_column(String(40), nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    nri: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    weather_daily: Mapped[list["WeatherDaily"]] = relationship(
        back_populates="hub",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    risk_snapshots: Mapped[list["RiskSnapshot"]] = relationship(
        back_populates="hub",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    risk_alerts: Mapped[list["RiskAlert"]] = relationship(
        back_populates="hub",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WeatherDaily(Base):
    """Daily observed/reanalysis weather metrics per hub."""

    __tablename__ = "weather_daily"
    __table_args__: ClassVar[dict[str, str]] = {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
    }

    hub_id: Mapped[int] = mapped_column(
        UIntType,
        ForeignKey("hubs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    temp_max_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    temp_min_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    precip_mm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    snowfall_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    gust_max_kmh: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    weather_code: Mapped[int | None] = mapped_column(UTinyIntType, nullable=True)

    hub: Mapped["Hub"] = relationship(back_populates="weather_daily")


class RiskSnapshot(Base):
    """Deterministic WDRI scoring output per hub per scoring run."""

    __tablename__ = "risk_snapshots"
    __table_args__ = (
        Index("ix_snap_hub_time", "hub_id", "computed_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
        },
    )

    id: Mapped[int] = mapped_column(UBigIntType, primary_key=True, autoincrement=True)
    hub_id: Mapped[int] = mapped_column(
        UIntType,
        ForeignKey("hubs.id", ondelete="CASCADE"),
        nullable=False,
    )
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    operational_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    hub: Mapped["Hub"] = relationship(back_populates="risk_snapshots")


class RiskAlert(Base):
    """Score or tier change alert emitted by the detector."""

    __tablename__ = "risk_alerts"
    __table_args__ = (
        Index("ix_alert_time", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
        },
    )

    id: Mapped[int] = mapped_column(UBigIntType, primary_key=True, autoincrement=True)
    hub_id: Mapped[int] = mapped_column(
        UIntType,
        ForeignKey("hubs.id", ondelete="CASCADE"),
        nullable=False,
    )
    alert_type: Mapped[str] = mapped_column(
        Enum(*ALERT_TYPES, name="risk_alert_type"),
        nullable=False,
    )
    old_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    new_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    message: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    hub: Mapped["Hub"] = relationship(back_populates="risk_alerts")


class ChatSession(Base):
    """Conversational session metadata."""

    __tablename__ = "chat_sessions"
    __table_args__: ClassVar[dict[str, str]] = {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
    }

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ChatMessage(Base):
    """Individual user, assistant, or tool message within a chat session."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_msg_session", "session_id", "id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
        },
    )

    id: Mapped[int] = mapped_column(UBigIntType, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        Enum(*CHAT_ROLES, name="chat_message_role"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
