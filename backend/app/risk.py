"""Risk scoring: rank metadata by the damage a confident wrong answer would do."""

from typing import Any

from . import catalog
from .database import connect
from .grader import ABSTAINED, CONFIDENT_WRONG, CORRECT

CERTIFIED_WEIGHT = 1.5


def score(confident_wrong_rate: float, downstream: int, certified: bool) -> float:
    """A weak description matters in proportion to what depends on it.

    Weights are chosen for this demo, not calibrated on production data.
    """
    weight = CERTIFIED_WEIGHT if certified else 1.0
    return round(confident_wrong_rate * (1 + downstream) * weight, 2)


def save_results(engine: str, results: list[dict[str, Any]], contexts: dict[str, str]) -> None:
    with connect() as db:
        db.executemany(
            """INSERT INTO probe_results
               (probe_id, asset_id, engine, outcome, answer, context_seen)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    result["probe_id"], result["asset_id"], engine,
                    result["outcome"], result["answer"], contexts.get(result["probe_id"], ""),
                )
                for result in results
            ],
        )


def latest_results() -> list[dict[str, Any]]:
    """Most recent result per probe."""
    with connect() as db:
        rows = db.execute(
            """SELECT r.* FROM probe_results r
               JOIN (
                 SELECT probe_id, MAX(id) AS newest
                 FROM probe_results GROUP BY probe_id
               ) latest ON latest.newest = r.id
               ORDER BY r.asset_id, r.probe_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def _tally(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "probes": len(results),
        "correct": sum(item["outcome"] == CORRECT for item in results),
        "abstained": sum(item["outcome"] == ABSTAINED for item in results),
        "confident_wrong": sum(item["outcome"] == CONFIDENT_WRONG for item in results),
    }


def asset_risk(asset: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _tally(results)
    total = counts["probes"]
    rate = counts["confident_wrong"] / total if total else 0.0
    downstream = catalog.downstream_count(asset["id"])
    return {
        "asset_id": asset["id"],
        "asset": asset["name"],
        "asset_type": asset["asset_type"],
        "certified": bool(asset["certified"]),
        "deprecated": bool(asset["deprecated"]),
        "owner": asset["owner"],
        **catalog.coverage(asset["id"]),
        **counts,
        "confident_wrong_rate": round(rate, 3),
        "downstream_assets": downstream,
        "risk": score(rate, downstream, bool(asset["certified"])),
        "probed": total > 0,
    }


def risk_queue() -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in latest_results():
        grouped.setdefault(result["asset_id"], []).append(result)
    queue = [
        asset_risk(asset, grouped.get(asset["id"], []))
        for asset in catalog.list_assets()
    ]
    queue.sort(key=lambda item: (-item["risk"], -item["confident_wrong"], item["asset_id"]))
    return queue


def column_breakdown(asset_id: str) -> list[dict[str, Any]]:
    """Per-column outcome counts, so a steward sees which description to fix."""
    results = [item for item in latest_results() if item["asset_id"] == asset_id]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["probe_id"], []).append(result)
    probes = {probe["id"]: probe for probe in catalog.get_probes(asset_id)}
    by_column: dict[str, list[dict[str, Any]]] = {}
    for probe_id, items in grouped.items():
        probe = probes.get(probe_id)
        if probe is None:
            continue
        key = probe["column_name"] or "(asset level)"
        by_column.setdefault(key, []).extend(items)
    columns = {column["name"]: column for column in catalog.get_columns(asset_id)}
    breakdown = []
    for name, items in by_column.items():
        counts = _tally(items)
        breakdown.append({
            "column": name,
            "description": columns.get(name, {}).get("description"),
            **counts,
        })
    breakdown.sort(key=lambda item: (-item["confident_wrong"], item["column"]))
    return breakdown
