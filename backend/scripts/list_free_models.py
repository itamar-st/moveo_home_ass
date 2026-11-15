"""Query OpenRouter /models for :free models supporting response_format.

Usage:
    python -m scripts.list_free_models
    python -m scripts.list_free_models --all-free
"""

import argparse
import asyncio
import logging
from typing import Any

import httpx

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
logger = logging.getLogger(__name__)


def filter_free_structured_models(
    models: list[dict[str, Any]],
    *,
    require_response_format: bool = True,
) -> list[dict[str, Any]]:
    """Filter OpenRouter model records to :free models supporting response_format."""
    matched: list[dict[str, Any]] = []
    for model in models:
        model_id = str(model.get("id") or "")
        if not model_id.endswith(":free"):
            continue

        supported_params = set(model.get("supported_parameters") or [])
        has_response_format = "response_format" in supported_params
        has_structured = "structured_outputs" in supported_params

        if require_response_format and not has_response_format:
            continue

        matched.append(
            {
                "id": model_id,
                "name": model.get("name") or model_id,
                "context_length": model.get("context_length"),
                "has_response_format": has_response_format,
                "has_structured_outputs": has_structured,
                "supported_parameters": sorted(supported_params),
            }
        )

    matched.sort(
        key=lambda m: (
            not m["has_structured_outputs"],
            not m["has_response_format"],
            -(m["context_length"] or 0),
            m["id"],
        )
    )
    return matched


async def fetch_openrouter_models(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Fetch the public OpenRouter model catalog."""
    if client is not None:
        resp = await client.get(OPENROUTER_MODELS_URL)
        resp.raise_for_status()
        payload = resp.json()
    else:
        async with httpx.AsyncClient(timeout=20.0) as owned_client:
            resp = await owned_client.get(OPENROUTER_MODELS_URL)
            resp.raise_for_status()
            payload = resp.json()
    return list(payload.get("data") or [])


async def main() -> None:
    parser = argparse.ArgumentParser(description="List free OpenRouter models with JSON support.")
    parser.add_argument(
        "--all-free",
        action="store_true",
        help="Include :free models even if response_format is not listed.",
    )
    args = parser.parse_args()

    models = await fetch_openrouter_models()
    filtered = filter_free_structured_models(
        models,
        require_response_format=not args.all_free,
    )

    print(f"Found {len(filtered)} matching :free models:")
    for item in filtered:
        rf = "response_format" if item["has_response_format"] else "-"
        so = "structured_outputs" if item["has_structured_outputs"] else "-"
        ctx = item["context_length"] or "?"
        print(f"  {item['id']} | ctx={ctx} | {rf} | {so}")


if __name__ == "__main__":
    asyncio.run(main())
