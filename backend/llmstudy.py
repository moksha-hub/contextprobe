"""Run the probe suite against a real model and compare it with the hypothesis.

The simulated engine encodes one claim: a present-but-vague description invites a
confident guess. This script tests that claim instead of assuming it.

Run with: py backend\\llmstudy.py [runs]
"""

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import env  # noqa: E402

env.load()

from app import answerer  # noqa: E402
from app.answerer import llm_available  # noqa: E402
from app.database import initialize, reset_fixture  # noqa: E402
from app.runner import run_probes  # noqa: E402

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
LLM_REASONS = {"llm_json", "llm_prose"}

initialize()
reset_fixture()

if not llm_available():
    print("LLM_API_KEY and LLM_MODEL are not set; nothing to compare.")
    raise SystemExit(1)

baseline = run_probes(None, "simulated", persist=False)
simulated = {r["probe_id"]: r for r in baseline["results"]}

runs: list[dict[str, dict]] = []
for index in range(RUNS):
    started = time.perf_counter()
    result = run_probes(None, "auto", persist=False)
    runs.append({r["probe_id"]: r for r in result["results"]})
    answered = sum(1 for r in result["results"] if r["reason"] in LLM_REASONS)
    print(
        f"run {index + 1}/{RUNS}  {result['summary']['correct']} correct  "
        f"{result['summary']['abstained']} abstained  "
        f"{result['summary']['confident_wrong']} confident wrong  "
        f"({answered}/{len(result['results'])} answered by model, "
        f"{time.perf_counter() - started:.0f}s)"
    )

probe_ids = sorted(simulated)
print()
print(f"{'probe':<18}{'simulated':<16}{'model runs':<26}{'stable':<8}{'verdict'}")
print("-" * 92)

falsified: list[str] = []
confirmed: list[str] = []
unstable: list[str] = []
incomplete: list[str] = []
totals = Counter()

for probe_id in probe_ids:
    # Only genuine model answers count. A silent fallback is simulated output and
    # must never be reported as if the model produced it.
    answered = [run[probe_id] for run in runs if run[probe_id]["reason"] in LLM_REASONS]
    outcomes = [result["outcome"] for result in answered]
    for outcome in outcomes:
        totals[outcome] += 1

    sim = simulated[probe_id]["outcome"]
    if not outcomes:
        incomplete.append(probe_id)
        verdict = "no model data"
        shown = "-"
        stable = "-"
    else:
        stable_flag = len(set(outcomes)) == 1
        stable = "yes" if stable_flag else "NO"
        if not stable_flag:
            unstable.append(probe_id)
        shown = " ".join(outcome[:4] for outcome in outcomes)
        if sim == "confident_wrong" and all(o == "abstained" for o in outcomes):
            falsified.append(probe_id)
            verdict = "FALSIFIED: model declined"
        elif sim == outcomes[0] and stable_flag:
            confirmed.append(probe_id)
            verdict = "matches hypothesis"
        else:
            verdict = f"differs (sim={sim})"
    print(f"{probe_id:<18}{sim:<16}{shown:<26}{stable:<8}{verdict}")

sim_totals = Counter(result["outcome"] for result in simulated.values())
n = len(probe_ids)
graded = sum(totals.values())

print()
print(f"simulated  {n} probes      correct {sim_totals['correct']:>3}  "
      f"abstained {sim_totals['abstained']:>3}  confident_wrong {sim_totals['confident_wrong']:>3}")
print(f"model      {graded} evaluations  correct {totals['correct']:>3}  "
      f"abstained {totals['abstained']:>3}  confident_wrong {totals['confident_wrong']:>3}")

print()
print(f"model answered        {graded}/{n * RUNS} attempted evaluations")
print(f"probes with no data   {len(incomplete)}  {incomplete}")
print(f"unstable across runs  {len(unstable)}  {unstable}")
print(f"matches hypothesis    {len(confirmed)}")
print(f"FALSIFIED             {len(falsified)}  {falsified}")
print()
print("provider call stats:", dict(answerer.stats))
print()
if falsified:
    print("On those probes the simulated engine predicted a confident guess and the")
    print("real model declined instead. The hypothesis does not hold there, and the")
    print("risk score computed from the simulated engine overstates the danger.")
if incomplete:
    print()
    print("Probes with no model data are EXCLUDED from the model totals above.")
    print("Re-run when quota allows before quoting these numbers.")
