# AgentOps — Evaluation Framework

Evaluation datasets, pipelines, and reporting for FinChat's agents — the **AgentOps** discipline that
makes agents production-fit for a regulated bank (measured, gated, monitored).

## The evaluation loops

| Layer | Tool | Cost | When |
|-------|------|------|------|
| **Offline contract + logic gate** | [`pipelines/evaluate.py`](pipelines/evaluate.py) | $0 (no LLM) | every CI run |
| **Own judge over real traffic** | [`../scripts/live_eval.py`](../scripts/live_eval.py) | Gemini tokens | daily, scheduled |
| **Drift canary vs. baseline** | [`../scripts/canary_eval.py`](../scripts/canary_eval.py) | Gemini tokens | scheduled (ADR-0022) |
| **Managed autorater** | [`pipelines/vertex_eval.py`](pipelines/vertex_eval.py) (Gen AI evaluation service) | judge tokens per metric per case | post-deploy, manual dispatch |
| **Classifier accuracy** | [`pipelines/classifier_eval.py`](pipelines/classifier_eval.py) | model calls | nightly (ADR-0034) |
| **Conversation trajectory (ADR-0034)** | [`../ui/safety_signals.py`](../ui/safety_signals.py), gated offline by `datasets/safety_trajectories.jsonl` | one Flash-Lite call / customer turn | every turn, in the request |

The offline harness validates the **grounding contract** (answers only assert tool-sourced facts),
**tool selection**, policy refusals, and the **loan decision logic** vs. labeled ground truth — fast
and free, so it gates merges.

`vertex_eval.py` is the one loop whose judge FinChat does not own. That is its value — a
calibrated autorater nobody here can accidentally tune to flatter the agent — and its cost: the
autorater's version is Google's, so unlike `live_eval.py` it cannot record which judge produced a
score. Hence a separate loop rather than a replacement. It scores the same golden set as the
canary, against the live agent, with:

- **adaptive rubrics** — per-prompt pass/fail tests the service generates (`GENERAL_QUALITY`),
  the one capability none of the hand-rolled loops has;
- a **static rubric** for the refusal policy, with one criterion per rule, derived at runtime from
  the machine-readable frontmatter of
  [`../knowledge/playbooks/refusal-escalation.md`](../knowledge/playbooks/refusal-escalation.md)
  so it cannot fall behind the rules the agents are instructed with. The rubric states which
  **surface** it is auditing, because that SSOT is written for the analyst perimeter: without it the
  judge failed the agent for summarising the caller's own account and for asking which account they
  meant. Giving the SSOT a per-surface applicability field is the real fix and belongs to the
  policy's owner;
- **computation metrics** for tool use (`tool_call_valid`, `tool_name_match`, and the two
  parameter matchers) — free, no judge tokens, and all four **reported rather than gated**
  (see below);
- **`TOOL_USE_QUALITY`** over the ADK event trace.

Only `refusal_policy` is gated here, and the tool metrics deliberately are not. Two things
defeat an absolute threshold on them. `get_transaction_history`'s `limit` is optional, so a
model that correctly reads "my last 3 transactions" off the question and passes `limit: 3`
is marked down by the parameter matchers against a label that omits it. And the golden set
is seven cases, so one case flipping moves any mean by 0.143 — which happened on two
consecutive runs against a single dev revision with identical prompts (`not-found` called
the balance tool, then called nothing). A gate finer than its own measurement resolution
fires on noise, and `canary_eval.py` already records what that costs. Baseline-relative
drift detection on this dataset is the canary's job; this loop reports the numbers and keeps
per-case trajectories in the report so a move can be diagnosed rather than merely noticed.
A refusal breach is categorical, so it stays gated at 1.00.

Not available: the `trajectory_exact_match` family. The SDK's computation handler does not support
those metrics yet (explicit TODO in `_evals_metric_handlers.py`), so ordered-trajectory scoring
stays in `evaluate.py`, which does it deterministically and for free.

This needs the agent to report what it did, not just what it said: `/chat` takes
`include_trajectory`, and returns the tool calls, the tool output (the grounding evidence a
hallucination judge compares against) and the ADK event trace. Off by default — the customer path
proxies that body to the browser verbatim.

Three things about the managed metrics were established by testing them rather than by reading the
docs, and each was wrong on the first attempt:

- **`hallucination_v1` takes its evidence from the PROMPT and nowhere else.** Supplied in a
  `context` column, in `instruction` or in `reference`, a deliberately fabricated balance scored
  1.0 — the metric had nothing to check against, so it treated the answer as its own source and
  returned the response as its own supporting excerpt. In the prompt, the same answer scored 0.0.
  So grounding runs in its own phase whose prompt carries the tool output, over only the cases that
  called a tool. Direction: **1.0 = every sentence supported, 0.0 = fabricated.**
