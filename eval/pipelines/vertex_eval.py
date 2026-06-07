"""
Live-agent evaluation on Vertex AI (Gen AI Evaluation Service + Agent Engine).

Where evaluate.py validates the grounding contract + decision logic offline (CI
gate), this evaluates the *deployed* agents with LLM-as-judge metrics. Run after
deploying agents to Agent Engine. Costs Gemini tokens, so it is NOT in CI.

Usage:
    python vertex_eval.py --project strongsville-city-schools --location us-central1
"""
from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(__file__)


def load_jsonl(name):
    with open(os.path.join(HERE, "..", "datasets", name), encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="us-central1")
    args = ap.parse_args()

    import pandas as pd
    import vertexai
    from vertexai.preview.evaluation import EvalTask, MetricPromptTemplateExamples

    vertexai.init(project=args.project, location=args.location)

    # Build an eval dataset: prompt + (agent) response + reference.
    rows = load_jsonl("transaction_agent_eval.jsonl")
    # In practice: query the deployed Agent Engine app per prompt to get responses.
    # from vertexai import agent_engines
    # agent = agent_engines.get(RESOURCE_NAME)
    # response = agent.query(input=r["query"])  -> fill "response"
    df = pd.DataFrame([
        {"prompt": r["query"], "response": "<deployed-agent-response>", "reference": r["reference"]}
        for r in rows
    ])

    task = EvalTask(
        dataset=df,
        metrics=[
            MetricPromptTemplateExamples.Pointwise.GROUNDEDNESS,
            MetricPromptTemplateExamples.Pointwise.INSTRUCTION_FOLLOWING,
            MetricPromptTemplateExamples.Pointwise.VERBOSITY,
            "safety",
            # tool-use trajectory metrics when evaluating via Agent Engine traces:
            # "trajectory_exact_match", "tool_call_quality"
        ],
        experiment="finchat-agent-eval",
    )
    result = task.evaluate()
    print(result.summary_metrics)
    out = os.path.join(HERE, "..", "reports", "vertex_latest.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"summary_metrics": result.summary_metrics}, fh, indent=2, default=str)
    print("report ->", out)


if __name__ == "__main__":
    main()
