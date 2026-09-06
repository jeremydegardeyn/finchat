# Process API

Cross-domain business capabilities: the middle layer of API-led connectivity
([ADR-0030](../../../docs/adr/0030-api-led-layering.md), [docs/28](../../../docs/28-api-led-layering.md)).

```bash
PORT=8090 python main.py
curl 'http://localhost:8090/v1/customers/by-account/acct-001/overview'
```

With no `TXN_API_URL` / `LOAN_API_URL` it reuses the two system APIs' own demo
repositories, so it runs with no GCP access. `GET /healthz` reports which.

| Variable | Default | Effect |
|---|---|---|
| `TXN_API_URL` | unset | unset → in-process demo data |
| `LOAN_API_URL` | unset | unset → in-process demo data |
| `PROCESS_RECENT_LIMIT` | `5` | recent transactions fetched per overview |

## What belongs here

Composition across domains, and **decisions**. `next_action` is the example: a business
rule that both the web and mobile channels need, which would otherwise be implemented
twice and drift.

## What does not

Schema, dataset names, SQL. This layer reaches data only through the system APIs that own
it — `requirements.txt` has no BigQuery client, and `scripts/test_api_layering.py`
(LAYER-2) fails the build if one appears.