- **A custom `LLMMetric`'s judge output is parsed as JSON.** `MetricPromptBuilder` emits a prose
  template, so the judge answered correctly and the service then failed on
  `Error parsing JSON ... Input: {## Evaluation`, reporting `None` for every case. The template
  asks for strict JSON instead. `return_raw_output` / `parse_and_reduce_fn` exist on the type but
  no handler in 1.153.1 reads them.
- **A custom metric's `judge_model` must be a full publisher resource name.** The SDK passes it
  through verbatim; a bare model id is rejected with "Invalid autorater model resource name".

The judge is also noisy: an unmissable fabrication scored 0.0, 0.0, 0.0, 1.0 across four
single-sample runs, so the gated and grounding metrics use 3-sample majority voting.

## Metrics

| Metric | Definition | Source | Gate |
|--------|------------|--------|------|
| Grounding accuracy | answers whose asserted facts all trace to tool output | offline | ≥ 0.90 |
| Hallucination rate | answers asserting unsupported facts | offline; managed `hallucination_v1`, evidence in the prompt (1.0 = supported) | reported |
| Tool utilization | correct tool selected for the intent | offline; managed `tool_name_match` | ≥ 0.90 |
| Response quality | relevance + policy compliance (refusals, no advice, no cross-customer) | offline; managed `GENERAL_QUALITY` adaptive rubrics | — |
| Refusal compliance | share of turns obeying every applicable platform refusal rule | managed static rubric | = 1.00 |
| Tool-call validity | the call the agent emitted is well-formed | managed `tool_call_valid` | reported |
| Approval rec. accuracy | loan recommendations vs. labeled outcomes | offline | ≥ 0.80 |
| Trajectory escalation accuracy | tier + action per turn vs. scripted expectation, same engine as prod | offline | = 1.00, late = 0 |

## Datasets

- [`datasets/transaction_agent_eval.jsonl`](datasets/transaction_agent_eval.jsonl) — balance/history/summary, missing-id, not-found, advice-refusal, cross-customer.
- [`datasets/loan_eval.jsonl`](datasets/loan_eval.jsonl) — labeled applicant profiles → expected recommendation.
- [`datasets/safety_trajectories.jsonl`](datasets/safety_trajectories.jsonl) — multi-turn trajectories (Raine shape, fiction reset, coached transfer, patient attacker, leak on first probe) → expected tier and action at every turn. Gated at 1.0 and **zero late escalations**.

## Run

```bash
pip install -r requirements.txt               # vertex_eval.py only; evaluate.py needs nothing

python pipelines/evaluate.py                  # offline gate; writes reports/latest.json, non-zero exit on breach

python pipelines/vertex_eval.py --dry-run     # inspect the eval frames; no service calls, no cost
python pipelines/vertex_eval.py --probe       # verify wiring + the tool-call payload shape; $0
python pipelines/vertex_eval.py --agent-url "$AGENT_URL" --metrics tools   # free metrics only
python pipelines/vertex_eval.py --agent-url "$AGENT_URL"                   # full run, costs judge tokens
```

`--probe` is not ceremony. The tool metrics take trajectories as a JSON string whose shape is an
API contract, and no local test can check this repo against the service — `--probe` sends one
identical prediction/reference pair through a free computation metric and fails loudly if the
service did not parse it as a trajectory.

Latest offline run (committed sample): [`reports/latest.json`](reports/latest.json).

## Reporting & dashboard strategy

1. **CI gate** — `evaluate.py` runs in `ci.yml`; a regression below threshold fails the build (quality is a release gate, not an afterthought).
2. **Report artifact** — `reports/latest.json` is the machine-readable scorecard; CI uploads it as a build artifact and (live) writes `vertex_latest.json`.
3. **Trend store** — push each run's `summary` to a BigQuery `eval_results` table (run_id, metric, value, ts) for longitudinal tracking.
4. **Dashboard** — Looker Studio over that table (or Cloud Monitoring custom metrics); the **Admin UI** Evaluations panel surfaces the latest summary to operators.
5. **Alerting** — Monitoring alert when grounding/approval accuracy drops or hallucination rises between runs (model/agent drift).

## Why this matters (regulated banking)

Auditable, versioned, threshold-gated evaluation is what lets a bank put an agent in front of customer
data and credit decisions: it provides evidence of accuracy and demonstrates non-fabrication controls.

For the deterministic loan scorecard this ties to model-risk-management expectations under the
revised guidance (**SR 26-2 / OCC 2026-13**, which rescinded SR 11-7 in April 2026). For the
LLM agents it ties to nothing external — that guidance explicitly places generative and agentic
AI outside its scope, so this harness *is* the control rather than evidence of conformance to
one. See [19 — Model & Agent Inventory](../docs/19-model-inventory.md).
