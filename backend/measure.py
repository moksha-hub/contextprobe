"""Print the measured fixture result used in the README."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import initialize, reset_fixture  # noqa: E402
from app.report import coverage_vs_risk, paired_comparison  # noqa: E402
from app.risk import risk_queue  # noqa: E402
from app.runner import run_probes  # noqa: E402

initialize()
reset_fixture()
summary = run_probes(None, "simulated")["summary"]

print("SUMMARY", summary)
print()
header = f"{'asset':<30}{'cov':>5}{'ok':>4}{'abst':>6}{'wrong':>7}{'down':>6}{'risk':>7}"
print(header)
print("-" * len(header))
for row in risk_queue():
    if not row["probed"]:
        continue
    print(
        f"{row['asset']:<30}{int(row['column_coverage'] * 100):>4}%"
        f"{row['correct']:>4}{row['abstained']:>6}{row['confident_wrong']:>7}"
        f"{row['downstream_assets']:>6}{row['risk']:>7.2f}"
    )

print()
print("MISLEADING COVERAGE (100% covered, still unsafe):")
for row in coverage_vs_risk()["misleading_coverage"]:
    print(f"  {row['asset']}: {row['confident_wrong']}/{row['probes']} wrong, risk {row['risk']}")

print()
print("CONTROLLED PAIR:")
for column in paired_comparison()["columns"]:
    print(
        f"  {column['column']:<16} documented={column['has_description']} "
        f"correct={column['correct']}/{column['probes']} "
        f"confident_wrong={column['confident_wrong']}/{column['probes']}"
    )
