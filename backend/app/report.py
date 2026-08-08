"""Coverage versus risk: the comparison that makes the point."""

from typing import Any

from . import catalog, risk


def coverage_vs_risk() -> dict[str, Any]:
    queue = [item for item in risk.risk_queue() if item["probed"]]
    misleading = [
        item for item in queue
        if item["column_coverage"] >= 0.999 and item["confident_wrong"] > 0
    ]
    return {
        "assets_probed": len(queue),
        "note": (
            "Coverage counts descriptions. Risk counts confident wrong answers weighted by "
            "downstream assets. Assets listed under misleading_coverage are fully covered and "
            "still unsafe for an agent."
        ),
        "misleading_coverage": [
            {
                "asset": item["asset"],
                "column_coverage": item["column_coverage"],
                "confident_wrong": item["confident_wrong"],
                "probes": item["probes"],
                "risk": item["risk"],
            }
            for item in misleading
        ],
        "rows": [
            {
                "asset": item["asset"],
                "column_coverage": item["column_coverage"],
                "correct": item["correct"],
                "abstained": item["abstained"],
                "confident_wrong": item["confident_wrong"],
                "probes": item["probes"],
                "downstream_assets": item["downstream_assets"],
                "risk": item["risk"],
            }
            for item in queue
        ],
    }


def paired_comparison() -> dict[str, Any]:
    """The controlled pair: two columns, same coverage, same probe count."""
    breakdown = {item["column"]: item for item in risk.column_breakdown("fct_revenue")}
    pair = []
    for column in ("net_revenue", "gross_revenue"):
        item = breakdown.get(column)
        if item is None:
            continue
        pair.append({
            "column": column,
            "description": item["description"],
            "has_description": bool((item["description"] or "").strip()),
            "probes": item["probes"],
            "correct": item["correct"],
            "abstained": item["abstained"],
            "confident_wrong": item["confident_wrong"],
        })
    return {
        "asset": "fct_revenue",
        "note": (
            "Both columns are documented, so column coverage treats them identically. "
            "The probe outcomes do not."
        ),
        "columns": pair,
    }
