"""Full-stack integration tests across Frontend (React/Vite), Backend (FastAPI/SSE), and Agent Loop."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.llm_client import LLMResponse, LLMUnavailableError
from app.api.deps import SlidingWindowRateLimiter, get_db
from app.api.routes_chat import get_llm_client
from app.config import get_settings
from app.db.models import Base, Hub, WeatherDaily
from app.main import create_app
from app.scoring.runner import rescore_all_hubs
from scripts.seed import seed_hubs

playwright_sync = pytest.importorskip("playwright.sync_api")

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http_ok(url: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if 200 <= resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_err}")


@dataclass
class IntegrationScriptedLLM:
    """Deterministic scripted LLM that records received histories and exercises agent flows."""

    recorded_calls: list[list[dict[str, str]]] = field(default_factory=list)
    fail_with_unavailable: bool = False
    _repair_injected_for_q1: bool = False

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        _ = response_format
        self.recorded_calls.append(list(messages))

        if self.fail_with_unavailable:
            raise LLMUnavailableError("Simulated OpenRouter 429 rate limit")

        last_msg = messages[-1]["content"]
        user_turns = [m["content"] for m in messages if m["role"] == "user"]
        latest_user_prompt = next(
            (c for c in reversed(user_turns) if not c.startswith("OBSERVATION:") and "invalid AgentStep JSON" not in c),
            "",
        )

        # Handle schema-repair retry turn
        if "invalid AgentStep JSON" in last_msg:
            return LLMResponse(
                content=json.dumps(
                    {
                        "rationale": "Repaired step: call rank_hubs for Midwest winter risk.",
                        "action": "call_tool",
                        "tool_call": {
                            "name": "rank_hubs",
                            "arguments": {"hazard": "winter", "region": "Midwest", "top_n": 5},
                        },
                    }
                ),
                model="nvidia/nemotron-3.5-super-vl:free",
            )

        # Tool observation turn -> produce grounded final_answer
        if last_msg.startswith("OBSERVATION:"):
            obs_payload = json.loads(last_msg[len("OBSERVATION:") :].strip())
            tool_name = obs_payload.get("tool") or obs_payload.get("tool_name")

            if tool_name == "rank_hubs":
                items = obs_payload["data"]["items"]
                top = items[0]
                return LLMResponse(
                    content=json.dumps(
                        {
                            "rationale": "Summarize deterministic Midwest winter ranking.",
                            "action": "final_answer",
                            "final_answer": {
                                "answer_markdown": (
                                    f"**{top['city']} ({top['code']})** ranks #1 in Midwest winter "
                                    f"exposure with a score of **{top['score']}** ({top['tier']})."
                                ),
                                "intent": "ranking",
                                "hubs": [item["code"] for item in items[:3]],
                                "key_points": [
                                    f"#{item['rank']} {item['city']} ({item['code']}): {item['score']} ({item['tier']})"
                                    for item in items[:3]
                                ],
                                "reasoning": [
                                    "Ranked Midwest hubs deterministically using WDRI v1 winter scores."
                                ],
                                "assumptions": [
                                    "Structural WDRI v1 weights and 5-year climatology window applied."
                                ],
                                "confidence": "high",
                                "confidence_rationale": "All Midwest hubs have complete snapshots.",
                                "follow_ups": ["What about Chicago specifically?"],
                            },
                        }
                    ),
                    model="nvidia/nemotron-3.5-super-vl:free",
                )

            if tool_name == "explain_hub_risk":
                d = obs_payload["data"]
                return LLMResponse(
                    content=json.dumps(
                        {
                            "rationale": "Deliver deterministic hub breakdown.",
                            "action": "final_answer",
                            "final_answer": {
                                "answer_markdown": (
                                    f"**{d['city']} ({d['code']})** has an overall structural WDRI "
                                    f"of **{d['overall_score']}** ({d['overall_tier']}), ranking "
                                    f"#{d['rank']} of {d['total_hubs']} hubs."
                                ),
                                "intent": "explanation",
                                "hubs": [d["code"]],
                                "key_points": [
                                    f"{d['code']} overall score: {d['overall_score']} ({d['overall_tier']})."
                                ],
                                "reasoning": [
                                    "Inspected per-hazard contributions and top drivers via explain_hub_risk."
                                ],
                                "assumptions": ["County-level FEMA NRI percentiles combined with ERA5 metrics."],
                                "confidence": "high",
                                "confidence_rationale": "Complete hub snapshot available.",
                                "follow_ups": [],
                            },
                        }
                    ),
                    model="nvidia/nemotron-3.5-super-vl:free",
                )

            if tool_name == "compare_hubs":
                hubs = obs_payload["data"]["hubs"]
                codes = [h["code"] for h in hubs]
                hub_labels = " and ".join(f"{h['city']} ({h['code']})" for h in hubs)
                return LLMResponse(
                    content=json.dumps(
                        {
                            "rationale": "Present deterministic multi-hub comparison.",
                            "action": "final_answer",
                            "final_answer": {
                                "answer_markdown": (
                                    f"Compared **{hub_labels}** "
                                    "across hurricane and flood exposure."
                                ),
                                "intent": "comparison",
                                "hubs": codes,
                                "key_points": [
                                    f"{h['code']}: hurricane {h['hazard_scores'].get('hurricane')}, flood {h['hazard_scores'].get('flood')}"
                                    for h in hubs
                                ],
                                "reasoning": ["Used deterministic compare_hubs tool."],
                                "assumptions": ["Structural hazard scores (0-100) compared."],
                                "confidence": "high",
                                "confidence_rationale": "Both hubs scored under WDRI v1.",
                                "follow_ups": [],
                            },
                        }
                    ),
                    model="nvidia/nemotron-3.5-super-vl:free",
                )

            if tool_name == "get_weather_stats":
                d = obs_payload["data"]
                return LLMResponse(
                    content=json.dumps(
                        {
                            "rationale": "Report deterministic weather statistic.",
                            "action": "final_answer",
                            "final_answer": {
                                "answer_markdown": (
                                    f"**{d['city']} ({d['code']})** recorded snowfall on "
                                    f"**{d['percentage']}%** of days ({d['days_matching']} of {d['days_total']} days) last year."
                                ),
                                "intent": "statistic",
                                "hubs": [d["code"]],
                                "key_points": [
                                    f"{d['days_matching']} / {d['days_total']} days ({d['percentage']}%) met {d['threshold']}."
                                ],
                                "reasoning": ["Computed from weather_daily via get_weather_stats."],
                                "assumptions": ["Last year = previous full calendar year (2025)."],
                                "confidence": "high",
                                "confidence_rationale": f"{d['coverage_pct']}% daily coverage.",
                                "follow_ups": [],
                            },
                        }
                    ),
                    model="nvidia/nemotron-3.5-super-vl:free",
                )

        # First step of a turn: route by user prompt
        prompt_lower = latest_user_prompt.lower()
        if "midwest" in prompt_lower and "winter" in prompt_lower:
            if not self._repair_injected_for_q1:
                self._repair_injected_for_q1 = True
                # Deliberately return malformed JSON once to verify agent repair loop + status event!
                return LLMResponse(content="not-valid-json", model="nvidia/nemotron-3.5-super-vl:free")
            return LLMResponse(
                content=json.dumps(
                    {
                        "rationale": "Rank Midwest hubs by winter disruption.",
                        "action": "call_tool",
                        "tool_call": {
                            "name": "rank_hubs",
                            "arguments": {"hazard": "winter", "region": "Midwest", "top_n": 5},
                        },
                    }
                ),
                model="nvidia/nemotron-3.5-super-vl:free",
            )

        if "chicago" in prompt_lower:
            return LLMResponse(
                content=json.dumps(
                    {
                        "rationale": "Explain Chicago (ORD) risk drivers.",
                        "action": "call_tool",
                        "tool_call": {
                            "name": "explain_hub_risk",
                            "arguments": {"hub": "Chicago"},
                        },
                    }
                ),
                model="nvidia/nemotron-3.5-super-vl:free",
            )

        if "miami" in prompt_lower and "houston" in prompt_lower:
            return LLMResponse(
                content=json.dumps(
                    {
                        "rationale": "Compare Miami and Houston on hurricane and flood.",
                        "action": "call_tool",
                        "tool_call": {
                            "name": "compare_hubs",
                            "arguments": {"hubs": ["Miami", "Houston"], "hazards": ["hurricane", "flood"]},
                        },
                    }
                ),
                model="nvidia/nemotron-3.5-super-vl:free",
            )

        if "denver" in prompt_lower and "snow" in prompt_lower:
            return LLMResponse(
                content=json.dumps(
                    {
                        "rationale": "Compute Denver snowfall days percentage for last year.",
                        "action": "call_tool",
                        "tool_call": {
                            "name": "get_weather_stats",
                            "arguments": {
                                "hub": "Denver",
                                "metric": "snowfall_days",
                                "period": "last_year",
                            },
                        },
                    }
                ),
                model="nvidia/nemotron-3.5-super-vl:free",
            )

        return LLMResponse(
            content=json.dumps(
                {
                    "rationale": "General portfolio acknowledgement.",
                    "action": "final_answer",
                    "final_answer": {
                        "answer_markdown": "Portfolio ready across 20 US logistics hubs.",
                        "intent": "general",
                        "hubs": [],
                        "key_points": ["All 20 US hubs are scored under WDRI v1."],
                        "reasoning": ["General portfolio overview."],
                        "assumptions": [],
                        "confidence": "high",
                        "confidence_rationale": "Deterministic scores available.",
                        "follow_ups": [],
                    },
                }
            ),
            model="nvidia/nemotron-3.5-super-vl:free",
        )


def _seed_integration_weather(hubs_by_code: dict[str, Hub]) -> list[WeatherDaily]:
    """Create deterministic 2025 daily weather rows for key hubs."""
    rows: list[WeatherDaily] = []
    start_2025 = date(2025, 1, 1)
    for day_idx in range(365):
        cur = start_2025 + timedelta(days=day_idx)
        rows.append(
            WeatherDaily(
                hub_id=hubs_by_code["MSP"].id,
                date=cur,
                temp_max_c=Decimal("10.0"),
                temp_min_c=Decimal("-15.0") if day_idx < 25 else Decimal("0.0"),
                precip_mm=Decimal("1.0"),
                snowfall_cm=Decimal("3.5") if day_idx < 15 else Decimal("0.0"),
                gust_max_kmh=Decimal("30.0"),
                weather_code=73 if day_idx < 15 else 1,
            )
        )
        rows.append(
            WeatherDaily(
                hub_id=hubs_by_code["ORD"].id,
                date=cur,
                temp_max_c=Decimal("12.0"),
                temp_min_c=Decimal("-12.0") if day_idx < 12 else Decimal("2.0"),
                precip_mm=Decimal("2.0"),
                snowfall_cm=Decimal("3.0") if day_idx < 10 else Decimal("0.0"),
                gust_max_kmh=Decimal("32.0"),
                weather_code=71 if day_idx < 10 else 1,
            )
        )
        rows.append(
            WeatherDaily(
                hub_id=hubs_by_code["DTW"].id,
                date=cur,
                temp_max_c=Decimal("11.0"),
                temp_min_c=Decimal("-11.0") if day_idx < 8 else Decimal("1.0"),
                precip_mm=Decimal("1.5"),
                snowfall_cm=Decimal("2.8") if day_idx < 8 else Decimal("0.0"),
                gust_max_kmh=Decimal("28.0"),
                weather_code=71 if day_idx < 8 else 1,
            )
        )
        rows.append(
            WeatherDaily(
                hub_id=hubs_by_code["DEN"].id,
                date=cur,
                temp_max_c=Decimal("18.0"),
                temp_min_c=Decimal("-4.0"),
                precip_mm=Decimal("1.0"),
                snowfall_cm=Decimal("0.5") if day_idx < 73 else Decimal("0.0"),
                gust_max_kmh=Decimal("30.0"),
                weather_code=71 if day_idx < 73 else 1,
            )
        )
        rows.append(
            WeatherDaily(
                hub_id=hubs_by_code["MIA"].id,
                date=cur,
                temp_max_c=Decimal("31.0"),
                temp_min_c=Decimal("22.0"),
                precip_mm=Decimal("30.0") if day_idx < 12 else Decimal("1.0"),
                snowfall_cm=Decimal("0.0"),
                gust_max_kmh=Decimal("85.0") if day_idx < 2 else Decimal("25.0"),
                weather_code=63 if day_idx < 12 else 1,
            )
        )
        rows.append(
            WeatherDaily(
                hub_id=hubs_by_code["IAH"].id,
                date=cur,
                temp_max_c=Decimal("33.0"),
                temp_min_c=Decimal("20.0"),
                precip_mm=Decimal("28.0") if day_idx < 14 else Decimal("1.0"),
                snowfall_cm=Decimal("0.0"),
                gust_max_kmh=Decimal("82.0") if day_idx < 1 else Decimal("24.0"),
                weather_code=63 if day_idx < 14 else 1,
            )
        )
    return rows


@dataclass
class FullStackServer:
    frontend_url: str
    backend_url: str
    llm: IntegrationScriptedLLM
    app: Any


@pytest.fixture
def fullstack_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FullStackServer]:
    """Boot real FastAPI (uvicorn) + seeded DB + real Vite frontend dev server."""
    if not (FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js").exists():
        pytest.skip("frontend/node_modules not installed; run `npm install` in frontend/")

    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node executable not found on PATH")

    api_port = _free_tcp_port()
    ui_port = _free_tcp_port()
    frontend_url = f"http://127.0.0.1:{ui_port}"
    backend_url = f"http://127.0.0.1:{api_port}"

    monkeypatch.setenv("CORS_ORIGINS", f"{frontend_url},http://localhost:{ui_port}")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_MIN", "50")
    get_settings.cache_clear()

    db_path = tmp_path / "fullstack_integration.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        echo=False,
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _prepare_db() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            await seed_hubs(session)
            hubs = {h.code: h for h in (await session.execute(select(Hub))).scalars().all()}
            session.add_all(_seed_integration_weather(hubs))
            await session.commit()
            await rescore_all_hubs(
                session,
                fetch_nws=False,
                computed_at=datetime(2026, 11, 15, 18, 0, 0),
                reference_date=date(2026, 11, 15),
            )

    asyncio.run(_prepare_db())

    app = create_app()
    scripted_llm = IntegrationScriptedLLM()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_client] = lambda: scripted_llm

    import uvicorn

    uv_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=api_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uv_config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    _wait_http_ok(f"{backend_url}/api/health", timeout_s=15.0)

    vite_env = os.environ.copy()
    vite_env["VITE_API_BASE_URL"] = backend_url
    vite_proc = subprocess.Popen(
        [
            node_exe,
            str(FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            str(ui_port),
            "--strictPort",
        ],
        cwd=str(FRONTEND_DIR),
        env=vite_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_http_ok(frontend_url, timeout_s=20.0)
        yield FullStackServer(
            frontend_url=frontend_url,
            backend_url=backend_url,
            llm=scripted_llm,
            app=app,
        )
    finally:
        vite_proc.terminate()
        try:
            vite_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            vite_proc.kill()
        server.should_exit = True
        server_thread.join(timeout=5)
        asyncio.run(engine.dispose())
        get_settings.cache_clear()


def _launch_browser(pw: Any) -> Any:
    last_exc: Exception | None = None
    for channel in ("chrome", "msedge", None):
        try:
            kwargs: dict[str, Any] = {"headless": True}
            if channel is not None:
                kwargs["channel"] = channel
            return pw.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    pytest.skip(f"No Chromium/Chrome/Edge browser binary available for Playwright: {last_exc}")


def test_frontend_backend_agent_end_to_end(fullstack_env: FullStackServer) -> None:
    """Verify full browser <-> FastAPI SSE/REST <-> Agent loop + DB integration."""
    with playwright_sync.sync_playwright() as pw:
        browser = _launch_browser(pw)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page_errors: list[str] = []
        console_errors: list[str] = []
        failed_requests: list[str] = []
        server_5xx_urls: list[str] = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on(
            "console",
            lambda msg: console_errors.append(f"{msg.type}: {msg.text}")
            if msg.type in {"error", "warning"}
            else None,
        )
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(f"{req.method} {req.url}: {req.failure}"),
        )
        page.on(
            "response",
            lambda resp: server_5xx_urls.append(f"{resp.status} {resp.url}")
            if resp.status >= 500
            else None,
        )

        try:
            page.goto(fullstack_env.frontend_url, wait_until="networkidle")

            # 1. Health & HubSidebar hydration
            alerts_panel = page.locator(".alerts-panel")
            alerts_panel.get_by_text("Online").wait_for(timeout=10000)
            alerts_panel.get_by_text("Connected").wait_for(timeout=10000)

            sidebar = page.locator(".sidebar-panel")
            sidebar.locator(".hub-card").first.wait_for(timeout=10000)
            assert sidebar.locator(".hub-card").count() == 20

            # Filter sidebar by Midwest + Winter and inspect ORD detail card
            sidebar.locator("select").select_option("Midwest")
            sidebar.get_by_role("button", name="Winter", exact=True).click()
            sidebar.locator(".hub-card", has_text="MSP").wait_for(timeout=5000)
            assert sidebar.locator(".hub-card").count() == 6

            sidebar.locator(".hub-card", has_text="ORD").click()
            sidebar.locator(".hub-detail-card").get_by_text("Chicago (ORD)").wait_for(timeout=5000)
            sidebar.get_by_role("button", name="Explain ORD in Chat").wait_for(timeout=5000)

            # Open & close Methodology modal (/api/meta/scoring)
            page.get_by_role("button", name="Methodology").click()
            modal = page.locator(".modal-card")
            modal.get_by_text("WDRI Scoring Methodology").wait_for(timeout=5000)
            modal.get_by_text("Composite Formula").wait_for(timeout=5000)
            modal.get_by_label("Close methodology modal").click()
            modal.wait_for(state="hidden")

            # 2. Q1 Starter Question -> Repair loop -> rank_hubs SSE -> RankingTable + ToolTrace
            page.locator(".starter-card", has_text="Winter Ranking").click()

            page.locator(".artifact-card", has_text="MSP").wait_for(timeout=15000)
            ranking_card = page.locator(".artifact-card").first
            assert "Minneapolis" in ranking_card.inner_text()
            assert "Chicago" in ranking_card.inner_text()
            assert "Detroit" in ranking_card.inner_text()

            # Verify session persisted in localStorage
            session_id = page.evaluate("window.localStorage.getItem('wria_session_id')")
            assert session_id and len(session_id) >= 32

            # Expand ToolTrace & AssumptionsBox
            first_assistant = page.locator(".assistant-bubble").last
            first_assistant.locator(".tool-trace-toggle").click()
            first_assistant.locator(".tool-badge", has_text="rank_hubs").wait_for(timeout=5000)
            first_assistant.locator(".assumptions-toggle").click()
            first_assistant.locator(".assumptions-list").wait_for(timeout=5000)

            # 3. Follow-up chip in same session -> verifies multi-turn history replay & ScoreBreakdown
            calls_before_followup = len(fullstack_env.llm.recorded_calls)
            first_assistant.locator(".followup-chip", has_text="What about Chicago specifically?").click()
            page.locator(".artifact-card", has_text="Structural WDRI").wait_for(timeout=15000)

            # Confirm the LLM actually received the Turn 1 user + compact tool + assistant history
            followup_prompt_msgs = fullstack_env.llm.recorded_calls[calls_before_followup]
            joined_history = "\n".join(m["content"] for m in followup_prompt_msgs)
            assert "Which hubs in the Midwest are most exposed to winter disruption?" in joined_history
            assert "[tool rank_hubs" in joined_history

            # 4. Q2 Comparison & Q3 Stat via composer input
            composer = page.locator("textarea")
            composer.fill("Compare Miami and Houston in terms of hurricane and flood exposure.")
            page.locator("button.send-btn").click()
            page.locator(".artifact-card", has_text="Miami (MIA)").wait_for(timeout=15000)
            page.locator(".artifact-card", has_text="Houston (IAH)").wait_for(timeout=15000)

            composer.fill("What percentage of days in Denver last year had snowfall?")
            page.locator("button.send-btn").click()
            page.locator(".stat-card").get_by_text("20.00%").wait_for(timeout=15000)
            page.locator(".stat-card").get_by_text("73 / 365").wait_for(timeout=5000)

            # 5. Reload browser -> verify DB session hydration restores all 4 turns & artifacts
            page.reload(wait_until="networkidle")
            page.locator(".user-bubble").nth(3).wait_for(timeout=10000)
            assert page.locator(".user-bubble").count() == 4
            assert page.locator(".assistant-bubble").count() == 4
            assert page.locator(".stat-card").get_by_text("20.00%").is_visible()

            # 6. New Chat clears localStorage & restores starter hero
            page.get_by_role("button", name="New Chat").click()
            page.locator(".empty-hero").wait_for(timeout=5000)
            assert page.evaluate("window.localStorage.getItem('wria_session_id')") is None

            # 7. LLM unavailable -> Deterministic fallback banner in UI
            fullstack_env.llm.fail_with_unavailable = True
            composer.fill("Give me a portfolio summary.")
            page.locator("button.send-btn").click()
            page.locator(".fallback-banner").wait_for(timeout=15000)
            page.locator(".error-banner").wait_for(timeout=5000)
            fullstack_env.llm.fail_with_unavailable = False

            # 8. Rate-limit (429) enforcement surfaced cleanly in UI
            fullstack_env.app.state.chat_rate_limiter = SlidingWindowRateLimiter(
                limit=1, window_seconds=60.0
            )
            composer.fill("First allowed turn.")
            page.locator("button.send-btn").click()
            page.locator(".assistant-bubble", has_text="Portfolio ready").wait_for(timeout=15000)

            composer.fill("Second turn should hit 429.")
            page.locator("button.send-btn").click()
            page.locator(".rate-limit-banner").wait_for(timeout=10000)
            assert "Rate limit reached" in page.locator(".rate-limit-banner").inner_text()

            assert page_errors == [], f"Uncaught browser page errors: {page_errors}"
            assert server_5xx_urls == [], f"Unexpected 5xx responses: {server_5xx_urls}"
        except Exception:
            print("DIAGNOSTIC console_errors:", console_errors)
            print("DIAGNOSTIC failed_requests:", failed_requests)
            print("DIAGNOSTIC llm_calls:", len(fullstack_env.llm.recorded_calls))
            print("DIAGNOSTIC chat_area:", page.locator(".chat-scroll-area").inner_text()[:1500])
            raise
        finally:
            context.close()
            browser.close()
