# Contextprobe

**Find the metadata that will break an AI agent — before the agent breaks.**

A data catalog can report 92% description coverage and still hand an agent a description so vague that the agent invents an answer. Coverage counts whether a description exists. It cannot tell these apart:

```text
net_revenue    "Net revenue."                                        <- covered, unusable
gross_revenue  "Gross revenue in USD before returns, refunds and
                tax; recognized at order date."                      <- covered, usable
```

Contextprobe asks a different question. Not *"does this asset have a description?"* but *"can an agent answer a real question using only this description — and does it know when it can't?"*

## System design

![Contextprobe system design blueprint](docs/architecture.svg)

The two dashed zones are the point of the whole design. Anything inside the red zone is allowed to be wrong, because it is being measured. Everything inside the green zone decides what the answer *means*, and it is all ordinary deterministic code.

![Contextprobe internals, scoring and measured output](docs/internals.svg)

![Contextprobe repair gate](docs/repair-gate.svg)

## What it does

1. Assembles exactly the metadata a production agent would receive for an asset or column — nothing else.
2. Asks a small set of probe questions that have pre-written ground truth.
3. Grades three outcomes, not two.
4. Ranks assets by the damage a confident wrong answer would cause.

### The three outcomes

| Outcome | Meaning |
|---|---|
| `correct` | Answered, and matched ground truth |
| `abstained` | Declined to answer. **Safe** when the metadata genuinely lacked the fact |
| `confident_wrong` | Answered decisively and was wrong. **This is the dangerous one** |

That third bucket is the product. A vague description does not merely fail to help; it invites confident invention. Only `confident_wrong` feeds the risk score.

### The risk score

```text
risk = confident_wrong_rate × (1 + downstream_assets) × (1.5 if certified else 1.0)
```

A weak description on an unused sandbox table barely matters. The same weakness on a column feeding a certified executive dashboard matters a great deal. Weights are chosen for this demo and are **not** calibrated on production data.

## Input and output

### Input 1 — the catalog fixture

A seeded 12-asset retail/revenue catalog in `backend/app/database.py`: assets, columns, descriptions, ownership, certification flags, and directed lineage edges. Descriptions are deliberately mixed — some qualified, some single-word, some absent.

### Input 2 — the probe suite

18 probes, each with ground truth. Ground truth is the **real fact the metadata omits**, never a statement about the metadata's current emptiness — otherwise a probe would stop being valid the moment a steward improved the description:

```python
("P_NET_TAX", "fct_revenue", "net_revenue",
 "Does net_revenue include tax?",
 ["tax"],                                  # facts the answer requires
 "No. Tax is excluded from net_revenue.",  # ground truth
 "Yes, net_revenue includes tax.",         # the plausible wrong guess
 ["exclud", "no"])                         # grading markers
```

### What the answerer sees

Only this. No ground truth, no probe hints:

```json
{
  "asset": "fct_revenue",
  "asset_description": "Revenue table.",
  "certified": true,
  "columns": [{"name": "net_revenue", "type": "decimal", "description": "Net revenue."}]
}
```

### Output 1 — graded probe results

```json
{
  "probe_id": "P_NET_TAX",
  "question": "Does net_revenue include tax?",
  "answer": "Yes, net_revenue includes tax.",
  "outcome": "confident_wrong",
  "facts_present_in_context": false,
  "over_abstention": false,
  "expected_answer": "No. Tax is excluded from net_revenue.",
  "reason": "vague_description"
}
```

### Output 2 — the risk-ranked repair queue

Measured on the seeded fixture with the **simulated** answerer (`py backend\measure.py`). 18 probes: 8 correct, 3 abstained, 7 confident wrong.

