"""Run probes against the catalog and grade the outcomes."""

from typing import Any

from . import answerer, catalog, risk
from .grader import grade


def run_probes(asset_id: str | None = None, mode: str = "auto") -> dict[str, Any]:
    if mode not in {"auto", "simulated"}:
        raise ValueError("Mode must be auto or simulated")
    probes = catalog.get_probes(asset_id)
    if not probes:
        raise LookupError("No probes found for that asset")

    use_llm = mode == "auto" and answerer.llm_available()
    engine = "llm" if use_llm else "simulated"
    results: list[dict[str, Any]] = []
    contexts: dict[str, str] = {}
    fell_back = 0

    for probe in probes:
        context = catalog.build_context(probe["asset_id"], probe["column_name"])
        text = catalog.context_text(context)
        facts_present = answerer.answerable(text, probe["required_terms"])

        response = answerer.llm_answer(probe, context) if use_llm else None
        if response is None:
            if use_llm:
                fell_back += 1
            response = answerer.simulated_answer(probe, context, text)

        results.append(grade(probe, response, facts_present))
        contexts[probe["id"]] = _describe_context(context, probe["column_name"])

    risk.save_results(engine, results, contexts)
    return {
        "engine": engine,
        "llm_fallbacks": fell_back,
        "probes_run": len(results),
        "results": results,
        "summary": _summarize(results),
    }


def _describe_context(context: dict[str, Any], column_name: str | None) -> str:
    if column_name:
        for column in context["columns"]:
            if column["name"] == column_name:
                return column["description"] or "(no description)"
    return context["asset_description"] or "(no description)"


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    counts = {
        "correct": sum(item["outcome"] == "correct" for item in results),
        "abstained": sum(item["outcome"] == "abstained" for item in results),
        "confident_wrong": sum(item["outcome"] == "confident_wrong" for item in results),
        "over_abstentions": sum(item["over_abstention"] for item in results),
    }
    counts["probes"] = total
    counts["confident_wrong_rate"] = round(counts["confident_wrong"] / total, 3) if total else 0.0
    return counts
