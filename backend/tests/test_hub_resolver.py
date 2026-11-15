"""Tests for HubResolver across codes, city names, aliases, and unknown cities."""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.hub_resolver import HubResolver
from app.db.models import Base, Hub
from scripts.seed import seed_hubs


@pytest.fixture
async def seeded_resolver() -> AsyncGenerator[HubResolver, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await seed_hubs(session)
        hubs = (await session.execute(select(Hub).order_by(Hub.code))).scalars().all()
        yield HubResolver(hubs)

    await engine.dispose()


@pytest.mark.parametrize(
    ("query", "expected_code"),
    [
        ("Dallas", "DFW"),
        ("DFW", "DFW"),
        ("dfw", "DFW"),
        ("dallas fort worth", "DFW"),
        ("Dallas-Fort Worth", "DFW"),
        ("the Houston hub", "IAH"),
        ("St. Louis", "STL"),
        ("st louis", "STL"),
        ("O'Hare", "ORD"),
        ("Chicago", "ORD"),
        ("Twin Cities", "MSP"),
        ("Denver, CO", "DEN"),
        ("nola", "MSY"),
        ("New York Metro", "EWR"),
    ],
)
def test_hub_resolver_matches_known_variants(
    seeded_resolver: HubResolver,
    query: str,
    expected_code: str,
) -> None:
    res = seeded_resolver.resolve(query)
    assert res.matched is True
    assert res.hub is not None
    assert res.hub.code == expected_code
    assert res.suggestions == []


@pytest.mark.parametrize("unknown_query", ["Boise", "Paris", "Austin", ""])
def test_hub_resolver_rejects_unknown_cities_with_suggestions(
    seeded_resolver: HubResolver,
    unknown_query: str,
) -> None:
    res = seeded_resolver.resolve(unknown_query)
    assert res.matched is False
    assert res.hub is None
    assert len(res.suggestions) >= 1
