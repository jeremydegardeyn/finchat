#!/usr/bin/env python3
"""
AI unit economics — cost per **successful** task, by workload class (docs/22).

Why this and not cost per token
-------------------------------
Cost per token is available from any billing export and answers nothing a CFO asked.
"We spent $4,000 on inference" invites one question — *did it work?* — and token counts
cannot answer it. Cost per successful task can, because it needs both halves:

  * cost      — from the gateway audit (`input_tokens`, `output_tokens`, tier, model)
  * success   — from the eval harness (`conversation_scores.overall`, LLM-judge)

FinChat is unusual in having both, joined on a shared correlation key: the BFF passes a
turn id to the gateway as `session_id` and writes the same value as `conversation_id`.
Without that key there is no join, and the metric quietly degrades back to cost per token.

Success threshold
-----------------
A turn counts as successful at `overall >= SUCCESS_THRESHOLD` (default 0.7 on the
normalized 0..1 composite). The threshold is a judgement call and is reported alongside
every figure, because a unit cost quoted without its success definition is not comparable
to anything — including its own previous quarter.

Pricing
-------
`PRICES` below are **placeholders** and are marked as such in the output. Per-token list
prices change, differ by region and tier, and are exactly the kind of number that should
not be invented in code. Populate them from the current price list (or an account-team
quote) before quoting a dollar figure to anyone; the arithmetic and the joins are correct
regardless, and `--tokens-only` skips money entirely.

Usage
-----
    python scripts/unit_economics.py dev
    python scripts/unit_economics.py dev --days 30 --tokens-only
    python scripts/unit_economics.py dev --sql        # print the SQL and exit
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = os.getenv("GCP_PROJECT", "strongsville-city-schools")
GATEWAY_DATASET = os.getenv("GATEWAY_BQ_DATASET", "ai_gateway")
GATEWAY_TABLE = os.getenv("GATEWAY_BQ_TABLE", "requests")

SUCCESS_THRESHOLD = float(os.getenv("SUCCESS_THRESHOLD", "0.7"))

# USD per 1M tokens. PLACEHOLDERS — see the module docstring. Keyed by the model id the
# gateway records, with a fallback so an unrecognised model still costs *something*
# rather than silently costing zero (a zero-cost model is the most dangerous default in
# a spend report).
PRICES = {
    "gemini-2.5-flash": {"input": 0.0, "output": 0.0},
    "gemini-2.5-pro": {"input": 0.0, "output": 0.0},
    "_default": {"input": 0.0, "output": 0.0},
}
PRICES_CONFIGURED = any(p["input"] or p["output"] for k, p in PRICES.items())


def sql(env: str, days: int) -> str:
    """Join gateway cost to eval outcome on the shared correlation key.

    LEFT JOIN, deliberately: a turn with no score is *unjudged*, not unsuccessful.
    Counting unjudged turns as failures would make the metric fall whenever the scorer
    lags, which is a scheduling artefact rather than a quality signal — so they are
    reported in their own column instead.
    """
    return f"""
WITH cost AS (
  SELECT
    session_id,
    agent_id,
    workload_class,
    tier,
    model,
    SUM(COALESCE(input_tokens, 0))  AS input_tokens,
    SUM(COALESCE(output_tokens, 0)) AS output_tokens
  FROM `{PROJECT}.{GATEWAY_DATASET}.{GATEWAY_TABLE}`
  WHERE outcome = 'ok'
    AND surface = 'complete'
    AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
    AND session_id IS NOT NULL
  GROUP BY session_id, agent_id, workload_class, tier, model
),
quality AS (
  SELECT conversation_id, overall
  FROM `{PROJECT}.finchat_eval_{env}.conversation_scores`
  WHERE scored_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
)
SELECT
  c.workload_class,
  c.model,
  COUNT(*)                                                   AS tasks,
  COUNTIF(q.overall IS NULL)                                 AS unjudged,
  COUNTIF(q.overall >= {SUCCESS_THRESHOLD})                  AS successful,
  SUM(c.input_tokens)                                        AS input_tokens,
  SUM(c.output_tokens)                                       AS output_tokens,
  ROUND(AVG(q.overall), 3)                                   AS avg_quality
