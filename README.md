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

Measured on the seeded fixture with the simulated answerer (`py backend\measure.py`). 18 probes: 8 correct, 3 abstained, 7 confident wrong.

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

## The two answerers

**`simulated`** (default, no API key needed) is a deterministic behavioural model. It is not a language model and does not pretend to be one. It encodes one explicit, falsifiable hypothesis:

> A description that is present but missing the needed fact invites a confident guess. A description that is absent, or that declares its own uncertainty, produces an abstention.

**`llm`** (optional) sends the same context to a real OpenAI-compatible model, so the hypothesis above can be **tested rather than assumed**. If a real model abstains on a vague description where the simulated engine guesses, that is a genuine falsification of the hypothesis for that probe — and the harness will show it.

This separation is deliberate: the reproducible engine makes the demo runnable and deterministic; the LLM engine makes the claim testable.

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

```cmd
set LLM_API_KEY=your-key
set LLM_MODEL=your-compatible-model
set LLM_BASE_URL=https://api.openai.com/v1
```

Never commit keys. If the provider fails or returns unusable JSON, each probe falls back to the simulated engine and the response reports `llm_fallbacks`.

## Verify

```cmd
py backend\selfcheck.py
py backend\apicheck.py
py backend\measure.py
py -m compileall backend\app
cd frontend && npm run build
```

`apicheck.py` drives the whole API with FastAPI's test client, including the fix-and-re-probe loop, queue ordering, 404/422 paths, and a check that hidden ground truth never leaks through the read endpoints.

`selfcheck.py` asserts the invariants that would let a probe suite quietly lie: every ground-truth answer must satisfy its own grader, and no wrong answer may match a correct marker. Writing that check caught a real bug — `"not deducted"` contains `"deduct"`, so one wrong answer was being graded correct.

## API

- `GET /api/queue` — risk-ranked repair queue
- `GET /api/assets/{id}` — columns, probes, per-column breakdown, latest results
- `POST /api/assets/{id}/probe` — probe one asset (`{"mode":"auto"}` or `"simulated"`)
- `POST /api/probe` — probe the whole catalog, returns the refreshed queue
- `PATCH /api/assets/{id}/description` — edit an asset or column description
- `GET /api/report` — coverage-vs-risk comparison and the controlled pair
- `POST /api/reset` — restore seeded descriptions, clear probe history
- `GET /health` — status and whether an LLM is configured

## Demo path

1. Click **Probe whole catalog**.
2. Read the controlled pair: two documented columns, 0/4 vs 4/4 correct.
3. Open `dim_customer` — 100% coverage, top of the risk queue at 4.00.
4. Read the probe: the agent confidently claimed `region` was the billing region. The metadata just says `"Region."`
5. In **Fix and re-probe**, rewrite it: `"Shipping region derived from the delivery address; not the billing region."`
6. Save. The outcome flips to correct and the risk drops to 0.00.
7. Open `legacy_customers` — also 100% covered, but it declares itself unverified, so the agent abstained. Risk 0.00. Honest incompleteness beats confident vagueness.

## Limitations

- The catalog and ground truth are synthetic; 12 assets and 18 probes demonstrate the method, not industry generalization.
- Ground truth is author-written, so the benchmark and the system share assumptions.
- The simulated engine is a hypothesis about model behaviour, **not evidence** about real models. Only LLM mode produces evidence, and only for the model tested.
- Grading uses substring markers on short answers. This is why `selfcheck.py` exists; it would not scale to long free-form responses without semantic matching.
- Probes are hand-written per column. Schema-derived probe generation would miss business context a human knows to ask about.
- Risk weights (1.5× for certified) are chosen, not learned.
- A description could be gamed by stuffing required keywords. A held-out probe set would be needed to resist that.
- No auth, no multi-tenancy, no real warehouse or catalog connector, no migrations.

## Design decisions, and what I rejected

**Three outcomes instead of pass/fail.** A binary evaluation collapses "I don't know" into the same bucket as a wrong answer. Operationally those are opposites: one costs a human a few seconds, the other puts a wrong number on an executive dashboard. Keeping them apart is the single decision the rest of the design follows from.

**Only `confident_wrong` scores.** Abstention contributes exactly zero risk. This is why `sandbox_experiments` — no description at all — sits at 0.00 while a fully documented `dim_customer` tops the queue.

**Rate, not count.** An asset with more probes would otherwise look worse simply for being tested more thoroughly.

**Ground truth is the omitted fact, never "the metadata is empty."** I got this wrong first. A probe whose expected answer was *"the metadata does not say"* became invalid the moment a steward improved the description — the test broke during the fix-and-re-probe check. Ground truth now describes the real fact the catalog fails to convey, so a probe stays valid across edits.

**Names excluded from fact detection.** `P_SANDBOX` initially passed because the required term `"experiment"` appeared in the asset name `sandbox_experiments`. Crediting a catalog for a word in an identifier is exactly the failure this tool exists to expose.

**Probes are upserted, not `INSERT OR IGNORE`.** A corrected probe suite must never grade against a stale seeded row. My marker fix silently didn't apply until I changed this.

**Rejected:** an LLM judge (it would make the model both subject and judge); embeddings for grading (unjustifiable complexity at 18 probes with short answers); a real warehouse connector (reproducibility matters more here than realism); a vector store, an agent framework, and multi-tenancy (none earn their weight at this scale).

## Prior art and what is different

Atlan's published [metadata feedback loop](https://blog.atlan.com/community/metadata-feedback-loop-context-layer/) captures **human** signals — a thumbs-down and a reason — and their post closes by asking openly what the loop looks like when agents close it too, naming an agent's failed query and hallucinated answer as the signals still to capture. Their [Sherlock post](https://blog.atlan.com/engineering/loop-engineering-in-production-putting-ai-agents-on-call/) states the related constraint plainly: confidence is not accuracy.

Contextprobe is one small answer to that open question. Instead of waiting for a human to report confusion, it provokes the failure on purpose, before deployment, and ranks the metadata by how much damage the failure would do. It is far smaller than anything in production — the contribution is the unit of measurement, not the scale.

## License

MIT
