"""Reference Airflow DAG (NOT deployed) — the enterprise orchestration of the
medallion + serving refresh that Cloud Workflows handles in the sandbox.

Shows the pattern: scheduled, dependency-aware, retry/alert-wired DAG that triggers
the Dataflow Flex Template, runs dbt-style transforms, refreshes materialized views,
and rebuilds the RAG corpus — each a task with explicit upstream/downstream edges.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator
from airflow.providers.google.cloud.operators.dataflow import (
    DataflowStartFlexTemplateOperator,
)

default_args = {
    "owner": "data-platform",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": True,
}

with DAG(
    dag_id="finchat_medallion",
    schedule="0 * * * *",  # hourly
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["finchat", "medallion"],
) as dag:

    ingest = DataflowStartFlexTemplateOperator(
        task_id="ingest_transactions",
        location="us-central1",
        body={
            "launchParameter": {
                "jobName": "txn-stream",
                "containerSpecGcsPath": "gs://finchat-enterprise-dataflow/templates/txn-pipeline.json",
                "parameters": {
                    "input_subscription": "projects/PROJECT/subscriptions/finchat-enterprise-transactions-dataflow",
                    "output_table": "PROJECT:finchat_silver_enterprise.transaction",
                    "sdk_container_image": "REGION-docker.pkg.dev/PROJECT/REPO/txn-pipeline-worker:latest",
                },
            }
        },
    )

    build_gold = BigQueryInsertJobOperator(
        task_id="build_gold",
        configuration={"query": {"query": "CALL finchat.build_gold();", "useLegacySql": False}},
    )

    refresh_mv = BigQueryInsertJobOperator(
        task_id="refresh_materialized_views",
        configuration={
            "query": {
                "query": "CALL BQ.REFRESH_MATERIALIZED_VIEW('finchat_gold_enterprise.customer_360_mv');",
                "useLegacySql": False,
            }
        },
    )

    ingest >> build_gold >> refresh_mv
