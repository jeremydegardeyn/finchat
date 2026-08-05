# 22 — AI Unit Economics

> Cost per **successful** task, by workload class — not cost per token.
>
> Implementation: [`scripts/unit_economics.py`](../scripts/unit_economics.py).
> Depends on the gateway ([ADR-0024](adr/0024-enterprise-ai-gateway.md)) for cost and the
> eval harness ([ADR-0015](adr/0015-live-evaluation.md)) for success.

## The measure

Cost per token is available from any billing export and answers nothing that was asked.
*"We spent $4,000 on inference last month"* invites exactly one question — **did it
work?** — and token counts cannot answer it.

Cost per successful task can, because it requires both halves:

| Half | Source | What it contributes |
|---|---|---|
| Cost | Gateway audit (`ai_gateway.requests`) | input/output tokens, tier, model, per agent |
| Success | `finchat_eval_<env>.conversation_scores` | LLM-judge composite score per turn |

Most platforms have one or the other. Having both, and joining them, is what makes this
metric available at all — which is why it is worth building rather than buying a
dashboard.

## The join

The two halves live in different systems, so they need a shared key. The BFF generates a
turn id, passes it to the gateway as `session_id`, and writes the same value as
`conversation_id` in the eval log.

```
BFF turn ──┬─ gateway /v1/complete   (session_id)     → tokens, tier, model
           └─ conversation_log       (conversation_id) → question, answer
                     ↓ LLM-judge
              conversation_scores    (conversation_id) → overall 0..1
```

Without that key the metric silently degrades back to cost per token, which is the
failure mode to watch for: it does not error, it just stops being interesting.

## Two decisions that shape the number

**Unjudged turns are not failures.** The join is a `LEFT JOIN` and unscored turns are
reported in their own column. Counting them as failures would make the metric drop
whenever the scorer lags — a scheduling artefact presented as a quality signal. Expect
the `evaluation` workload class to be almost entirely unjudged: the judge is not itself
judged, which is a [known gap](19-model-inventory.md#known-gaps-tracked-not-yet-closed),
not a bug in this report.

**The success threshold is quoted with every figure.** A turn counts as successful at
`overall >= 0.7` on the normalized composite. That is a judgement call, and a unit cost
quoted without its success definition is not comparable to anything — including its own
previous quarter.

## Running it

```bash
python scripts/unit_economics.py dev --days 7
python scripts/unit_economics.py dev --tokens-only    # no dollar figures
python scripts/unit_economics.py dev --sql            # inspect the query
```

```
  workload class          tasks    ok  unjudged  quality      tokens
  ------------------------------------------------------------------
  tool_calling_agent        412   351        38     0.86   2,242,351
  classification           1204  1188         0     0.97     250,432
  evaluation                390     0       390        —     968,500
```

The shape of that table is the argument. `classification` is the highest-volume workload
and the cheapest per task; `tool_calling_agent` is the reverse. That is the tiering case
made with numbers instead of assertion — and it is why the gateway clamps
`classification` to the standard tier ([ADR-0024](adr/0024-enterprise-ai-gateway.md)).

## Prices are placeholders

`PRICES` in the script is zeroed and the report says so loudly whenever dollar figures are
requested. Per-token list prices change, differ by region and tier, and are precisely the
kind of number that should not be invented in code and then quoted upward as if verified.

Task counts, success rates, quality scores and token volumes are real without them. Fill
`PRICES` from the current price list or an account-team quote before putting a dollar
figure in front of anyone.

The unrecognised-model fallback is deliberately a real entry rather than a zero default:
**a model that costs nothing is the most dangerous default in a spend report**, because it
makes unattributed traffic look free.

## Known limits

- **Only governed traffic appears.** A task shows up here only if it transited the
  gateway. With 3 of 6 call sites transiting ([docs/23](23-gateway-transit.md)), this is a
  view of part of the platform — and by volume, the smaller part. Reading it as total AI
  spend would understate materially.
- **Quality is a proxy.** "Successful" means an LLM judge scored the turn above a
  threshold, with all the known biases that carries — position, verbosity, and
  self-enhancement, since the judge shares a model family with what it scores.
- **Cost is inference only.** No serving infrastructure, storage, or engineering time. The
  number is a unit cost of inference per successful task, not a total cost of ownership,
  and should be labelled that way when reported.
- **Batch and caching discounts are not modelled.** Batch is ~50% off across major
  providers and cache reads are heavily discounted, but on Gemini the two do not stack.
  Any savings projection built on this report has to model that explicitly.
