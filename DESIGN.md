# Design notes

## The questions this measures

Existing metadata quality metrics answer *"is a description present?"*. Contextprobe now runs two separate experiments:

- The **automatic mutation path** asks whether a model tracks an exact claim when that claim is preserved, reversed, removed, or surrounded by neutral noise.
- The **legacy fixture path** asks whether a description is sufficient for known operational questions and whether the model recognises when it is not.

In both, the unit under test is **the metadata context**, not the agent. The model is the instrument and never the final judge.

## Proof-carrying mutation path

`POST /api/playground` accepts a column name, optional data type, description, execution mode, and bounded padding count. It does not accept client-authored questions, expected answers, markers, or operators.

The compiler scans clause boundaries and accepts exactly one registered reversible operator: include/exclude (with explicit inflections), before/after, or with/without. A proof carries the exact evidence and mutation spans, source SHA-256, controlled opposite, and deterministic ID. It then creates original, flipped, removed, and padded variants with expected labels `SUPPORTED`, `CONTRADICTED`, `NOT_STATED`, and `SUPPORTED`.

Every transform is replayed and validated before execution. Ambiguous, repeated, unsupported, or malformed claims produce diagnostics rather than invented tests. The endpoint is stateless and does not touch SQLite.

This path measures grounding, sensitivity, abstention discipline, and noise robustness. Because its source may itself be false, it does not establish factual truth.

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

Both paths are deterministic and independent of the model, but they use different evidence.

### Automatic mutation grading

The expected label comes from transform replay. Model output must exactly equal `SUPPORTED`, `CONTRADICTED`, or `NOT_STATED`; partial matches and prose become `INVALID`. The deterministic simulator classifies exact claim and controlled-opposite witnesses in the actual variant and never reads the expected label. Provider fallbacks are labelled and excluded from `model_only_score`.

This removes keyword-answer grading for compiled claims without introducing an LLM judge.

### Legacy fixture grading

1. Explicit `abstained` flag, or an abstention phrase in the answer → `abstained`.
2. Otherwise, answer contains any `correct_marker` → `correct`.
3. Otherwise → `confident_wrong`.

Substring markers are fragile. That fragility is contained by `selfcheck.py`, which asserts for all 18 fixture probes that the ground-truth answer matches its own markers and the wrong answer does not. That check caught a real defect during development. This grader supports the historical risk and repair demonstration only.

## The repair gate

Measuring which description is bad is only useful if you can tell whether the fix helped. That is not self-evident: Atlan AI Labs measured a verbose variant of the *same facts* performing 13.8% worse than a concise one. Description edits are not monotonic improvements, so they need a test.

The gate is a dry run. It baselines the asset, composes a candidate from documented text, applies it, re-probes, and restores the original in a `finally` block. A candidate is accepted only when `fixed > 0 and regressed == 0`.

### Generation is blind, diagnosis is not

The composer may only read upstream column descriptions reached through lineage, sibling columns, and the asset description. It never sees `required_terms`. If it did, it would insert the exact tokens the grader matches on and every repair would pass trivially — the same circularity the simulated answerer has to avoid.

After the run, diagnosis *does* use the probe definitions, to distinguish two different rejections: facts absent from the grounding entirely, versus facts present in the candidate but past the salience window.

### The salience window

`SALIENCE_CHARS = 200`. Only the first 200 characters of each description count as reliably visible. This models attention decay, and it exists so the gate can reject padding rather than reward it.

Every seeded description is under 100 characters, so the window changes no baseline result — verified by re-running `measure.py` after introducing it. It is a documented hypothesis, not evidence about real models.

### What counts as a regression

- `confident_wrong -> correct` is **fixed**
- `correct -> anything else` is a **regression**
- `abstained -> confident_wrong` is a **regression** (a safe failure became a dangerous one)
- `confident_wrong -> abstained` is **made_safe** — an improvement, but not enough to accept on its own

Regressions are counted across *all* probes on the asset, not just the target column, because an asset-level probe sees every column description and a rewrite can cause collateral damage.

### Why a dry run, and why that is asserted

`run_probes(persist=False)` keeps dry-run outcomes out of `probe_results`. Without it the risk queue would move during evaluation and a rejected candidate would leave its outcomes behind as though they were real.

Two invariants in `repaircheck.py` enforce this: after any dry run the stored description must be byte-identical, and the `probe_results` row count must be unchanged. Both are asserted rather than assumed.

### Known limitation: answerability is not truth

The accepted candidate for `net_revenue` inherits *"before returns, refunds and tax"* from its sibling `gross_revenue`. That clause is correct for gross revenue and wrong for net revenue, which is measured after refunds. The probe checks keyword presence and cannot detect the inverted polarity, so it passes.

This is the strongest argument for LLM mode, and the reason the gate produces a recommendation for a steward rather than an auto-commit. Committing remains a separate explicit `PATCH`.

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

- Automatic mode attempts the configured real model and visibly falls back per case; those cases are excluded from `model_only_score`. Explicit LLM mode returns HTTP 503 when configuration or a usable provider response is unavailable.
- A playground description with no supported unambiguous claim returns HTTP 200 with an empty proof list, null score, and deterministic diagnostics.
- A required catalog query failure returns HTTP 503 and produces no results, rather than reporting a misleadingly clean queue.
- Probe definitions are upserted on startup, so a corrected legacy suite can never silently grade against a stale seeded row.

## Deliberate trade-offs

- **SQLite over PostgreSQL** — zero setup matters more than scale for the retained 12-asset fixture.
- **Plain functions over an agent framework** — proof construction, context boundaries, and verdict ownership must remain obvious and auditable.
- **Controlled mutation grammar over generated questions** — supported claims require no human-authored answer and carry replayable evidence; unsupported semantics fail empty instead of being guessed.
- **Hand-written probes retained only for the legacy fixture** — their known ground truth preserves the original risk and repair demonstration, but they are not the scaling strategy.
- **Synthetic catalog over a live connector** — reproducibility currently beats a demo that only works on one warehouse.

## Priority roadmap

1. Add provenance-preserving context adapters for types, constraints, glossary definitions, lineage, dbt artifacts, and historical agent traces.
2. Run the same compiled proof set across multiple configured models and preserve per-model labels, failures, and fallback-free scores.
3. Persist versioned baselines keyed by source hash, compiler version, model, and prompt version; diff them on metadata changes for CI regression checks.
4. Add executable agent tasks only after historical tasks, warehouse ground truth, and a safe SQL/tool sandbox exist.
5. Add AI-assisted repair only after external truth evidence and regression checks exist; keep human approval and never auto-commit.

Generated questions and semantic grading remain useful for future task traces that the controlled grammar cannot cover. They require a calibrated evaluator and human-validated held-out set. They do not replace exact mutation grading, and one model must never be allowed to grade another without independent calibration.
