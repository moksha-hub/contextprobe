"""Stateless browser playground for testing one description and an optional rewrite."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import answerer, catalog, grader, risk


class PlaygroundUnavailable(RuntimeError):
    """Raised when explicit real-model execution cannot produce an answer."""


class PlaygroundRequest(BaseModel):
    column_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    question: str = Field(min_length=1, max_length=500)
    expected_answer: str = Field(min_length=1, max_length=1000)
    required_terms: str = Field(min_length=1, max_length=500)
    correct_markers: str = Field(min_length=1, max_length=500)
    proposed_rewrite: str | None = Field(default=None, max_length=2000)
    mode: Literal["auto", "llm", "simulated"] = "auto"
    downstream_count: int = Field(default=0, ge=0, le=10000)
    certified: bool = False


def _csv(value: str, label: str) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        cleaned = item.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            items.append(cleaned)
    if not items:
        raise ValueError(f"{label} must contain at least one comma-separated value")
    if len(items) > 20:
        raise ValueError(f"{label} supports at most 20 values")
    return items


def _wrong_answer(markers: list[str]) -> str:
    candidates = (
        "Yes, the metadata supports that conclusion.",
        "The answer is affirmative.",
        "A definite value can be inferred.",
        "Unsupported claim.",
    )
    for candidate in candidates:
        if not grader.matches_expected(candidate, markers):
            return candidate
    raise ValueError("Correct markers are too broad to grade safely; use specific phrases")


def _context(request: PlaygroundRequest, description: str) -> dict[str, Any]:
    return {
        "asset": "user_input",
        "asset_type": "table",
        "asset_description": None,
        "certified": request.certified,
        "deprecated": False,
        "columns": [{
            "name": request.column_name.strip(),
            "type": "unknown",
            "description": description.strip() or None,
        }],
    }

def _evaluate(
    request: PlaygroundRequest,
    description: str,
    required_terms: list[str],
    correct_markers: list[str],
) -> dict[str, Any]:
    context = _context(request, description)
    context_seen = catalog.context_text(context)
    probe = {
        "id": "playground",
        "asset_id": "user_input",
        "column_name": request.column_name.strip(),
        "question": request.question.strip(),
        "required_terms": required_terms,
        "expected_answer": request.expected_answer.strip(),
        "wrong_answer": _wrong_answer(correct_markers),
        "correct_markers": correct_markers,
    }

    response: dict[str, Any] | None = None
    engine = "simulated"
    attempted_llm = request.mode in {"auto", "llm"} and answerer.llm_available()
    if request.mode == "llm" and not answerer.llm_available():
        raise PlaygroundUnavailable("Real-model mode is not configured on this deployment")
    if attempted_llm:
        response = answerer.llm_answer(probe, context)
        if response is not None:
            engine = "llm"
    if request.mode == "llm" and response is None:
        raise PlaygroundUnavailable("The model provider did not return a usable answer")
    if response is None:
        response = answerer.simulated_answer(probe, context, context_seen)

    facts_present = answerer.answerable(context_seen, required_terms)
    graded = grader.grade(probe, response, facts_present)
    wrong_rate = 1.0 if graded["outcome"] == grader.CONFIDENT_WRONG else 0.0
    answer_lower = graded["answer"].lower()
    return {
        "description": description,
        "engine": engine,
        "llm_fallback": attempted_llm and engine == "simulated",
        "answer": graded["answer"],
        "outcome": graded["outcome"],
        "reason": graded["reason"],
        "facts_present_in_context": graded["facts_present_in_context"],
        "over_abstention": graded["over_abstention"],
        "matched_markers": [m for m in correct_markers if m in answer_lower],
        "context_seen": context_seen,
        "risk": risk.score(wrong_rate, request.downstream_count, request.certified),
    }


def _transition(before: str, after: str) -> str:
    if before == grader.CONFIDENT_WRONG and after == grader.CORRECT:
        return "fixed"
    if before == grader.CONFIDENT_WRONG and after == grader.ABSTAINED:
        return "made_safe"
    if before == grader.CORRECT and after != grader.CORRECT:
        return "regressed"
    if before == grader.ABSTAINED and after == grader.CONFIDENT_WRONG:
        return "regressed"
    return "unchanged"


def run_playground(request: PlaygroundRequest) -> dict[str, Any]:
    """Evaluate user input without reading from or writing to SQLite."""
    if not request.column_name.strip() or not request.question.strip():
        raise ValueError("Column name and question cannot be blank")
    if not request.expected_answer.strip():
        raise ValueError("Expected answer cannot be blank")
    required_terms = _csv(request.required_terms, "Required context terms")
    correct_markers = _csv(request.correct_markers, "Correct answer markers")

    original = _evaluate(
        request,
        request.description,
        required_terms,
        correct_markers,
    )
    rewrite = None
    comparison = None
    if request.proposed_rewrite and request.proposed_rewrite.strip():
        rewrite = _evaluate(
            request,
            request.proposed_rewrite.strip(),
            required_terms,
            correct_markers,
        )
        transition = _transition(original["outcome"], rewrite["outcome"])
        comparison = {
            "transition": transition,
            "risk_before": original["risk"],
            "risk_after": rewrite["risk"],
            "risk_delta": round(rewrite["risk"] - original["risk"], 2),
            "improved": transition in {"fixed", "made_safe"},
            "regressed": transition == "regressed",
        }

    return {
        "requested_mode": request.mode,
        "required_terms": required_terms,
        "correct_markers": correct_markers,
        "expected_answer": request.expected_answer.strip(),
        "original": original,
        "rewrite": rewrite,
        "comparison": comparison,
        "caution": (
            "This grades short answers with explicit markers. It measures answerability "
            "and model behaviour, not the factual truth of arbitrary metadata."
        ),
    }
