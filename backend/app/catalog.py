import json
from collections import deque
from typing import Any

from .database import connect


def get_asset(asset_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return dict(row) if row else None


def list_assets() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM assets ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def get_columns(asset_id: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT name, data_type, description FROM columns WHERE asset_id = ? ORDER BY name",
            (asset_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_probes(asset_id: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM probes"
    params: tuple[Any, ...] = ()
    if asset_id:
        query += " WHERE asset_id = ?"
        params = (asset_id,)
    query += " ORDER BY id"
    with connect() as db:
        rows = db.execute(query, params).fetchall()
    probes = []
    for row in rows:
        probe = dict(row)
        probe["required_terms"] = json.loads(probe.pop("required_terms_json"))
        probe["correct_markers"] = json.loads(probe.pop("correct_markers_json"))
        probes.append(probe)
    return probes


def downstream_count(asset_id: str) -> int:
    """Number of distinct assets reachable downstream, via breadth-first search."""
    with connect() as db:
        rows = db.execute("SELECT upstream_id, downstream_id FROM lineage_edges").fetchall()
    adjacency: dict[str, list[str]] = {}
    for row in rows:
        adjacency.setdefault(row["upstream_id"], []).append(row["downstream_id"])
    seen: set[str] = set()
    queue = deque([asset_id])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return len(seen)


def downstream_assets(asset_id: str) -> list[str]:
    with connect() as db:
        rows = db.execute("SELECT upstream_id, downstream_id FROM lineage_edges").fetchall()
    adjacency: dict[str, list[str]] = {}
    for row in rows:
        adjacency.setdefault(row["upstream_id"], []).append(row["downstream_id"])
    found: list[str] = []
    seen: set[str] = set()
    queue = deque([asset_id])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                found.append(neighbor)
                queue.append(neighbor)
    return found


def build_context(asset_id: str, column_name: str | None) -> dict[str, Any]:
    """Assemble exactly the metadata a production agent would receive.

    Nothing outside the catalog is added: no ground truth, no probe hints.
    """
    asset = get_asset(asset_id)
    if asset is None:
        raise LookupError("Asset not found")
    columns = get_columns(asset_id)
    if column_name:
        columns = [column for column in columns if column["name"] == column_name]
    return {
        "asset": asset["name"],
        "asset_type": asset["asset_type"],
        "asset_description": asset["description"],
        "certified": bool(asset["certified"]),
        "deprecated": bool(asset["deprecated"]),
        "columns": [
            {"name": column["name"], "type": column["data_type"], "description": column["description"]}
            for column in columns
        ],
    }


# Only the first N characters of a description are treated as reliably visible.
#
# This models attention decay. Atlan AI Labs measured a verbose 176-line metadata
# variant performing 13.8% WORSE than a 64-line one on the same 522-query suite,
# because prose written for humans reads as noise to a model. A fact buried past
# this window is therefore treated as diluted rather than conveyed.
#
# Like the simulated answerer, this is a documented hypothesis, not evidence about
# real models. Every description in the seeded fixture is well under this limit,
# so it changes no baseline result; it only catches padded rewrites.
SALIENCE_CHARS = 200


def salient(description: str | None) -> str:
    return (description or "")[:SALIENCE_CHARS]


def context_text(context: dict[str, Any]) -> str:
    """Documented text only, truncated to the salience window.

    Asset and column *names* are deliberately excluded. A fact that appears only
    in an identifier is not documentation, and treating it as such would credit
    the catalog for something an agent can only guess at.
    """
    parts = [salient(context["asset_description"])]
    parts.extend(salient(column["description"]) for column in context["columns"])
    return " ".join(parts).lower()


def coverage(asset_id: str) -> dict[str, Any]:
    columns = get_columns(asset_id)
    described = [column for column in columns if (column["description"] or "").strip()]
    total = len(columns)
    return {
        "columns": total,
        "described_columns": len(described),
        "column_coverage": round(len(described) / total, 3) if total else 1.0,
    }


def upstream_assets(asset_id: str) -> list[str]:
    with connect() as db:
        rows = db.execute("SELECT upstream_id, downstream_id FROM lineage_edges").fetchall()
    adjacency: dict[str, list[str]] = {}
    for row in rows:
        adjacency.setdefault(row["downstream_id"], []).append(row["upstream_id"])
    found: list[str] = []
    seen: set[str] = set()
    queue = deque([asset_id])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                found.append(neighbor)
                queue.append(neighbor)
    return found


def grounding_sources(asset_id: str, column_name: str) -> list[dict[str, Any]]:
    """Documented text a rewrite is allowed to draw on.

    Only the catalog's own content: upstream column descriptions reached through
    lineage, sibling columns in the same asset, and the asset description.

    Deliberately absent: the probe questions and their required terms. If a
    generator could see those, it would insert the exact tokens the grader looks
    for and every repair would pass by construction.
    """
    sources: list[dict[str, Any]] = []
    asset = get_asset(asset_id)
    if asset and (asset["description"] or "").strip():
        sources.append({"origin": "asset", "asset": asset["name"], "column": None,
                        "text": asset["description"]})
    for column in get_columns(asset_id):
        if column["name"] == column_name or not (column["description"] or "").strip():
            continue
        sources.append({"origin": "sibling", "asset": asset_id, "column": column["name"],
                        "text": column["description"]})
    for upstream_id in upstream_assets(asset_id):
        for column in get_columns(upstream_id):
            if not (column["description"] or "").strip():
                continue
            sources.append({"origin": "upstream", "asset": upstream_id,
                            "column": column["name"], "text": column["description"]})
    return sources


def update_description(asset_id: str, column_name: str | None, description: str | None) -> None:
    value = description.strip() if description and description.strip() else None
    with connect() as db:
        if column_name:
            cursor = db.execute(
                "UPDATE columns SET description = ? WHERE asset_id = ? AND name = ?",
                (value, asset_id, column_name),
            )
        else:
            cursor = db.execute(
                "UPDATE assets SET description = ? WHERE id = ?", (value, asset_id)
            )
        if cursor.rowcount == 0:
            raise LookupError("Asset or column not found")