FROM cost c
LEFT JOIN quality q ON q.conversation_id = c.session_id
GROUP BY c.workload_class, c.model
ORDER BY tasks DESC
""".strip()


def price(model: str) -> dict:
    return PRICES.get(model, PRICES["_default"])


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p = price(model)
    return (input_tokens / 1_000_000) * p["input"] + (output_tokens / 1_000_000) * p["output"]


def report(rows: list[dict], tokens_only: bool) -> None:
    print(f"\n== AI unit economics — success threshold {SUCCESS_THRESHOLD} "
          f"(overall score, 0..1) ==\n")
    if not rows:
        print("  no governed traffic in the window.")
        print("  Cost lives in the gateway audit and quality in conversation_scores;")
        print("  a turn appears here only if it transited the gateway (see docs/23).")
        return

    header = f"  {'workload class':<22}{'tasks':>7}{'ok':>6}{'unjudged':>10}{'quality':>9}"
    header += f"{'tokens':>12}"
    if not tokens_only:
        header += f"{'cost':>10}{'per ok task':>14}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    tot_tasks = tot_ok = tot_tok = 0
    tot_cost = 0.0
    for r in rows:
        toks = (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        ok = r["successful"] or 0
        line = (f"  {r['workload_class']:<22}{r['tasks']:>7}{ok:>6}"
                f"{r['unjudged']:>10}{(r['avg_quality'] if r['avg_quality'] is not None else '—'):>9}"
                f"{toks:>12,}")
        if not tokens_only:
            c = cost_usd(r["model"], r["input_tokens"] or 0, r["output_tokens"] or 0)
            per = (c / ok) if ok else None
            line += f"{('$%.4f' % c):>10}" + (f"{('$%.4f' % per):>14}" if per is not None
                                              else f"{'n/a':>14}")
            tot_cost += c
        print(line)
        tot_tasks += r["tasks"]
        tot_ok += ok
        tot_tok += toks

    print("  " + "-" * (len(header) - 2))
    total = f"  {'TOTAL':<22}{tot_tasks:>7}{tot_ok:>6}{'':>10}{'':>9}{tot_tok:>12,}"
    if not tokens_only:
        per = (tot_cost / tot_ok) if tot_ok else None
        total += f"{('$%.4f' % tot_cost):>10}" + (f"{('$%.4f' % per):>14}" if per is not None
                                                  else f"{'n/a':>14}")
    print(total)

    if not tokens_only and not PRICES_CONFIGURED:
        print("\n  ⚠ PRICES ARE PLACEHOLDERS (all zero). Populate PRICES in this script from")
        print("    the current per-token price list before quoting any dollar figure.")
        print("    Task counts, success rates and token volumes above are real.")

    unjudged = sum(r["unjudged"] for r in rows)
    if unjudged:
        pct = round(100 * unjudged / tot_tasks) if tot_tasks else 0
        print(f"\n  {unjudged} of {tot_tasks} tasks ({pct}%) are unjudged — counted as neither")
        print("  success nor failure. A high number here means the scorer is lagging, not")
        print("  that quality fell.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Cost per successful task, by workload class.")
    ap.add_argument("env", nargs="?", default="dev")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--tokens-only", action="store_true",
                    help="skip dollar figures (use until PRICES are populated)")
    ap.add_argument("--sql", action="store_true", help="print the query and exit")
    args = ap.parse_args()

    query = sql(args.env, args.days)
    if args.sql:
        print(query)
        return 0

    print(f"AI unit economics — {args.env}, last {args.days}d  ({date.today().isoformat()})")
    try:
        from google.cloud import bigquery
        rows = [dict(r) for r in bigquery.Client(project=PROJECT).query(query).result()]
    except Exception as e:
        print(f"\nquery failed: {type(e).__name__}: {e}")
        print("\nThis needs the gateway audit table and the eval dataset to both exist.")
        print("Inspect the query with --sql.")
        return 1

    report(rows, args.tokens_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
