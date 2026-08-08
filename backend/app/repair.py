"""The repair gate.

A rewrite is accepted only when it makes a previously-failing question answerable
without breaking one that already passed.

Why a gate is necessary rather than nice to have: Atlan AI Labs measured a verbose
metadata variant performing 13.8% worse than a concise one on the same query suite.
So editing a description is not monotonically an improvement — it can regress. And
their enrichment agents skip any asset that already has a description, so a vague
one is never revisited automatically. This module targets exactly that population.

Generation is blind to the probe suite. Diagnosis afterwards is not.
"""

import re
from typing import Any

from . import catalog, runner
from .catalog import SALIENCE_CHARS
from .database import connect
from .grader import ABSTAINED, CONFIDENT_WRONG, CORRECT

STRATEGIES = ("grounded", "verbose", "narrow")

# Clauses carrying these markers state a boundary, a unit, or an identity rule —
# the kind of fact a probe asks about.
QUALIFIER_MARKERS = (
    "excluding", "including", "before", "after", "converted to", "in usd",
    "in utc", "at the", "not the", "unique per", "per row", "derived from",
    "measured in", "generated in", "recognized at",
)

# Deliberately generic governance prose, used only by the `verbose` strategy to
# reproduce the padding pattern that regressed accuracy in Atlan's study.
BOILERPLATE = (
    "This field is maintained by the data platform team as part of the governed "
    "analytics layer and is reviewed periodically under the organisation's data "
    "quality programme. It is intended for reporting and downstream consumption "
    "by certified dashboards and approved analytical workloads. "
)

TOKEN = re.compile(r"[a-z0-9_]+")


def _tokens(value: str) -> set[str]:
    return set(TOKEN.findall(value.lower()))


def _clauses(text: str) -> list[str]:
    """Split on sentence and semicolon boundaries only.

    Not on commas: a list like "returns, refunds and tax" is one fact, and
    splitting it would silently drop two thirds of the qualifier.
    """
    parts = re.split(r"[;.]", text)
    return [part.strip(" .,;") for part in parts if part and part.strip(" .,;")]


def _qualifier_clauses(
    sources: list[dict[str, Any]], target_tokens: set[str]
) -> list[dict[str, Any]]:
    """Rank documented clauses that state a fact, most relevant first."""
    origin_rank = {"upstream": 0, "sibling": 1, "asset": 2}
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        for clause in _clauses(source["text"]):
            lowered = clause.lower()
            if not any(marker in lowered for marker in QUALIFIER_MARKERS):
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            ranked.append({
                "clause": clause,
                "origin": source["origin"],
                "from_asset": source["asset"],
                "from_column": source["column"],
                "overlap": len(target_tokens & _tokens(clause)),
            })
    ranked.sort(key=lambda item: (-item["overlap"], origin_rank[item["origin"]], item["clause"]))
    return ranked


def _sentence(clause: str) -> str:
    return clause[0].upper() + clause[1:] if clause else clause


