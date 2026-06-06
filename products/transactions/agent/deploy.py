"""
Deploy the FinChat banking assistant to Vertex AI Agent Engine (ADR-0004).

Scale-to-zero by default (near-zero idle cost); cold start accepted in the
sandbox. Set a warm min-instance pool for enterprise latency SLOs.

Usage:
    python deploy.py --project strongsville-city-schools --location us-central1 \
        --staging-bucket gs://finchat-dev-dataflow
"""
from __future__ import annotations

import argparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="us-central1")
    ap.add_argument("--staging-bucket", required=True, help="gs://... for Agent Engine staging")
    args = ap.parse_args()

    import vertexai
    from vertexai import agent_engines
    from vertexai.preview import reasoning_engines
    from agent import root_agent

    if root_agent is None:
        raise SystemExit("google-adk not installed; `pip install -r requirements.txt` first.")

    vertexai.init(project=args.project, location=args.location, staging_bucket=args.staging_bucket)

    app = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=True)

    remote = agent_engines.create(
        app,
        requirements=[
            "google-adk>=0.3.0",
            "google-cloud-aiplatform[agent_engines]>=1.70.0",
            "httpx>=0.27.0",
        ],
        display_name="finchat-banking-assistant",
        description="Conversational data agent grounded in the transactions data product.",
    )
    print("Deployed Agent Engine resource:", remote.resource_name)


if __name__ == "__main__":
    main()
