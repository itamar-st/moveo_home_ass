"""Tests for database schema, Alembic migration, and hub seeding (M1)."""

import io
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.db.models import (
    HUB_REGIONS,
    Base,
    ChatMessage,
    ChatSession,
    Hub,
    RiskAlert,
    RiskSnapshot,
    WeatherDaily,
)
from scripts.seed import seed_hubs

BACKEND_DIR = Path(__file__).resolve().parent.parent

EXPECTED_HUB_CODES = {
    "ORD",
    "MSP",
    "DTW",
    "IND",
    "MCI",
    "STL",
    "EWR",
    "BOS",
    "MIA",
    "ATL",
    "CLT",
    "IAH",
    "DFW",
    "MSY",
    "MEM",
    "DEN",
    "SLC",
    "PHX",
    "LAX",
    "SEA",
}


@pytest.fixture
async def schema_session() -> AsyncGenerator[AsyncSession, None]:
    """Create all 6 tables in an in-memory SQLite DB with FK enforcement."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        yield session

    await engine.dispose()


async def test_all_six_tables_created(schema_session: AsyncSession) -> None:
    """Verify Base.metadata.create_all creates all 6 expected tables."""
    conn = await schema_session.connection()
    table_names = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    assert table_names == {
        "hubs",
        "weather_daily",
        "risk_snapshots",
        "risk_alerts",
        "chat_sessions",
        "chat_messages",
    }


async def test_seed_hubs_inserts_20_valid_hubs(schema_session: AsyncSession) -> None:
    """Verify seed_hubs populates all 20 hubs with valid metadata."""
    result = await seed_hubs(schema_session)
    assert result == {"inserted": 20, "updated": 0, "skipped": False}

    hubs = (await schema_session.execute(select(Hub))).scalars().all()
    assert len(hubs) == 20
    assert {h.code for h in hubs} == EXPECTED_HUB_CODES

    for hub in hubs:
        assert hub.region in HUB_REGIONS
        assert len(hub.state) == 2
        assert len(hub.county_fips) == 5 and hub.county_fips.isdigit()
        assert ZoneInfo(hub.timezone) is not None
        assert isinstance(hub.aliases, list) and len(hub.aliases) >= 2


async def test_seed_hubs_is_idempotent(schema_session: AsyncSession) -> None:
    """Running seed_hubs twice updates existing hubs without creating duplicates."""
    first = await seed_hubs(schema_session)
    assert first == {"inserted": 20, "updated": 0, "skipped": False}

    second = await seed_hubs(schema_session)
    assert second == {"inserted": 0, "updated": 20, "skipped": False}

    count = await schema_session.scalar(select(func.count()).select_from(Hub))
    assert count == 20


async def test_seed_hubs_if_empty_skips_when_populated(schema_session: AsyncSession) -> None:
    """Passing if_empty=True skips seeding when hubs already exist."""
    await seed_hubs(schema_session)
    skipped = await seed_hubs(schema_session, if_empty=True)
    assert skipped == {"inserted": 0, "updated": 0, "skipped": True}

    count = await schema_session.scalar(select(func.count()).select_from(Hub))
    assert count == 20


async def test_insert_all_related_models_and_json_columns(schema_session: AsyncSession) -> None:
    """Verify inserting WeatherDaily, RiskSnapshot, RiskAlert, ChatSession, ChatMessage."""
    await seed_hubs(schema_session)
    den = await schema_session.scalar(select(Hub).where(Hub.code == "DEN"))
    assert den is not None

    weather = WeatherDaily(
        hub_id=den.id,
        date=date(2025, 1, 15),
        temp_max_c=Decimal("2.5"),
        temp_min_c=Decimal("-8.0"),
        precip_mm=Decimal("4.20"),
        snowfall_cm=Decimal("5.50"),
        gust_max_kmh=Decimal("48.3"),
        weather_code=73,
    )
    snapshot = RiskSnapshot(
        hub_id=den.id,
        computed_at=datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC).replace(tzinfo=None),
        overall_score=Decimal("44.50"),
        operational_score=Decimal("44.50"),
        details={
            "scoring_version": "wdri-v1",
            "confidence": "high",
            "hazards": {"winter": {"score": 62.1, "contribution": 15.5}},
            "active_alerts": [],
        },
    )
    alert = RiskAlert(
        hub_id=den.id,
        alert_type="tier_change",
        old_score=Decimal("38.00"),
        new_score=Decimal("44.50"),
        message="DEN operational risk Moderate -> High",
    )

    session_id = str(uuid.uuid4())
    chat_session = ChatSession(id=session_id, title="Denver winter risk")
    chat_message = ChatMessage(
        session_id=session_id,
        role="assistant",
        content="Denver had snowfall on 12.3% of days last year.",
        payload={"confidence": "high", "artifacts": []},
    )

    schema_session.add_all([weather, snapshot, alert, chat_session, chat_message])
    await schema_session.commit()

    fetched_snap = await schema_session.scalar(
        select(RiskSnapshot).where(RiskSnapshot.hub_id == den.id)
    )
    assert fetched_snap is not None
    assert fetched_snap.details["scoring_version"] == "wdri-v1"
    assert fetched_snap.details["hazards"]["winter"]["score"] == 62.1

    fetched_msg = await schema_session.scalar(
        select(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    assert fetched_msg is not None
    assert fetched_msg.payload == {"confidence": "high", "artifacts": []}

    # Verify ON DELETE CASCADE from chat_sessions -> chat_messages
    await schema_session.delete(chat_session)
    await schema_session.commit()
    remaining_msgs = await schema_session.scalar(select(func.count()).select_from(ChatMessage))
    assert remaining_msgs == 0


def test_alembic_initial_migration_loads_and_generates_sql() -> None:
    """Verify Alembic ScriptDirectory loads 0001_initial and emits offline SQL."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    assert head == "0002_add_session_model"

    buf = io.StringIO()
    cfg.output_buffer = buf
    command.upgrade(cfg, "head", sql=True)
    sql_output = buf.getvalue()

    assert "ADD COLUMN model" in sql_output

    for table in (
        "hubs",
        "weather_daily",
        "risk_snapshots",
        "risk_alerts",
        "chat_sessions",
        "chat_messages",
    ):
        assert f"CREATE TABLE {table}" in sql_output
    assert "ix_snap_hub_time" in sql_output
    assert "ix_alert_time" in sql_output
    assert "ix_msg_session" in sql_output
