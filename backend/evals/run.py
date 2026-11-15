"""CLI evaluation runner for Weather Risk Intelligence Agent (AGENTS.md §9.2)."""

import argparse
import asyncio
import json
import logging
import math
import statistics
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm_client import OpenRouterLLMClient
from app.agent.loop import run_agent
from app.config import get_settings
from app.db.session import get_session_maker
from evals.graders import CaseEvalResult, grade_case

logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).resolve().parent
CASES_PATH = EVALS_DIR / "cases.yaml"
RESULTS_DIR = EVALS_DIR / "results"


def load_cases(only_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Load evaluation cases from cases.yaml, optionally filtering by case ID."""
    with CASES_PATH.open("r", encoding="utf-8") as f:
        cases: list[dict[str, Any]] = yaml.safe_load(f) or []
    if only_ids:
        cases = [c for c in cases if c.get("id") in only_ids]
    return cases


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]


async def _run_case_in_process(
    db: AsyncSession,
    llm_client: OpenRouterLLMClient,
    case: dict[str, Any],
    *,
    model: str | None,
) -> CaseEvalResult:
    turns: list[str] = (
        list(case["conversation"])
        if "conversation" in case
        else [str(case["question"])]
    )

    session_id: str | None = None
    last_final: dict[str, Any] | None = None
    last_tool_trace: list[dict[str, Any]] = []
    last_artifacts: list[dict[str, Any]] = []
    last_warnings: list[str] = []
    last_model = model or get_settings().openrouter_model
    total_latency_ms = 0
    total_repairs = 0
    used_fallback = False

    for turn_msg in turns:
        turn_tool_calls: dict[str, dict[str, Any]] = {}
        turn_tool_trace: list[dict[str, Any]] = []
        turn_repairs = 0
        turn_fallback = False
        turn_final: dict[str, Any] | None = None
        turn_artifacts: list[dict[str, Any]] = []
        turn_warnings: list[str] = []
        turn_latency = 0

        async for ev in run_agent(
            db,
            llm_client,
            session_id=session_id,
            user_message=turn_msg,
            model=model,
        ):
            if ev.event == "session":
                session_id = str(ev.data["session_id"])
            elif ev.event == "status":
                stage = ev.data.get("stage")
                if stage == "repairing_output":
                    turn_repairs += 1
                elif stage == "fallback":
                    turn_fallback = True
            elif ev.event == "tool_call":
                call_id = str(ev.data.get("call_id", ""))
                if call_id:
                    turn_tool_calls[call_id] = ev.data
            elif ev.event == "tool_result":
                merged = dict(ev.data)
                call_id = str(ev.data.get("call_id", ""))
                if call_id in turn_tool_calls and "arguments" not in merged:
                    merged["arguments"] = turn_tool_calls[call_id].get("arguments") or {}
                turn_tool_trace.append(merged)
            elif ev.event == "final":
                turn_final = ev.data.get("answer")
                turn_artifacts = ev.data.get("artifacts") or []
                turn_warnings = ev.data.get("warnings") or []
                last_model = str(ev.data.get("model") or last_model)
                turn_latency = int(ev.data.get("latency_ms") or 0)

        last_final = turn_final
        last_tool_trace = turn_tool_trace
        last_artifacts = turn_artifacts
        last_warnings = turn_warnings
        total_latency_ms += turn_latency
        total_repairs += turn_repairs
        used_fallback = turn_fallback

    return await grade_case(
        db,
        case,
        raw_answer=last_final,
        tool_trace=last_tool_trace,
        artifacts=last_artifacts,
        warnings=last_warnings,
        schema_repairs=total_repairs,
        used_fallback=used_fallback,
        model=last_model,
        latency_ms=total_latency_ms,
    )


async def _run_case_http(
    db: AsyncSession,
    client: httpx.AsyncClient,
    api_url: str,
    case: dict[str, Any],
    *,
    model: str | None,
) -> CaseEvalResult:
    turns: list[str] = (
        list(case["conversation"])
        if "conversation" in case
        else [str(case["question"])]
    )

    base = api_url.rstrip("/")
    sess_payload = {"model": model} if model else {}
    sess_resp = await client.post(f"{base}/api/chat/sessions", json=sess_payload)
    sess_resp.raise_for_status()
    session_id = sess_resp.json()["session_id"]

    last_data: dict[str, Any] = {}
    total_latency_ms = 0
    for turn_msg in turns:
        body: dict[str, Any] = {"session_id": session_id, "message": turn_msg}
        if model:
            body["model"] = model
        resp = await client.post(f"{base}/api/chat", json=body)
        resp.raise_for_status()
        last_data = resp.json()
        total_latency_ms += int(last_data.get("latency_ms") or 0)

    answer = last_data.get("answer") or {}
    used_fallback = "fallback" in str(answer.get("confidence_rationale", "")).lower()

    return await grade_case(
        db,
        case,
        raw_answer=answer,
        tool_trace=last_data.get("tool_trace") or [],
        artifacts=last_data.get("artifacts") or [],
        warnings=last_data.get("warnings") or [],
        schema_repairs=0,
        used_fallback=used_fallback,
        model=str(last_data.get("model") or model or ""),
        latency_ms=total_latency_ms,
    )


def build_summary(results: list[CaseEvalResult], *, requested_model: str) -> dict[str, Any]:
    """Aggregate pass rates, grader breakdown, schema stats, and latency percentiles."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    schema_valid = sum(1 for r in results if r.schema_valid)
    fallbacks = sum(1 for r in results if r.used_fallback)
    repairs = sum(r.schema_repairs for r in results)
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]

    grader_counts: dict[str, dict[str, int]] = {}
    for res in results:
        for g in res.graders:
            bucket = grader_counts.setdefault(g.name, {"passed": 0, "total": 0})
            bucket["total"] += 1
            if g.passed:
                bucket["passed"] += 1

    per_grader = {
        name: {
            "passed": counts["passed"],
            "total": counts["total"],
            "pass_rate": round(100.0 * counts["passed"] / counts["total"], 1),
        }
        for name, counts in sorted(grader_counts.items())
    }

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "requested_model": requested_model,
        "observed_models": sorted({r.model for r in results if r.model}),
        "total_cases": total,
        "passed_cases": passed,
        "pass_rate_pct": round(100.0 * passed / total, 1) if total else 0.0,
        "schema_valid_cases": schema_valid,
        "schema_valid_pct": round(100.0 * schema_valid / total, 1) if total else 0.0,
        "schema_repair_count": repairs,
        "fallback_count": fallbacks,
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "latency_p95_ms": _percentile(latencies, 95.0),
        "per_grader": per_grader,
    }