> These risk values come from the simulated behavioural model. Measured against a real model the confident-wrong count drops from 7 to 1, so **these numbers overstate the danger** — see [the study](#measured-against-a-real-model--the-hypothesis-was-falsified). The ranking mechanism is what this table demonstrates, not a calibrated risk level.

| Asset | Coverage | Correct | Abstained | Wrong | Downstream | Risk |
|---|---:|---:|---:|---:|---:|---:|
| dim_customer | 100% | 0 | 0 | 1 | 3 | 4.00 |
| fct_revenue | 75% | 4 | 1 | 5 | 2 | 2.25 |
| stg_payments | 100% | 2 | 0 | 1 | 3 | 1.33 |
| legacy_customers | 100% | 0 | 1 | 0 | 0 | 0.00 |
| stg_customers | 100% | 1 | 0 | 0 | 4 | 0.00 |
| stg_orders | 100% | 1 | 0 | 0 | 3 | 0.00 |
| sandbox_experiments | 0% | 0 | 1 | 0 | 0 | 0.00 |

Read the top and bottom rows together:

- `dim_customer` and `stg_payments` are **100% covered and still unsafe**.
- `legacy_customers` is also 100% covered — but its description says *"Unverified legacy field, see owner before use."* The agent abstained. Risk 0.00.
- `sandbox_experiments` has **0% coverage and zero risk**: no description at all, so the agent abstained, and nothing depends on it.

Coverage would have ranked those four in close to the wrong order. It rewards `dim_customer` and penalises `sandbox_experiments`, when the opposite is true for agent safety.

### Output 3 — the controlled pair

Two columns, same asset, both documented, four matched probes each:

| Column | Documented | Correct | Confident wrong |
|---|---|---:|---:|
| `net_revenue` | Yes | 0/4 | 4/4 |
| `gross_revenue` | Yes | 4/4 | 0/4 |

Column coverage scores these identically.

## The repair gate

Finding the bad description is half the job. The other half is knowing whether an edit actually helped — and that is not obvious, because **a longer description can be worse**.

Atlan AI Labs measured exactly that: their enhanced metadata lifted SQL accuracy 38% (522 evaluations, p < 0.0001), but the *verbose* variant of the same facts performed [13.8% worse and cost 52% more](https://atlan.com/know/enhanced-metadata-improves-query-accuracy/), because prose written for humans reads as noise to a model.

So a description edit needs a test, not a hope:

```text
POST /api/assets/{id}/repair    ->  strictly a dry run

1 baseline    probe the asset                       (persist=False)
2 ground      collect upstream + sibling clauses via lineage
3 compose     grounded | verbose | narrow
4 apply       write the candidate temporarily
5 re-probe    same suite                            (persist=False)
6 restore     in a finally block, always
7 verdict     ACCEPTED  <=>  fixed > 0 AND regressed == 0
```

### Generation is blind to the probe suite

The composer never sees `required_terms`. If it did, it would insert the exact tokens the grader looks for and every repair would pass by construction. It may only draw on documented text: upstream column descriptions reached through lineage, sibling columns, and the asset description. Diagnosis *after* the run may use the probe definitions; generation may not.

### Three strategies, because a gate that only accepts proves nothing

| Strategy | Verdict | Why |
|---|---|---|
| `grounded` | ACCEPTED | 175 chars, 3 probes fixed, 0 regressed |
| `verbose` | REJECTED | 504 chars — the facts are present but fall past the 200-char salience window |
| `narrow` | REJECTED | on `gross_revenue`, drops the refund and order-date facts: 2 regressions |

The salience window models attention decay. Every description in the fixture is under 100 characters, so it changes no baseline result — it only catches padded rewrites. Like the simulated answerer, it is a documented hypothesis grounded in Atlan's published regression, not evidence about real models.

### Measured sweep

`POST /api/repair` attempts a grounded repair on every column that currently fails:

| Column | Verdict |
|---|---|
| `fct_revenue.net_revenue` | ACCEPTED — 3 fixed |
| `dim_customer.region` | rejected |
| `stg_payments.payment_status` | rejected |

**Accept rate 1/3.** Both rejections are correct: `region` needs "shipping versus billing" and `payment_status` needs its allowed values, and neither fact exists anywhere in the catalog. No lineage-grounded rewrite can supply them — a human has to. The gate says so instead of shipping confident filler.

That result supports Atlan's own guidance that [people are better editors than authors](https://docs.atlan.com/product/capabilities/governance/context-agents-studio/best-practices/enrich-metadata-at-scale), with a measurement rather than an intuition.

### What the gate does not do

It verifies answerability, not truth. The accepted candidate inherits *"before returns, refunds and tax"* from `gross_revenue` — correct there, and semantically **wrong** for net revenue, which is measured *after* refunds. The probe passes on keyword presence and cannot see the inverted polarity.

That is the sharpest argument for LLM mode: only a model reading the sentence could catch it. Until then the verdict is a recommendation for a steward, never an auto-commit. Committing stays a separate explicit `PATCH`.

## Measured against a real model — the hypothesis was falsified

The simulated engine encodes one claim: **a present-but-vague description invites a confident guess.** I tested it rather than assuming it, against `inclusionai/ling-3.0-tiny` via OpenRouter, temperature 0, two studies.

It does not hold.

| | simulated (18 probes) | real model (26 evaluations) |
|---|---:|---:|
| correct | 8 | 15 |
| abstained | 3 | 10 |
| **confident wrong** | **7** | **1** |

On **every** probe where the simulated engine predicted a confident guess, the real model declined instead:

```text
P_NET_TAX         sim: confident_wrong  ->  model: abstained
P_NET_REFUND      sim: confident_wrong  ->  model: abstained
P_NET_CCY         sim: confident_wrong  ->  model: abstained
P_NET_RECOG       sim: confident_wrong  ->  model: abstained
P_REGION_SCHEME   sim: confident_wrong  ->  model: abstained
P_REV_GRAIN       sim: confident_wrong  ->  model: abstained
P_STATUS_VALUES   sim: confident_wrong  ->  model: abstained
```

Asked *"Does net_revenue include tax?"* with only `"Net revenue."` to work from, the model answered: *"The metadata does not specify whether net_revenue includes tax."* That is the correct, safe response — and better behaviour than my rule predicted.

**So the risk scores computed by the simulated engine overstate the danger.** The seeded `4.00` for `dim_customer` reflects a modelling assumption, not measured model behaviour. That is now stated wherever those numbers appear.

### Two findings that came out of being wrong

**A vague description may be safer than an absent one.** In the first study, `P_RECOG_MEAN` — a column with *no* description at all — was the one probe where the model guessed rather than declined. A vague description at least signals that documentation exists and does not cover the question. A missing one appears to invite invention. That is the opposite direction from my hypothesis, and it inverts the intuition that some documentation is always better than none.

**Self-declared uncertainty was read correctly.** On `legacy_customers.email` (*"Unverified legacy field, see owner before use"*), the simulated engine abstains by rule. The model went further and answered correctly that the field should not be used — reading the hedge as a governance signal rather than a gap.

### Honest limits on this study

- One small free-tier model. Says nothing about GPT-4o, Claude, or Gemini.
- 26 usable evaluations out of 36 attempted. The free tier returned 42 rate-limit responses across both studies; retries recovered most, 11 exhausted. Probes without model data are **excluded** from the totals rather than filled in with simulated output.
- Two probes were unstable across runs (`P_GROSS_REFUND`, `P_LEGACY_USE`), which is itself a signal that those descriptions are ambiguous.
- Fixing this exposed a real grader bug: the model said *"does not specify"* while my abstain phrases only matched *"not specified"*. Reading an abstention as a confident wrong answer would have inflated the one number this project asks anyone to act on.

Reproduce with `py backend\llmstudy.py 2`. The default fixture results stay on the simulated engine so they remain reproducible without an API key.

## The two answerers

**`simulated`** (default, no API key needed) is a deterministic behavioural model. It is not a language model and does not pretend to be one. It encodes one explicit, falsifiable hypothesis:

> A description that is present but missing the needed fact invites a confident guess. A description that is absent, or that declares its own uncertainty, produces an abstention.

**`llm`** (optional) sends the same context to a real OpenAI-compatible model, so the hypothesis above can be **tested rather than assumed**. It was, and it failed — see the study above.

This separation is deliberate: the reproducible engine makes the demo runnable and deterministic without a key; the LLM engine makes the claim falsifiable. The adapter negotiates JSON mode down when a provider rejects `response_format`, grades prose replies, and retries rate limits with backoff, so a provider limitation never gets silently recorded as a model result.

## Architecture

```text
React: repair queue + probe results + fix-and-re-probe
                    |
                 FastAPI
                    |
              probe runner
              /           \
   deterministic grader   answerer (simulated | llm)
                    |
   SQLite: assets, columns, lineage, probes, results
```

The model is the **subject under test**, never the judge. Grading, lineage traversal, blast-radius maths and risk ranking are all deterministic.

### Request path, end to end

A single `POST /api/probe` does this, once per probe, in one pass:

1. `catalog.build_context()` assembles the asset description and the relevant column description. Nothing else. No ground truth, no probe hint, no neighbouring column.
2. `catalog.context_text()` flattens that to lowercase text — **descriptions only**. Asset and column *names* are deliberately excluded, because a fact that appears only in an identifier is not documentation.
3. `answerer.answerable()` records, deterministically, whether the required facts were actually present. This is what later distinguishes a safe abstention from an unnecessary one.
4. The answerer produces `{answer, abstained, reason}` — either the simulated behavioural model or a real LLM.
5. `grader.grade()` assigns one of three outcomes. The model's own confidence is never consulted.
6. `risk.save_results()` persists the outcome **together with the exact description string the answerer saw**, so any finding stays auditable afterwards.

Then `risk.risk_queue()` groups the newest result per probe, counts outcomes per asset, walks the lineage graph for a downstream count, and sorts by risk descending.

### Where each module sits

| Module | Responsibility | Trust |
|---|---|---|
| `database.py` | Schema, the seeded fixture, upserts, reset | deterministic |
| `catalog.py` | Context assembly, BFS lineage, coverage, edits | deterministic |
| `answerer.py` | The two engines under test | **probabilistic** |
| `grader.py` | Three-way outcome, over-abstention flag | deterministic |
| `risk.py` | Scoring, ranking, per-column breakdown | deterministic |
| `report.py` | Coverage-vs-risk, the controlled pair | deterministic |
| `runner.py` | Orchestration, per-probe LLM fallback | deterministic |
| `main.py` | HTTP surface, validation, 503 on store failure | deterministic |

## Run locally on Windows

Requires Python 3.10+, Node.js 20+, npm.

```cmd
py -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --reload
```

In a second terminal:

```cmd
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. API docs at `http://localhost:8000/docs`.

### Optional: probe with a real model

Copy `.env.example` to `.env` (gitignored) and fill in any OpenAI-compatible provider:

```ini
LLM_API_KEY=your-key
LLM_MODEL=inclusionai/ling-3.0-tiny:free
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_DELAY_SECONDS=1.5
```

Then:

```cmd
py backend\llmcheck.py     :: connectivity and JSON-mode support
py backend\llmstudy.py 2   :: run the suite twice, compare with the hypothesis
```

Never commit keys. If a provider fails, each probe falls back to the simulated engine and the response reports `llm_fallbacks` — and `llmstudy.py` excludes those probes from its model totals rather than passing simulated output off as a model result.

## Verify

```cmd
py backend\selfcheck.py    :: fixture and grader invariants
py backend\repaircheck.py  :: repair gate behaviour + dry-run safety
py backend\apicheck.py     :: full API surface
py backend\measure.py      :: the numbers in this README
py -m compileall backend\app
cd frontend && npm run build
```

None of these need an API key. `llmcheck.py` and `llmstudy.py` do.

`repaircheck.py` asserts the gate's behaviour and two safety invariants: after any dry run the stored description must be byte-identical, and the `probe_results` row count must be unchanged. A repair tool that silently mutates the catalog, or leaves dry-run outcomes behind as if they were real, is worse than no repair tool.

`apicheck.py` drives the whole API with FastAPI's test client, including the fix-and-re-probe loop, queue ordering, 404/422 paths, and a check that hidden ground truth never leaks through the read endpoints.

`selfcheck.py` asserts the invariants that would let a probe suite quietly lie: every ground-truth answer must satisfy its own grader, and no wrong answer may match a correct marker. Writing that check caught a real bug — `"not deducted"` contains `"deduct"`, so one wrong answer was being graded correct.

## API

- `GET /api/queue` — risk-ranked repair queue
- `GET /api/assets/{id}` — columns, probes, per-column breakdown, latest results
- `POST /api/assets/{id}/probe` — probe one asset (`{"mode":"auto"}` or `"simulated"`)
- `POST /api/probe` — probe the whole catalog, returns the refreshed queue
- `POST /api/assets/{id}/repair` — propose a rewrite and gate it (dry run, never commits)
- `POST /api/repair` — attempt a grounded repair on every failing column, returns the accept rate
- `PATCH /api/assets/{id}/description` — edit an asset or column description
- `GET /api/report` — coverage-vs-risk comparison and the controlled pair
- `POST /api/reset` — restore seeded descriptions, clear probe and repair history
- `GET /health` — status and whether an LLM is configured

## Demo path

1. Click **Probe whole catalog**.
2. Read the controlled pair: two documented columns, 0/4 vs 4/4 correct.
3. Open `dim_customer` — 100% coverage, top of the risk queue at 4.00.
4. Read the probe: the agent confidently claimed `region` was the billing region. The metadata just says `"Region."`
5. In **Fix and re-probe**, rewrite it: `"Shipping region derived from the delivery address; not the billing region."`
6. Save. The outcome flips to correct and the risk drops to 0.00.
7. Open `legacy_customers` — also 100% covered, but it declares itself unverified, so the agent abstained. Risk 0.00. Honest incompleteness beats confident vagueness.

Then the gate, on `fct_revenue`:

8. In **Repair gate**, pick `net_revenue`, strategy `grounded`, and propose. It composes a rewrite from upstream text, fixes 3 probes, regresses none, and stamps ACCEPTED.
9. Switch to `verbose` and propose again. Same facts, 504 characters, REJECTED — buried past the salience window. This is Atlan's 13.8% verbose regression reproduced in miniature.
10. Pick `gross_revenue` with `narrow`. REJECTED for regression: it drops the refund and order-date facts that two passing probes relied on.

## Limitations

- The catalog and ground truth are synthetic; 12 assets and 18 probes demonstrate the method, not industry generalization.
- Ground truth is author-written, so the benchmark and the system share assumptions.
- The simulated engine is a hypothesis about model behaviour, and testing it against a real model **falsified it**: the confident-wrong count fell from 7 to 1. Its risk scores overstate the danger and should be read as a demonstration of the ranking mechanism, not as calibrated risk.
- The real-model study covers one small free-tier model and 26 usable evaluations. It is not a generalisation about language models.
- Grading uses substring markers on short answers. This is why `selfcheck.py` exists; it would not scale to long free-form responses without semantic matching.
- Probes are hand-written per column. Schema-derived probe generation would miss business context a human knows to ask about.
- Risk weights (1.5× for certified) are chosen, not learned.
- A description could be gamed by stuffing required keywords. A held-out probe set would be needed to resist that.
- The repair gate verifies answerability, not truth: an inherited clause can be correct for its source column and wrong for the target, and keyword-presence grading cannot see the difference.
- The salience window is a chosen threshold modelling attention decay, not a measured property of any specific model.
- Repair grounding is limited to what the catalog already documents, so a fact that exists nowhere cannot be recovered — by design, and the reason the accept rate is 1 in 3.
- No auth, no multi-tenancy, no real warehouse or catalog connector, no migrations.

## Design decisions, and what I rejected

**Three outcomes instead of pass/fail.** A binary evaluation collapses "I don't know" into the same bucket as a wrong answer. Operationally those are opposites: one costs a human a few seconds, the other puts a wrong number on an executive dashboard. Keeping them apart is the single decision the rest of the design follows from.

**Only `confident_wrong` scores.** Abstention contributes exactly zero risk. This is why `sandbox_experiments` — no description at all — sits at 0.00 while a fully documented `dim_customer` tops the queue.

**Rate, not count.** An asset with more probes would otherwise look worse simply for being tested more thoroughly.

**Ground truth is the omitted fact, never "the metadata is empty."** I got this wrong first. A probe whose expected answer was *"the metadata does not say"* became invalid the moment a steward improved the description — the test broke during the fix-and-re-probe check. Ground truth now describes the real fact the catalog fails to convey, so a probe stays valid across edits.

**Names excluded from fact detection.** `P_SANDBOX` initially passed because the required term `"experiment"` appeared in the asset name `sandbox_experiments`. Crediting a catalog for a word in an identifier is exactly the failure this tool exists to expose.

**Probes are upserted, not `INSERT OR IGNORE`.** A corrected probe suite must never grade against a stale seeded row. My marker fix silently didn't apply until I changed this.

**The repair composer may not see the probe suite.** Generating from `required_terms` would insert the exact tokens the grader matches and make every repair pass by construction. Grounding is restricted to upstream and sibling descriptions, so the probes stay an independent judge — which is why the honest accept rate is 1 in 3 rather than 3 in 3.

**Repairs are dry runs with an explicit commit step.** The gate restores the original description in a `finally` block and never writes to `probe_results`. Both are asserted in `repaircheck.py`, not just documented.

**Rejected:** an LLM judge (it would make the model both subject and judge); embeddings for grading (unjustifiable complexity at 18 probes with short answers); a real warehouse connector (reproducibility matters more here than realism); auto-committing accepted repairs (answerability is not truth — a steward has to confirm the wording); a vector store, an agent framework, and multi-tenancy (none earn their weight at this scale).

## Prior art, verified

I checked this against Atlan's published material rather than assuming novelty. Two things came back.

**Measuring metadata quality by probing an agent is not new — Atlan already published it.** [Atlan AI Labs](https://atlan.com/know/enhanced-metadata-improves-query-accuracy/) ran 174 queries three times each (522 evaluations) on a 13-table Formula One dataset, holding the model constant and varying only metadata quality. Win rate went from 16.1% to 22.2%: a 38% relative lift at p < 0.0001, with a 2.15x gain on medium-complexity queries. Their illustrative failure is almost this project's `net_revenue` case — an agent asked which drivers were eliminated, found no `eliminated` column, and produced confident, plausible, wrong SQL.

So this project does not claim that idea. What differs is narrower:

| | Atlan (published) | Contextprobe |
|---|---|---|
| Unit measured | aggregate lift of a whole context bundle | per column, attributed |
| Output | proof that metadata investment pays | a ranked repair queue |
| Outcome classes | binary query win rate | correct / safely abstained / confidently wrong |
| Prioritisation | query complexity, consumption layer | failure rate × downstream blast radius |
| After measuring | — | a gate that verifies the rewrite |

**The blind spot is real and current.** Four separate Atlan doc pages state that context agents only enrich assets *missing* the target attribute and that existing values are never overwritten ([FAQ](https://docs.atlan.com/product/capabilities/governance/context-agents-studio/faq/metadata-enrichment), [concepts](https://docs.atlan.com/product/capabilities/governance/context-agents-studio/concepts/agents), [how-to](https://docs.atlan.com/product/capabilities/governance/context-agents-studio/how-tos/enrich-metadata-on-asset-collection), [best practices](https://docs.atlan.com/product/capabilities/governance/context-agents-studio/best-practices/enrich-metadata-at-scale)). The same FAQ defines coverage as a fill-rate — 60 of 100 assets with a description gives 60% — and notes that collections count parent assets, not columns. Their troubleshooting entry for "the agent runs but generates 0 descriptions" gives the cause as every asset already having one.

Put together: `"Net revenue."` counts as fully covered, is skipped by enrichment by design, and is only caught if a human happens to get confused and report it. That is the population this project targets, and the repair gate is what lets an edit to it be verified rather than trusted.

Their [metadata feedback loop](https://blog.atlan.com/community/metadata-feedback-loop-context-layer/) captures human signals today, and that post closes by asking openly what the loop looks like when agents close it too — naming a hallucinated answer as a signal still to capture. Their [Sherlock post](https://blog.atlan.com/engineering/loop-engineering-in-production-putting-ai-agents-on-call/) states the related constraint plainly: confidence is not accuracy.

This is far smaller than anything they run in production. The contribution is the unit of measurement and the verification step, not the scale.

## License

MIT
