"""
Deploy the loan multi-agent system (Planner + specialists) to Vertex AI Agent
Engine. Scale-to-zero by default (ADR-0004). Human approval is handled by Cloud
Workflows, not the agent runtime.

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
    ap.add_argument("--staging-bucket", required=True)
    args = ap.parse_args()

    import vertexai
    from vertexai import agent_engines
    from vertexai.preview import reasoning_engines
    from agents import root_agent

    if root_agent is None:
        raise SystemExit("google-adk not installed; `pip install -r requirements.txt` first.")

    vertexai.init(project=args.project, location=args.location, staging_bucket=args.staging_bucket)
    app = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=True)
    remote = agent_engines.create(
        app,
        requirements=["google-adk>=0.3.0", "google-cloud-aiplatform[agent_engines]>=1.70.0", "httpx>=0.27.0"],
        # Local modules the pickled agent needs at runtime. gateway_llm defines the
        # GatewayLlm class each agent's `model` is an instance of (ADR-0024) — without it
        # the remote runtime cannot unpickle the agent at all. tools.py holds the tool
        # functions. Same failure mode the agent image's explicit COPY list guards against.
        extra_packages=["gateway_llm.py", "tools.py"],
        display_name="finchat-loan-planner",
        description="Loan underwriting multi-agent system (planner + credit + review + approval + notify).",
    )
    print("Deployed Agent Engine resource:", remote.resource_name)


if __name__ == "__main__":
    main()
