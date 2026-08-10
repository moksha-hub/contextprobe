"""Fixture, grader, and proof-compiler invariants.

Run with: py backend\selfcheck.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import PROBES, initialize, reset_fixture  # noqa: E402
from app.grader import matches_expected  # noqa: E402
from app.playground import (  # noqa: E402
    VARIANT_LABELS,
    compile_proofs,
    parse_label,
)
from app.report import paired_comparison  # noqa: E402
from app.runner import run_probes  # noqa: E402

failures: list[str] = []

# Legacy fixture invariants.
for probe_id, _asset, _column, question, required, expected, wrong, markers in PROBES:
    if not matches_expected(expected, markers):
        failures.append(f"{probe_id}: expected answer does not match its own markers")
    if matches_expected(wrong, markers):
        failures.append(f"{probe_id}: wrong answer wrongly matches a correct marker")
    if not required:
        failures.append(f"{probe_id}: no required terms declared")

ids = [probe[0] for probe in PROBES]
if len(ids) != len(set(ids)):
    failures.append("duplicate legacy probe ids")

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
    failures.append("not every legacy probe ran")

# Proof-carrying mutation compiler invariants.
source = "Payment amount in USD, excluding tax; Settled before payout."
proofs, diagnostics = compile_proofs(source, 2)
repeat, repeat_diagnostics = compile_proofs(source, 2)
if diagnostics or repeat_diagnostics:
    failures.append("unambiguous compiler fixture produced diagnostics")
if [proof["id"] for proof in proofs] != [proof["id"] for proof in repeat]:
    failures.append("proof IDs or ordering are not deterministic")
if len(proofs) != 2:
    failures.append("compiler did not extract both controlled claims")

for proof in proofs:
    span = proof["evidence_span"]
    mutation = proof["mutation_span"]
    if source[span["start"]:span["end"]] != span["text"]:
        failures.append(f"{proof['id']}: evidence span is not exact")
    if source[mutation["start"]:mutation["end"]] != mutation["text"]:
        failures.append(f"{proof['id']}: mutation span is not exact")
    variants = proof["variants"]
    if [item["kind"] for item in variants] != ["original", "flip", "remove", "pad"]:
        failures.append(f"{proof['id']}: mutation order changed")
    if {item["kind"]: item["expected_label"] for item in variants} != VARIANT_LABELS:
        failures.append(f"{proof['id']}: mutation labels changed")

ambiguous, ambiguous_diagnostics = compile_proofs("Amount includes refunds without tax.", 1)
if ambiguous or not any(item["code"] == "ambiguous_clause" for item in ambiguous_diagnostics):
    failures.append("ambiguous clauses must be rejected, not guessed through")

duplicated, duplicate_diagnostics = compile_proofs("Values include tax. Values include tax.", 1)
if duplicated or not any(item["code"] == "duplicate_evidence" for item in duplicate_diagnostics):
    failures.append("duplicate evidence must be rejected")

no_match, no_match_diagnostics = compile_proofs("Payment amount in USD.", 1)
if no_match or not any(item["code"] == "no_supported_claim" for item in no_match_diagnostics):
    failures.append("no-match input must return an explicit empty compilation")

for label in ("SUPPORTED", "CONTRADICTED", "NOT_STATED"):
    if parse_label(label) != label:
        failures.append(f"strict parser rejected valid label {label}")
for invalid_label in ("supported", "SUPPORTED.", "The answer is SUPPORTED", ""):
    if parse_label(invalid_label) is not None:
        failures.append(f"strict parser accepted invalid output {invalid_label!r}")

print(f"legacy probes={summary['probes']} correct={summary['correct']} "
      f"abstained={summary['abstained']} confident_wrong={summary['confident_wrong']}")
print(f"net_revenue  correct={vague['correct']} confident_wrong={vague['confident_wrong']}")
print(f"gross_revenue correct={qualified['correct']} confident_wrong={qualified['confident_wrong']}")
print(f"compiler proofs={len(proofs)} variants={sum(len(p['variants']) for p in proofs)}")

if failures:
    print("\nFAILED")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("\nall invariants passed")