def render_markdown_report(summary: dict[str, Any], results: list[CaseEvalResult]) -> str:
    """Format a human-readable markdown evaluation report."""
    lines = [
        "# Evaluation Report — Weather Risk Intelligence Agent",
        "",
        f"- **Timestamp (UTC):** {summary['timestamp']}",
        f"- **Requested Model:** `{summary['requested_model']}`",
        f"- **Observed Model(s):** {', '.join(f'`{m}`' for m in summary['observed_models']) or 'N/A'}",
        f"- **Pass Rate:** **{summary['passed_cases']}/{summary['total_cases']} ({summary['pass_rate_pct']}%)**",
        f"- **Schema Valid (after repair):** **{summary['schema_valid_cases']}/{summary['total_cases']} ({summary['schema_valid_pct']}%)**",
        f"- **Schema Repairs:** {summary['schema_repair_count']}",
        f"- **Fallbacks:** {summary['fallback_count']}",
        f"- **Latency:** p50 = {summary['latency_p50_ms']} ms, p95 = {summary['latency_p95_ms']} ms",
        "",
        "## Per-Grader Pass Rates",
        "",
        "| Grader | Passed | Total | Pass Rate |",
        "|---|---:|---:|---:|",
    ]
    for name, stats in summary["per_grader"].items():
        lines.append(
            f"| `{name}` | {stats['passed']} | {stats['total']} | {stats['pass_rate']}% |"
        )

    lines.extend(
        [
            "",
            "## Case Results",
            "",
            "| Case ID | Category | Result | Schema | Repairs | Latency (ms) | Notes |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        schema_str = "OK" if r.schema_valid else ("FALLBACK" if r.used_fallback else "INVALID")
        failed_notes = [f"{g.name}: {g.detail}" for g in r.graders if not g.passed]
        if r.error:
            failed_notes.insert(0, r.error)
        note_str = "; ".join(failed_notes) if failed_notes else "All checks passed"
        note_str = note_str.replace("|", "\\|")
        lines.append(
            f"| `{r.case_id}` | {r.category} | **{status}** | {schema_str} | {r.schema_repairs} | {r.latency_ms} | {note_str} |"
        )

    lines.append("")
    return "\n".join(lines)


async def run_evals(
    *,
    model: str | None = None,
    api_url: str | None = None,
    delay_seconds: float = 3.0,
    only_ids: set[str] | None = None,
    save_results: bool = True,
) -> tuple[dict[str, Any], list[CaseEvalResult]]:
    """Execute all selected eval cases and optionally write JSON/Markdown reports."""
    settings = get_settings()
    requested_model = model or settings.openrouter_model
    cases = load_cases(only_ids)
    if not cases:
        raise ValueError("No evaluation cases matched.")

    session_maker = get_session_maker()
    llm_client = OpenRouterLLMClient(settings)
    results: list[CaseEvalResult] = []

    async with httpx.AsyncClient(timeout=90.0) as http_client:
        for idx, case in enumerate(cases):
            if idx > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            async with session_maker() as db:
                try:
                    if api_url:
                        res = await _run_case_http(
                            db, http_client, api_url, case, model=model
                        )
                    else:
                        res = await _run_case_in_process(
                            db, llm_client, case, model=model
                        )
                except Exception as exc:
                    logger.exception("eval_case_error case_id=%s", case["id"])
                    res = CaseEvalResult(
                        case_id=str(case["id"]),
                        category=str(case.get("category", "general")),
                        passed=False,
                        schema_valid=False,
                        used_fallback=False,
                        schema_repairs=0,
                        model=requested_model,
                        latency_ms=0,
                        error=str(exc),
                    )
            results.append(res)
            status = "PASS" if res.passed else "FAIL"
            print(
                f"[{idx + 1}/{len(cases)}] {res.case_id}: {status} "
                f"(model={res.model}, repairs={res.schema_repairs}, {res.latency_ms}ms)"
            )

    summary = build_summary(results, requested_model=requested_model)
    report_md = render_markdown_report(summary, results)

    if save_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        payload = {
            "summary": summary,
            "cases": [asdict(r) for r in results],
        }
        (RESULTS_DIR / f"{stamp}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        (RESULTS_DIR / f"{stamp}.md").write_text(report_md, encoding="utf-8")
        (RESULTS_DIR / "latest.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        (RESULTS_DIR / "latest.md").write_text(report_md, encoding="utf-8")

    print("\n" + report_md)
    return summary, results


def main() -> None:
    """CLI entrypoint for `python -m evals.run`."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run Weather Risk Intelligence Agent evals.")
    parser.add_argument("--model", type=str, default=None, help="Override OpenRouter model ID")
    parser.add_argument("--api-url", type=str, default=None, help="Run against deployed HTTP API URL")
    parser.add_argument("--delay", type=float, default=3.0, help="Delay in seconds between cases")
    parser.add_argument("--only", type=str, default=None, help="Comma-separated case IDs to run")
    parser.add_argument("--no-save", action="store_true", help="Do not write report files")
    args = parser.parse_args()

    only_ids = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    asyncio.run(
        run_evals(
            model=args.model,
            api_url=args.api_url,
            delay_seconds=args.delay,
            only_ids=only_ids,
            save_results=not args.no_save,
        )
    )


if __name__ == "__main__":
    main()
