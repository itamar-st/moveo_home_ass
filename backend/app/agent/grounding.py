"""Post-validation grounding checks on FinalAnswer (AGENTS.md §7.4)."""

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.schemas.agent import FinalAnswer
from app.schemas.tools import ToolResult

logger = logging.getLogger(__name__)

QUANTITATIVE_INTENTS = frozenset({"ranking", "comparison", "statistic", "explanation"})

# Match ISO dates first so their "-MM-DD" segments aren't parsed as negative numbers.
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
LIST_PREFIX_RE = re.compile(r"(?m)^\s*\d+\.\s+")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])(-?\d+(?:,\d{3})*(?:\.\d+)?)")


@dataclass
class GroundingCheckResult:
    """Validated FinalAnswer plus any grounding warnings."""

    final_answer: FinalAnswer
    warnings: list[str] = field(default_factory=list)
    unverified_numbers: list[float] = field(default_factory=list)


def _collect_numbers_from_obj(obj: Any, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
        return
    if isinstance(obj, str):
        for match in ISO_DATE_RE.finditer(obj):
            out.add(float(int(match.group(1))))
        cleaned = ISO_DATE_RE.sub(" ", obj)
        for match in NUMBER_RE.finditer(cleaned):
            raw = match.group(1).replace(",", "")
            try:
                out.add(float(raw))
            except ValueError:
                pass
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers_from_obj(v, out)
        return
    if isinstance(obj, (list, tuple, set)):
        for item in obj:
            _collect_numbers_from_obj(item, out)


def collect_tool_numbers(
    tool_results: Sequence[ToolResult],
    prior_tool_lines: Sequence[str] = (),
) -> set[float]:
    """Collect all numeric figures present in tool outputs and prior tool summaries."""
    nums: set[float] = set()
    for tr in tool_results:
        _collect_numbers_from_obj(tr.data, nums)
        _collect_numbers_from_obj(tr.summary, nums)
        _collect_numbers_from_obj(tr.data_window, nums)
        _collect_numbers_from_obj(tr.caveats, nums)
    for line in prior_tool_lines:
        _collect_numbers_from_obj(line, nums)
    return nums


def extract_answer_numbers(text: str) -> list[float]:
    """Extract numeric claims from answer_markdown while ignoring list markers and ISO date dashes."""
    without_lists = LIST_PREFIX_RE.sub("", text)
    # Keep years from ISO dates, drop -MM-DD so they aren't parsed as negative numbers.
    without_dates = ISO_DATE_RE.sub(lambda m: m.group(1), without_lists)

    extracted: list[float] = []
    for match in NUMBER_RE.finditer(without_dates):
        raw = match.group(1).replace(",", "")
        try:
            extracted.append(float(raw))
        except ValueError:
            continue
    return extracted


def requires_tool_call(
    final_answer: FinalAnswer,
    tool_results: Sequence[ToolResult],
    *,
    has_prior_tool_context: bool = False,
) -> bool:
    """Return True if a quantitative FinalAnswer lacks any supporting tool execution."""
    if final_answer.intent not in QUANTITATIVE_INTENTS:
        return False
    has_ok_tool = any(tr.ok for tr in tool_results)
    return not has_ok_tool and not has_prior_tool_context


def check_final_answer(
    final_answer: FinalAnswer,
    tool_results: Sequence[ToolResult],
    known_hub_codes: Iterable[str],
    *,
    prior_tool_lines: Sequence[str] = (),
    tolerance: float = 0.5,
) -> GroundingCheckResult:
    """Validate FinalAnswer hub codes and cross-check numeric claims against tool results."""
    known_upper = {c.upper() for c in known_hub_codes}
    warnings: list[str] = []

    valid_hubs: list[str] = []
    dropped_hubs: list[str] = []
    for code in final_answer.hubs:
        upper = code.strip().upper()
        if upper in known_upper:
            if upper not in valid_hubs:
                valid_hubs.append(upper)
        elif code.strip():
            dropped_hubs.append(code)

    if dropped_hubs:
        msg = f"Dropped unknown hub code(s): {', '.join(dropped_hubs)}"
        logger.warning("grounding_unknown_hubs dropped=%s", dropped_hubs)
        warnings.append(msg)

    sanitized = final_answer.model_copy(update={"hubs": valid_hubs})

    tool_numbers = collect_tool_numbers(tool_results, prior_tool_lines=prior_tool_lines)
    # Standard WDRI tier cutoffs & portfolio size (20) are static domain constants.
    domain_constants = {0.0, 20.0, 25.0, 50.0, 75.0, 100.0}
    allowed_numbers = tool_numbers | domain_constants

    answer_numbers = extract_answer_numbers(sanitized.answer_markdown)
    unverified: list[float] = []
    if tool_results or prior_tool_lines:
        for val in answer_numbers:
            if not any(abs(val - ref) <= tolerance for ref in allowed_numbers):
                unverified.append(val)
    elif answer_numbers and sanitized.intent in QUANTITATIVE_INTENTS:
        for val in answer_numbers:
            if val not in domain_constants:
                unverified.append(val)

    if unverified:
        formatted = ", ".join(f"{n:g}" for n in unverified[:5])
        logger.warning("grounding_unverified_numbers values=%s", unverified)
        warnings.append(f"⚠ unverified figure(s): {formatted}")

    return GroundingCheckResult(
        final_answer=sanitized,
        warnings=warnings,
        unverified_numbers=unverified,
    )
