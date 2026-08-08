"""Fixture and grader invariants. Run with: py backend\\selfcheck.py

These guard the two ways a probe suite can quietly lie:
  1. a ground-truth answer that its own grader would mark wrong
  2. a wrong answer that its own grader would mark correct
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import PROBES, initialize, reset_fixture  # noqa: E402
from app.grader import matches_expected  # noqa: E402
from app.report import paired_comparison  # noqa: E402
from app.runner import run_probes  # noqa: E402

failures: list[str] = []

for probe_id, _asset, _column, question, required, expected, wrong, markers in PROBES:
    if not matches_expected(expected, markers):
        failures.append(f"{probe_id}: expected answer does not match its own markers")
    if matches_expected(wrong, markers):
        failures.append(f"{probe_id}: wrong answer wrongly matches a correct marker")
    if not required:
        failures.append(f"{probe_id}: no required terms declared")

ids = [probe[0] for probe in PROBES]
if len(ids) != len(set(ids)):
    failures.append("duplicate probe ids")

initialize()
reset_fixture()
outcome = run_probes(None, "simulated")
summary = outcome["summary"]

pair = {item["column"]: item for item in paired_comparison()["columns"]}
vague, qualified = pair["net_revenue"], pair["gross_revenue"]

if not (vague["has_description"] and qualified["has_description"]):
    failures.append("paired columns must both be documented for a fair comparison")
if qualified["confident_wrong"] != 0:
    failures.append("qualified description should not produce confident wrong answers")
if vague["confident_wrong"] <= qualified["confident_wrong"]:
    failures.append("vague description should produce more confident wrong answers")
if summary["probes"] != len(PROBES):
    failures.append("not every probe ran")

print(f"probes={summary['probes']} correct={summary['correct']} "
      f"abstained={summary['abstained']} confident_wrong={summary['confident_wrong']}")
print(f"net_revenue  correct={vague['correct']} confident_wrong={vague['confident_wrong']}")
print(f"gross_revenue correct={qualified['correct']} confident_wrong={qualified['confident_wrong']}")

if failures:
    print("\nFAILED")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("\nall invariants passed")
