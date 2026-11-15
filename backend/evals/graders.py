"""Deterministic graders for evaluating agent responses against cases and oracles."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.grounding import extract_answer_numbers
from app.schemas.agent import FinalAnswer
from evals.oracles import oracle_rank, resolve_numeric_oracle


@dataclass
class GraderResult:
    """Result of a single grader check on one eval case."""

    name: str
    passed: bool
    detail: str


@dataclass
class CaseEvalResult:
    """Complete evaluation outcome for one case."""

    case_id: str
    category: str
    passed: bool
    schema_valid: bool
    used_fallback: bool
    schema_repairs: int
    model: str
    latency_ms: int
    graders: list[GraderResult] = field(default_factory=list)
    error: str | None = None


def _normalize_str(val: Any) -> str:
    return str(val).strip().lower()


def _grade_tools_any(expected_tools: list[str], tool_trace: list[dict[str, Any]]) -> GraderResult:
    called = [str(t.get("name", "")) for t in tool_trace]
    ok = any(name in called for name in expected_tools)
    return GraderResult(
        name="tools_any",
        passed=ok,
        detail=f"expected any of {expected_tools}, called {called}",
    )


def _grade_tool_args(
    expected_map: dict[str, dict[str, Any]],
    tool_trace: list[dict[str, Any]],
) -> GraderResult:
    mismatches: list[str] = []
    for tool_name, expected_args in expected_map.items():
        matching_calls = [t for t in tool_trace if t.get("name") == tool_name]
        if not matching_calls:
            mismatches.append(f"{tool_name}: not called")
            continue
        found_match = False
        for call in matching_calls:
            actual_args = call.get("arguments") or {}
            if all(
                _normalize_str(actual_args.get(k)) == _normalize_str(v)
                for k, v in expected_args.items()
            ):
                found_match = True
                break
        if not found_match:
            actuals = [c.get("arguments") for c in matching_calls]
            mismatches.append(f"{tool_name}: expected {expected_args}, got {actuals}")

    return GraderResult(
        name="tool_args",
        passed=len(mismatches) == 0,
        detail="all matched" if not mismatches else "; ".join(mismatches),
    )


def _grade_intent(expected_intent: str, answer: FinalAnswer) -> GraderResult:
    ok = answer.intent == expected_intent
    return GraderResult(
        name="intent",
        passed=ok,
        detail=f"expected '{expected_intent}', got '{answer.intent}'",
    )


def _grade_hubs_include(expected_hubs: list[str], answer: FinalAnswer) -> GraderResult:
    actual = {h.upper() for h in answer.hubs}
    missing = [h.upper() for h in expected_hubs if h.upper() not in actual]
    return GraderResult(
        name="hubs_include",
        passed=len(missing) == 0,
        detail=f"expected {expected_hubs}, got {answer.hubs}" + (f" (missing {missing})" if missing else ""),
    )


def _grade_hubs_exclude(excluded_hubs: list[str], answer: FinalAnswer) -> GraderResult:
    actual = {h.upper() for h in answer.hubs}
    present = [h.upper() for h in excluded_hubs if h.upper() in actual]
    return GraderResult(
        name="hubs_exclude",
        passed=len(present) == 0,
        detail=f"excluded {excluded_hubs}, got {answer.hubs}",
    )


async def _grade_top_k_oracle(
    db: AsyncSession,
    spec: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    answer: FinalAnswer,
    *,
    today: date | None = None,
) -> GraderResult:
    k = int(spec.get("k", 3))
    expected_codes = await oracle_rank(
        db,
        hazard=spec.get("hazard", "overall"),
        region=spec.get("region"),
        score_kind=spec.get("score_kind", "structural"),
        k=k,
        today=today,
    )

    actual_codes: list[str] = []
    for trace in reversed(tool_trace):
        if trace.get("name") == "rank_hubs" and trace.get("ok"):
            items = (trace.get("data") or {}).get("items") or []
            actual_codes = [str(item.get("code", "")).upper() for item in items[:k]]
            break

    if not actual_codes:
        for art in reversed(artifacts):
            if art.get("type") == "ranking_table":
                items = (art.get("data") or {}).get("items") or []
                actual_codes = [str(item.get("code", "")).upper() for item in items[:k]]
                break

    if not actual_codes:
        return GraderResult(
            name="top_k_matches_oracle",
            passed=False,
            detail=f"oracle top-{k}={expected_codes}, but no rank_hubs result found",
        )

    ok = actual_codes == expected_codes
    if ok and expected_codes:
        top1 = expected_codes[0]
        if top1 not in answer.answer_markdown.upper() and top1 not in [h.upper() for h in answer.hubs]:
            ok = False
            return GraderResult(
                name="top_k_matches_oracle",
                passed=False,
                detail=f"tool top-{k} matched {expected_codes}, but #1 hub {top1} missing from answer",
            )

    return GraderResult(
        name="top_k_matches_oracle",
        passed=ok,
        detail=f"expected top-{k}={expected_codes}, got {actual_codes}",
    )


async def _grade_numeric(
    db: AsyncSession,
    spec: dict[str, Any],
    answer: FinalAnswer,
    *,
    name: str = "numeric_in_answer",
    today: date | None = None,
) -> GraderResult:
    expected = await resolve_numeric_oracle(db, spec, today=today)
    tol = float(spec.get("tolerance", 0.5))
    nums = extract_answer_numbers(answer.answer_markdown)
    matched = [n for n in nums if abs(n - expected) <= tol]
    return GraderResult(
        name=name,
        passed=len(matched) > 0,
        detail=f"oracle={expected:.2f} (±{tol}), extracted={nums[:10]}",
    )


def _grade_assumptions_mention_any(phrases: list[str], answer: FinalAnswer) -> GraderResult:
    haystack = " ".join(answer.assumptions).lower()
    ok = any(p.lower() in haystack for p in phrases)
    return GraderResult(
        name="assumptions_mention_any",
        passed=ok,
        detail=f"expected any of {phrases} in assumptions={answer.assumptions}",
    )


def _grade_answer_mentions_any(phrases: list[str], answer: FinalAnswer) -> GraderResult:
    haystack = answer.answer_markdown.lower()
    ok = any(p.lower() in haystack for p in phrases)
    return GraderResult(
        name="answer_mentions_any",
        passed=ok,
        detail=f"expected any of {phrases} in answer",
    )


def _grade_forbidden_claims(phrases: list[str], answer: FinalAnswer) -> GraderResult:
    haystack = answer.answer_markdown.lower()
    found = [p for p in phrases if p.lower() in haystack]
    return GraderResult(
        name="forbidden_claims",
        passed=len(found) == 0,
        detail="none found" if not found else f"found forbidden claim(s): {found}",
    )


async def grade_case(
    db: AsyncSession,
    case: dict[str, Any],
    *,
    raw_answer: dict[str, Any] | FinalAnswer | None,
    tool_trace: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    warnings: list[str],
    schema_repairs: int,
    used_fallback: bool,
    model: str,
    latency_ms: int,
    today: date | None = None,
) -> CaseEvalResult:
    """Run all configured graders for a single eval case."""
    case_id = str(case["id"])
    category = str(case.get("category", "general"))
    expect = case.get("expect") or {}

    graders: list[GraderResult] = []
    answer: FinalAnswer | None = None
    schema_valid = False

    if isinstance(raw_answer, FinalAnswer):
        answer = raw_answer
        schema_valid = not used_fallback
    elif isinstance(raw_answer, dict):
        try:
            answer = FinalAnswer.model_validate(raw_answer)
            schema_valid = not used_fallback
        except ValidationError as exc:
            graders.append(
                GraderResult(
                    name="schema_valid",
                    passed=False,
                    detail=f"FinalAnswer validation failed: {exc}",
                )
            )

    if answer is None:
        return CaseEvalResult(
            case_id=case_id,
            category=category,
            passed=False,
            schema_valid=False,
            used_fallback=used_fallback,
            schema_repairs=schema_repairs,
            model=model,
            latency_ms=latency_ms,
            graders=graders,
            error="No FinalAnswer produced",
        )

    graders.append(
        GraderResult(
            name="schema_valid",
            passed=schema_valid,
            detail="valid FinalAnswer" if schema_valid else "fallback triggered instead of valid LLM step",
        )
    )

    if "tools_any" in expect:
        graders.append(_grade_tools_any(expect["tools_any"], tool_trace))
    if "tool_args" in expect:
        graders.append(_grade_tool_args(expect["tool_args"], tool_trace))
    if "intent" in expect:
        graders.append(_grade_intent(expect["intent"], answer))
    if "hubs_include" in expect:
        graders.append(_grade_hubs_include(expect["hubs_include"], answer))
    if "hubs_exclude" in expect:
        graders.append(_grade_hubs_exclude(expect["hubs_exclude"], answer))
    if "top_k_matches_oracle" in expect:
        graders.append(
            await _grade_top_k_oracle(
                db,
                expect["top_k_matches_oracle"],
                tool_trace,
                artifacts,
                answer,
                today=today,
            )
        )
    if "numeric_in_answer" in expect:
        graders.append(await _grade_numeric(db, expect["numeric_in_answer"], answer, today=today))
    if "numeric_checks" in expect:
        for idx, spec in enumerate(expect["numeric_checks"], start=1):
            graders.append(
                await _grade_numeric(
                    db,
                    spec,
                    answer,
                    name=f"numeric_check_{idx}",
                    today=today,
                )
            )
    if "assumptions_mention_any" in expect:
        graders.append(_grade_assumptions_mention_any(expect["assumptions_mention_any"], answer))
    if "answer_mentions_any" in expect:
        graders.append(_grade_answer_mentions_any(expect["answer_mentions_any"], answer))
    if "forbidden_claims" in expect:
        graders.append(_grade_forbidden_claims(expect["forbidden_claims"], answer))

    unverified = [w for w in warnings if "unverified" in w.lower()]
    if unverified:
        graders.append(
            GraderResult(
                name="grounding_numbers",
                passed=False,
                detail="; ".join(unverified),
            )
        )

    passed = all(g.passed for g in graders)
    return CaseEvalResult(
        case_id=case_id,
        category=category,
        passed=passed,
        schema_valid=schema_valid,
        used_fallback=used_fallback,
        schema_repairs=schema_repairs,
        model=model,
        latency_ms=latency_ms,
        graders=graders,
    )
