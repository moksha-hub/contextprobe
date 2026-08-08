"""End-to-end API check, including the fix-and-re-probe demo loop.

Run with: py backend\\apicheck.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.database import initialize, reset_fixture  # noqa: E402
from app.main import app  # noqa: E402

initialize()
reset_fixture()
client = TestClient(app)

health = client.get("/health").json()
assert health["status"] == "ok", health

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

client.post("/api/reset")
restored = {item["asset_id"]: item for item in client.get("/api/queue").json()["queue"]}
assert restored["dim_customer"]["risk"] == 0.0  # history cleared by reset

print(f"catalog probe: 18 probes, engine={run['engine']}")
print(f"dim_customer risk before fix = {before}, after fix = {after['dim_customer']['risk']}")
print(f"misleading coverage assets: {sorted(misleading)}")
print("\nall API checks passed")
