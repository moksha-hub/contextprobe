"""End-to-end API check, including the fix-and-re-probe demo loop.

Run with: py backend\\apicheck.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import answerer  # noqa: E402
from app.database import connect, initialize, reset_fixture  # noqa: E402
import app.main as main_module  # noqa: E402
from app.main import app  # noqa: E402

initialize()
reset_fixture()
client = TestClient(app)

health = client.get("/health").json()
assert health["status"] == "ok", health

# A production build serves the SPA without shadowing API routes.
if (main_module.FRONTEND_DIST / "index.html").exists():
    root = client.get("/")
    assert root.status_code == 200, root.text
    assert '<div id="root"></div>' in root.text
    assert client.get("/health").headers["content-type"].startswith("application/json")

# Browser playground: user input is evaluated without touching fixture storage.
def stored_counts() -> tuple[int, int]:
    with connect() as db:
        return (
            db.execute("SELECT COUNT(*) FROM probe_results").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM repairs").fetchone()[0],
        )


playground_before = stored_counts()
playground_payload = {
    "column_name": "net_revenue",
    "description": "Net revenue.",
    "question": "Does net_revenue include tax?",
    "expected_answer": "No. Net revenue excludes tax.",
    "required_terms": "tax, excl",
    "correct_markers": "no, excl",
    "proposed_rewrite": "Net revenue in USD after refunds, excluding tax.",
    "mode": "simulated",
    "downstream_count": 2,
    "certified": True,
}
playground_response = client.post("/api/playground", json=playground_payload)
assert playground_response.status_code == 200, playground_response.text
playground = playground_response.json()
assert playground["original"]["outcome"] == "confident_wrong", playground
assert playground["original"]["risk"] == 4.5, playground
assert playground["rewrite"]["outcome"] == "correct", playground
assert playground["rewrite"]["risk"] == 0.0, playground
assert playground["comparison"]["transition"] == "fixed", playground
assert playground_before == stored_counts(), "playground must not persist results"

single = dict(playground_payload)
single["proposed_rewrite"] = ""
single_response = client.post("/api/playground", json=single)
assert single_response.status_code == 200
assert single_response.json()["rewrite"] is None
assert single_response.json()["comparison"] is None

invalid = dict(playground_payload)
invalid["correct_markers"] = " , "
assert client.post("/api/playground", json=invalid).status_code == 422
invalid = dict(playground_payload)
invalid["downstream_count"] = -1
assert client.post("/api/playground", json=invalid).status_code == 422

# Explicit LLM mode fails clearly when no provider is configured, without a call.
original_llm_available = answerer.llm_available
answerer.llm_available = lambda: False
llm_only = dict(playground_payload)
llm_only["mode"] = "llm"
assert client.post("/api/playground", json=llm_only).status_code == 503
answerer.llm_available = original_llm_available

# Probe the whole catalog.
run = client.post("/api/probe", json={"mode": "simulated"}).json()
assert run["engine"] == "simulated"
assert run["probes_run"] == 18, run["probes_run"]

queue = {item["asset_id"]: item for item in run["queue"]}

# 100% covered and still unsafe.
assert queue["dim_customer"]["column_coverage"] == 1.0
assert queue["dim_customer"]["confident_wrong"] == 1
assert queue["dim_customer"]["risk"] > 0

# Documented, but the description admits it is unverified, so the agent abstains.
assert queue["legacy_customers"]["column_coverage"] == 1.0
assert queue["legacy_customers"]["abstained"] == 1
assert queue["legacy_customers"]["risk"] == 0.0

# 0% covered, zero risk: no description at all, so the agent abstains safely.
assert queue["sandbox_experiments"]["column_coverage"] == 0.0
assert queue["sandbox_experiments"]["abstained"] == 1
assert queue["sandbox_experiments"]["risk"] == 0.0

# Queue is sorted by risk, descending.
risks = [item["risk"] for item in run["queue"]]
assert risks == sorted(risks, reverse=True), risks

# Hidden ground truth must never leak through the read APIs.
detail = client.get("/api/assets/fct_revenue").json()
assert "expected_answer" not in str(detail["probes"])
assert all("wrong_answer" not in probe for probe in detail["probes"])

before = queue["dim_customer"]["risk"]

# The demo loop: rewrite the vague description, re-probe, outcomes should flip.
patch = client.patch(
    "/api/assets/dim_customer/description",
    json={
        "column_name": "region",
        "description": "Shipping region derived from the delivery address; not the billing region.",
    },
)
assert patch.status_code == 200, patch.text

reprobe = client.post("/api/assets/dim_customer/probe", json={"mode": "simulated"}).json()
assert reprobe["summary"]["confident_wrong"] == 0, reprobe["summary"]
assert reprobe["summary"]["correct"] == 1, reprobe["summary"]

after = {item["asset_id"]: item for item in client.get("/api/queue").json()["queue"]}
assert after["dim_customer"]["risk"] == 0.0, after["dim_customer"]
assert after["dim_customer"]["correct"] == 1, after["dim_customer"]

# Error paths.
assert client.get("/api/assets/does_not_exist").status_code == 404
assert client.post("/api/assets/fct_revenue/probe", json={"mode": "nonsense"}).status_code == 422
assert client.patch(
    "/api/assets/fct_revenue/description",
    json={"column_name": "no_such_column", "description": "x"},
).status_code == 404

report = client.get("/api/report").json()
misleading = {item["asset"] for item in report["coverage_vs_risk"]["misleading_coverage"]}
assert "stg_payments" in misleading, misleading

# Repair gate over HTTP.
client.post("/api/reset")
client.post("/api/probe", json={"mode": "simulated"})

accepted = client.post(
    "/api/assets/fct_revenue/repair",
    json={"column_name": "net_revenue", "strategy": "grounded", "mode": "simulated"},
).json()
assert accepted["verdict"] == "accepted", accepted
assert accepted["dry_run"] is True
assert accepted["fixed_count"] >= 1

rejected = client.post(
    "/api/assets/fct_revenue/repair",
    json={"column_name": "net_revenue", "strategy": "verbose", "mode": "simulated"},
).json()
assert rejected["verdict"] == "rejected", rejected
assert "salience" in rejected["reject_reason"]

# A dry run must leave the catalog untouched, even over HTTP.
detail_after = client.get("/api/assets/fct_revenue").json()
net_revenue = next(c for c in detail_after["columns"] if c["name"] == "net_revenue")
assert net_revenue["description"] == "Net revenue.", net_revenue

assert client.post(
    "/api/assets/fct_revenue/repair",
    json={"column_name": "net_revenue", "strategy": "nonsense"},
).status_code == 422
assert client.post(
    "/api/assets/fct_revenue/repair", json={"column_name": "no_such_column"}
).status_code == 404

sweep = client.post("/api/repair", json={"mode": "simulated"}).json()
assert sweep["attempted"] == sweep["accepted"] + sweep["rejected"] + sweep["skipped"]

client.post("/api/reset")
restored = {item["asset_id"]: item for item in client.get("/api/queue").json()["queue"]}
assert restored["dim_customer"]["risk"] == 0.0  # history cleared by reset

print(f"catalog probe: 18 probes, engine={run['engine']}")
print(f"dim_customer risk before fix = {before}, after fix = {after['dim_customer']['risk']}")
print(f"misleading coverage assets: {sorted(misleading)}")
print(f"repair gate: grounded={accepted['verdict']} verbose={rejected['verdict']} "
      f"sweep accepted={sweep['accepted']}/{sweep['attempted']}")
print("\nall API checks passed")
