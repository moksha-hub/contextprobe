# Working in this repo

Contextprobe measures whether an LLM can answer a question using only a catalog
description. The model is the subject of the experiment. Everything that reaches
a verdict about the model is ordinary deterministic code. Most rules below exist
to keep that boundary intact, because every time it slipped, the project started
reporting numbers that were not true.

## Run these before saying you are done

```cmd
py backend\selfcheck.py     :: fixture and grader invariants
py backend\repaircheck.py   :: repair gate behaviour and dry-run safety
py backend\apicheck.py      :: full API surface
py -m compileall backend\app
cd frontend && npm run build
```

None of them need an API key. If you changed anything under `backend/app`, all of
them must pass, not just the one closest to your change. `measure.py` regenerates
the numbers quoted in README section 4.

## Invariants that must not be broken

- **No model may grade a model.** The legacy fixture uses hidden ground-truth
  markers and substring matching in `grader.py`. The mutation playground uses
  exact fixed labels plus replayed span and transform proofs in `playground.py`.
  Neither path may ask another LLM whether an answer was right.
- **A dry run leaves nothing behind.** `repair.py` temporarily writes a candidate
  description and restores it in a `finally` block, and it runs probes with
  `persist=False`. Both are asserted in `repaircheck.py`. Do not "simplify" either.
- **The rewrite drafter never sees the probe questions.** It may only reuse text
  already documented on upstream or sibling columns. If it could see the
  questions it would insert the exact words the grader looks for and every
  rewrite would pass, which would make the gate meaningless.
- **Simulated output is never presented as real-model evidence.** The `simulated`
  engine encodes one hypothesis and that hypothesis was falsified. Keep every
  table labelled with the engine that produced it.
- **Fallbacks are excluded, not counted.** When a provider call fails, the probe
  falls back to the simulator. A study must drop those rather than report them as
  model behaviour. A smaller honest sample beats a contaminated full one.
- **Ground truth stays hidden from the read APIs.** `expected_answer` and
  `wrong_answer` must never appear in an endpoint response.

## Things that have already gone wrong here

Read these before touching the grader or the study scripts.

- `"not deducted"` contains `"deduct"`, so a wrong answer was graded correct.
  Grading is substring matching and it is fragile. If you change it, add a test
  that fails first.
- Probes were seeded with `INSERT OR IGNORE`, so corrections never reached an
  existing database. They are upserted now.
- A required fact matched inside an asset **name**, giving the catalog credit for
  an identifier instead of documentation.
- The abstention list had `"not specified"` but not `"does not specify"`, so a
  real model's safe refusal was scored as confidently wrong and inflated risk.
- 10 of 18 probes silently fell back to the simulator and were counted as model
  results.

## Numbers that are quoted outside this repo

`repaircheck.py` pins the sweep at 1 accepted of 3 attempted. That figure appears
in a résumé and in application answers. If a fixture or gate change moves it, fix
the claim or fix the change. Do not loosen the assertion to make the suite pass.

## Secrets and the environment

- `.env` is gitignored and must stay that way. Never paste a key into code, a
  test, a commit message, or documentation.
- `start_contextprobe.cmd` defaults to simulation-only via
  `CONTEXTPROBE_SIMULATION_ONLY=true`, so a stale key cannot be spent by accident.
  Real-model mode is opt-in with `start_contextprobe.cmd real`.
- This is a local single-user demonstration. There is no auth, no multi-tenancy,
  and SQLite is single-writer. Do not add a network-exposed deployment path
  without also adding access control.

## Style

Small and boring. No new dependency unless it removes real work. Comments explain
why a constraint exists, not what the line does. When you find a defect, write the
test that catches it in the same change.

## What honesty means in this repo

The README keeps a result that contradicts the project's original hypothesis, and
states plainly that the repair gate tests answerability rather than truth. Do not
tidy those away, soften them, or replace a measured limitation with a confident
claim. The limitations section is the most load-bearing part of the documentation.
