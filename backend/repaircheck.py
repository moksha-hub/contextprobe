"""Repair gate invariants. Run with: py backend\\repaircheck.py

Two of these are safety invariants. A repair tool that silently mutates the
catalog, or that leaves dry-run outcomes behind as if they were real, is worse
than no repair tool at all.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.catalog import SALIENCE_CHARS, get_columns  # noqa: E402
from app.database import DB_PATH, initialize, reset_fixture  # noqa: E402
from app.repair import repair_all, repair_column  # noqa: E402
from app.runner import run_probes  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def description_of(asset_id: str, column_name: str) -> str | None:
    for column in get_columns(asset_id):
        if column["name"] == column_name:
            return column["description"]
    raise AssertionError("column missing")


def probe_result_count() -> int:
    db = sqlite3.connect(DB_PATH)
    try:
        return db.execute("SELECT COUNT(*) FROM probe_results").fetchone()[0]
    finally:
        db.close()


initialize()
reset_fixture()
run_probes(None, "simulated")

before_description = description_of("fct_revenue", "net_revenue")
before_rows = probe_result_count()

# 1. A grounded rewrite drawn from upstream text clears the gate.
grounded = repair_column("fct_revenue", "net_revenue", "grounded", "simulated")
check(grounded["verdict"] == "accepted", f"grounded should be accepted, got {grounded['verdict']}")
check(grounded["fixed_count"] >= 1, "grounded should fix at least one probe")
check(grounded["regressed_count"] == 0, "grounded must not regress a passing probe")
check(grounded["candidate_length"] <= SALIENCE_CHARS,
      "a grounded candidate should stay inside the salience window")

# 2. Padding the same facts past the salience window is rejected.
#    This reproduces, in miniature, the verbose regression Atlan measured.
verbose = repair_column("fct_revenue", "net_revenue", "verbose", "simulated")
check(verbose["verdict"] == "rejected", f"verbose should be rejected, got {verbose['verdict']}")
check(verbose["candidate_length"] > SALIENCE_CHARS, "verbose candidate should exceed the window")
check("salience" in (verbose["reject_reason"] or ""),
      f"verbose rejection should cite the salience window, got {verbose['reject_reason']!r}")

# 3. A rewrite that drops facts a passing probe relied on is rejected.
narrow = repair_column("fct_revenue", "gross_revenue", "narrow", "simulated")
check(narrow["verdict"] == "rejected", f"narrow should be rejected, got {narrow['verdict']}")
check(narrow["regressed_count"] >= 1, "narrow should regress at least one probe")
check("regression" in (narrow["reject_reason"] or ""),
      f"narrow rejection should cite regression, got {narrow['reject_reason']!r}")

# 4. SAFETY: a dry run never leaves its candidate in the catalog.
check(description_of("fct_revenue", "net_revenue") == before_description,
      "net_revenue description was mutated by a dry run")
check(description_of("fct_revenue", "gross_revenue")
      == "Gross revenue in USD before returns, refunds and tax; recognized at order date.",
      "gross_revenue description was mutated by a dry run")

# 5. SAFETY: a dry run never writes probe results.
check(probe_result_count() == before_rows,
      "a dry run wrote to probe_results and would have moved the risk queue")

# 6. Generation stays blind to the probe suite: no required term is inserted
#    unless it was already present in the documented text it drew on.
grounding_text = " ".join(item["clause"].lower() for item in grounded["grounding_used"])
for word in ("tax", "refund", "usd"):
    if word in grounded["after_description"].lower():
        check(word in grounding_text,
              f"'{word}' appeared in the candidate but not in the grounding it cited")

# 7. Catalog-wide sweep reports an honest accept rate.
sweep = repair_all("simulated")
check(sweep["attempted"] > 0, "sweep should attempt at least one repair")
check(sweep["accepted"] + sweep["rejected"] + sweep["skipped"] == sweep["attempted"],
      "sweep verdict counts must sum to attempts")

print(f"grounded  verdict={grounded['verdict']:<9} fixed={grounded['fixed_count']} "
      f"regressed={grounded['regressed_count']} len={grounded['candidate_length']}")
print(f"verbose   verdict={verbose['verdict']:<9} fixed={verbose['fixed_count']} "
      f"len={verbose['candidate_length']} -> buried")
print(f"narrow    verdict={narrow['verdict']:<9} fixed={narrow['fixed_count']} "
      f"regressed={narrow['regressed_count']}")
print(f"sweep     attempted={sweep['attempted']} accepted={sweep['accepted']} "
      f"rejected={sweep['rejected']} skipped={sweep['skipped']} "
      f"accept_rate={sweep['accept_rate']}")

if failures:
    print("\nFAILED")
    for failure in failures:
        print(f"  - {failure}")
    raise SystemExit(1)
print("\nall repair gate invariants passed")
