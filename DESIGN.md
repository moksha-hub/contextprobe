# Design notes

## The question this measures

Existing metadata quality metrics answer *"is a description present?"*. Contextprobe answers *"is the description sufficient for an agent to act on, and does the agent recognise when it isn't?"*

The unit under test is **the metadata**, not the agent. The agent is the instrument.

## Why three outcomes instead of two

A pass/fail evaluation hides the distinction that matters operationally:

- An agent that says *"the metadata doesn't say"* has failed **safely**. A human loses a few seconds.
- An agent that answers decisively and wrongly has failed **dangerously**. A dashboard gets built on the wrong number and nobody knows.

Both are "not correct". Only one belongs in a risk score. So `confident_wrong` is scored and `abstained` is not.

`over_abstention` is tracked separately: abstaining when the metadata *did* contain the answer is a usability miss, not a safety failure.

## Why the answerer is split in two

A portfolio project that requires an API key to run cannot be demonstrated reliably. A project that fakes an LLM and calls the result evidence is dishonest.

So there are two engines with different epistemic status:

| Engine | What it is | What it proves |
|---|---|---|
| `simulated` | Deterministic behavioural model | The harness works; results are reproducible |
| `llm` | Real OpenAI-compatible model | Whether the hypothesis holds for that model |

The simulated engine's rules are stated in `answerer.py` and applied in a fixed order:

1. All required facts present in context → answer correctly.
2. No description at all → abstain.
3. Description declares its own uncertainty (`unverified`, `tbd`, `see owner`, ...) → abstain.
4. Description present but missing the fact → commit to the plausible wrong answer.

Rule 3 encodes a design lesson worth stating: **metadata that admits its own incompleteness is safer than metadata that sounds authoritative and is vague.** `legacy_customers` demonstrates it.

## Grading

Deterministic and independent of the model:

1. Explicit `abstained` flag, or an abstention phrase in the answer → `abstained`.
2. Otherwise, answer contains any `correct_marker` → `correct`.
3. Otherwise → `confident_wrong`.

Substring markers are fragile. That fragility is contained by `selfcheck.py`, which asserts for all 18 probes that the ground-truth answer matches its own markers and the wrong answer does not. That check caught a real defect during development.

## Risk model

```text
risk = confident_wrong_rate × (1 + downstream_assets) × certified_weight
```

- **Rate, not count** — so an asset with many probes is not penalised for being thoroughly tested.
- **Downstream count** via breadth-first search over `lineage_edges` — blast radius.
- **Certified weight 1.5** — a certified asset carries an implicit promise, so breaking it costs more.

Deliberately excluded from the score: description length, description presence, usage counts. Those are proxies. Probe failure is the direct observation.

## Fixture design

The fixture is engineered to make the comparison falsifiable rather than flattering:

- `net_revenue` and `gross_revenue` sit in the same asset with the **same number of matched probes** covering the **same four facts**. The only variable is description quality.
- Distractors exist in both directions: assets with 100% coverage and real risk (`dim_customer`), and assets with 0% coverage and no risk (`legacy_customers`, `sandbox_experiments`).
- `fct_revenue` is certified, so the weighting path is exercised.

## Data model

- `assets` — name, type, description, owner, certified, deprecated
- `columns` — per-asset column descriptions
- `lineage_edges` — directed upstream → downstream
- `probes` — question, required facts, ground truth, wrong answer, grading markers
- `probe_results` — engine, outcome, answer, and the exact context string the answerer saw

Storing `context_seen` per result is what makes a finding auditable after the fact: you can see precisely which text produced the wrong answer.

## Failure behaviour

- LLM request has a 30-second timeout; HTTP, JSON, or empty-answer failures fall back per-probe to the simulated engine, and the response reports `llm_fallbacks`.
- A required catalog query failure returns HTTP 503 and produces no results, rather than reporting a misleadingly clean queue.
- Probe definitions are upserted on startup, so a corrected suite can never silently grade against a stale seeded row.

## Deliberate trade-offs

- **SQLite over PostgreSQL** — zero setup matters more than scale for a 12-asset fixture.
- **Plain functions over an agent framework** — the context boundary must be obvious and auditable.
- **Hand-written probes over generated ones** — ground truth quality beats probe volume at this size.
- **Synthetic catalog over a live connector** — reproducibility beats a demo that only works on one warehouse.

## If this went to production

Replace the fixture with a catalog connector; derive probes from real logged agent questions instead of hand-writing them; calibrate the risk weights against incidents actually caused by bad metadata; use semantic grading with a held-out probe set to resist keyword stuffing; run probes on a schedule and treat a rising `confident_wrong` rate as metadata drift; and route the queue to asset owners rather than a shared dashboard.