def compose(
    strategy: str, column_name: str, asset_name: str, ranked: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Build a candidate description from documented clauses only."""
    if strategy == "narrow":
        used = ranked[:1]
    elif strategy == "verbose":
        used = ranked[:3]
    else:
        used = ranked[:2]
    lead = f"{column_name.replace('_', ' ').capitalize()} in {asset_name}"
    body = " ".join(f"{_sentence(item['clause'])}." for item in used)
    if strategy == "verbose":
        return f"{lead}. {BOILERPLATE}{body}".strip(), used
    return f"{lead}. {body}".strip(), used


def _outcomes(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {result["probe_id"]: result for result in run["results"]}


def _classify(before: str, after: str) -> str:
    """How one probe moved. Only two labels change the verdict."""
    if before == CONFIDENT_WRONG and after == CORRECT:
        return "fixed"
    if before == CORRECT and after != CORRECT:
        return "regressed"
    if before == ABSTAINED and after == CONFIDENT_WRONG:
        return "regressed"
    if before == CONFIDENT_WRONG and after == ABSTAINED:
        return "made_safe"
    return "unchanged"


def _buried(candidate: str, probes: list[dict[str, Any]], failing: set[str]) -> bool:
    """True when a required fact is present in the text but past the salience window."""
    lowered = candidate.lower()
    window = lowered[:SALIENCE_CHARS]
    for probe in probes:
        if probe["id"] not in failing:
            continue
        terms = [term.lower() for term in probe["required_terms"]]
        if all(term in lowered for term in terms) and not all(term in window for term in terms):
            return True
    return False


def _save(record: dict[str, Any]) -> int:
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO repairs
               (asset_id, column_name, strategy, engine, before_description,
                after_description, fixed_count, regressed_count, unchanged_count,
                verdict, reject_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["asset_id"], record["column_name"], record["strategy"],
                record["engine"], record["before_description"], record["after_description"],
                record["fixed_count"], record["regressed_count"], record["unchanged_count"],
                record["verdict"], record["reject_reason"],
            ),
        )
        return int(cursor.lastrowid)


def repair_column(
    asset_id: str, column_name: str, strategy: str = "grounded", mode: str = "auto"
) -> dict[str, Any]:
    """Propose and evaluate a rewrite. Always a dry run; never commits."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Strategy must be one of {', '.join(STRATEGIES)}")
    asset = catalog.get_asset(asset_id)
    if asset is None:
        raise LookupError("Asset not found")
    columns = {column["name"]: column for column in catalog.get_columns(asset_id)}
    if column_name not in columns:
        raise LookupError("Column not found")

    before_description = columns[column_name]["description"]
    probes = catalog.get_probes(asset_id)

    baseline = runner.run_probes(asset_id, mode, persist=False)
    before_outcomes = _outcomes(baseline)

    target_tokens = _tokens(column_name) | _tokens(before_description or "")
    sources = catalog.grounding_sources(asset_id, column_name)
    ranked = _qualifier_clauses(sources, target_tokens)
    if not ranked:
        raise LookupError("No documented upstream or sibling facts to draw on")
    candidate, used = compose(strategy, column_name, asset["name"], ranked)

    # Apply, re-probe, and restore. The restore is in `finally` so a failure part
    # way through can never leave a machine-written description in the catalog.
    try:
        catalog.update_description(asset_id, column_name, candidate)
        after = runner.run_probes(asset_id, mode, persist=False)
    finally:
        catalog.update_description(asset_id, column_name, before_description)

    after_outcomes = _outcomes(after)
    rows = []
    for probe in probes:
        before_outcome = before_outcomes[probe["id"]]["outcome"]
        after_outcome = after_outcomes[probe["id"]]["outcome"]
        rows.append({
            "probe_id": probe["id"],
            "column_name": probe["column_name"],
            "question": probe["question"],
            "before": before_outcome,
            "after": after_outcome,
            "transition": _classify(before_outcome, after_outcome),
            "on_target_column": probe["column_name"] == column_name,
        })

    fixed = [row for row in rows if row["transition"] == "fixed"]
    regressed = [row for row in rows if row["transition"] == "regressed"]
    made_safe = [row for row in rows if row["transition"] == "made_safe"]
    still_failing = {
        row["probe_id"] for row in rows
        if row["after"] == CONFIDENT_WRONG and row["on_target_column"]
    }

    if regressed:
        verdict, reason = "rejected", (
            f"regression: {len(regressed)} probe(s) that previously passed no longer do"
        )
    elif not fixed and _buried(candidate, probes, still_failing):
        verdict, reason = "rejected", (
            "the required facts are present but fall outside the "
            f"{SALIENCE_CHARS}-character salience window, so they read as noise"
        )
    elif not fixed:
        verdict, reason = "rejected", (
            "no failing probe was fixed: the documented upstream and sibling text "
            "does not contain the missing facts"
        )
    else:
        verdict, reason = "accepted", None

    record = {
        "asset_id": asset_id,
        "column_name": column_name,
        "strategy": strategy,
        "engine": baseline["engine"],
        "before_description": before_description,
        "after_description": candidate,
        "fixed_count": len(fixed),
        "regressed_count": len(regressed),
        "unchanged_count": len(rows) - len(fixed) - len(regressed) - len(made_safe),
        "verdict": verdict,
        "reject_reason": reason,
    }
    repair_id = _save(record)

    return {
        **record,
        "repair_id": repair_id,
        "dry_run": True,
        "candidate_length": len(candidate),
        "salience_chars": SALIENCE_CHARS,
        "made_safe_count": len(made_safe),
        "grounding_used": used,
        "grounding_available": len(ranked),
        "probe_transitions": rows,
        "caution": (
            "The gate verifies answerability, not truth. An inherited clause can be "
            "worded correctly for its source column and still be wrong here, so a "
            "steward must confirm the wording before this is committed."
        ),
    }


def repair_all(mode: str = "auto") -> dict[str, Any]:
    """Attempt a grounded repair on every column that currently fails a probe."""
    from . import risk

    failing: dict[str, set[str]] = {}
    latest = {result["probe_id"]: result for result in risk.latest_results()}
    for asset in catalog.list_assets():
        for probe in catalog.get_probes(asset["id"]):
            result = latest.get(probe["id"])
            if result and result["outcome"] == CONFIDENT_WRONG and probe["column_name"]:
                failing.setdefault(asset["id"], set()).add(probe["column_name"])

    attempts = []
    for asset_id, column_names in sorted(failing.items()):
        for column_name in sorted(column_names):
            try:
                outcome = repair_column(asset_id, column_name, "grounded", mode)
            except LookupError as error:
                attempts.append({
                    "asset_id": asset_id, "column_name": column_name,
                    "verdict": "skipped", "reject_reason": str(error),
                    "fixed_count": 0, "regressed_count": 0,
                })
                continue
            attempts.append({
                key: outcome[key] for key in
                ("asset_id", "column_name", "verdict", "reject_reason",
                 "fixed_count", "regressed_count", "after_description")
            })

    accepted = [item for item in attempts if item["verdict"] == "accepted"]
    return {
        "attempted": len(attempts),
        "accepted": len(accepted),
        "rejected": sum(item["verdict"] == "rejected" for item in attempts),
        "skipped": sum(item["verdict"] == "skipped" for item in attempts),
        "accept_rate": round(len(accepted) / len(attempts), 3) if attempts else 0.0,
        "attempts": attempts,
        "note": (
            "Lineage-grounded rewrites can only convey facts documented somewhere "
            "upstream. A rejection is often correct: the fact exists nowhere in the "
            "catalog and a human has to supply it."
        ),
    }
