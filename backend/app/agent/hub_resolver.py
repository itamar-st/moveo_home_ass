"""Resolve user hub references (codes, city names, aliases) against the hub roster."""

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub


def normalize_hub_query(raw: str) -> str:
    """Normalize a hub reference for matching (strip filler words & punctuation)."""
    text = raw.strip().lower()
    text = re.sub(r"^(?:the)\s+", "", text)
    text = re.sub(r"\s+(?:distribution\s+hub|distribution\s+center|hub|facility)$", "", text)
    text = re.sub(r"[.,'\-/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass(frozen=True)
class HubResolution:
    """Result of resolving a single hub reference."""

    matched: bool
    query: str
    hub: Hub | None = None
    suggestions: list[str] = ()  # type: ignore[assignment]


class HubResolver:
    """In-memory matcher constructed from a sequence of Hub ORM rows."""

    def __init__(self, hubs: Sequence[Hub]) -> None:
        self._hubs = list(hubs)
        self._exact_map: dict[str, Hub] = {}
        self._label_to_hub: dict[str, Hub] = {}

        for hub in self._hubs:
            keys: list[str] = [
                hub.code,
                hub.city,
                f"{hub.city} {hub.state}",
                f"{hub.city}, {hub.state}",
            ]
            for alias in hub.aliases or []:
                keys.append(str(alias))

            for raw_key in keys:
                norm = normalize_hub_query(raw_key)
                if norm and norm not in self._exact_map:
                    self._exact_map[norm] = hub
                if norm:
                    self._label_to_hub[norm] = hub

    @property
    def hubs(self) -> list[Hub]:
        """Return all known hubs."""
        return list(self._hubs)

    def resolve(self, query: str) -> HubResolution:
        """Resolve a hub code, city name, or alias; return suggestions when unknown."""
        norm = normalize_hub_query(query)
        if not norm:
            return HubResolution(
                matched=False,
                query=query,
                hub=None,
                suggestions=self._default_suggestions(),
            )

        hub = self._exact_map.get(norm)
        if hub is not None:
            return HubResolution(matched=True, query=query, hub=hub, suggestions=[])

        # Fallback: try matching city + state prefix when user writes e.g. "chicago illinois"
        close_keys = difflib.get_close_matches(norm, self._label_to_hub.keys(), n=5, cutoff=0.6)
        suggestions: list[str] = []
        seen_codes: set[str] = set()
        for key in close_keys:
            candidate = self._label_to_hub[key]
            if candidate.code not in seen_codes:
                seen_codes.add(candidate.code)
                suggestions.append(f"{candidate.city} ({candidate.code})")
            if len(suggestions) >= 3:
                break

        if not suggestions:
            suggestions = self._default_suggestions()

        return HubResolution(
            matched=False,
            query=query,
            hub=None,
            suggestions=suggestions,
        )

    def _default_suggestions(self) -> list[str]:
        """Return a small sample of available hubs when no close match exists."""
        return [f"{h.city} ({h.code})" for h in self._hubs[:5]]


async def load_hub_resolver(db: AsyncSession) -> HubResolver:
    """Load all hubs from DB and return a HubResolver."""
    result = await db.execute(select(Hub).order_by(Hub.code))
    hubs = result.scalars().all()
    return HubResolver(hubs)


def format_unknown_hub_message(query: str, suggestions: Sequence[str], available_count: int) -> dict[str, Any]:
    """Build structured error summary and payload for an unknown hub."""
    sugg_text = ", ".join(suggestions) if suggestions else "none"
    return {
        "summary": (
            f"Unknown hub '{query}'. It is not one of the {available_count} portfolio hubs. "
            f"Closest/sample hubs: {sugg_text}."
        ),
        "data": {
            "unknown_hub": query,
            "suggestions": list(suggestions),
            "available_hub_count": available_count,
        },
    }
