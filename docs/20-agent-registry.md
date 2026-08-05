# 20 — Agent Registry & Identity

> Every agent FinChat runs: what it is, who owns it, what it may touch, which identity it
> acts as, and when that identity was last recertified. Where
> [19 — Model & Agent Inventory](19-model-inventory.md) registers what *reasons*, this
> registers what *acts*.
>
> Decision and rationale: [ADR-0023](adr/0023-agent-registry-and-identity.md).

## Why this exists

Revised model risk guidance (**SR 26-2 / OCC 2026-13**, April 2026) places generative and
agentic AI outside supervisory model-risk scope, and no US bank has published a reference
architecture for an agent registry, agent identity scheme, or agent gateway. Nothing
external requires this document. It exists because incident response and examination both
ask the same question — *who authorised this agent to take this action, and who is
accountable for it* — and that question has to be answerable at any point, from evidence
rather than from memory.

## The registry

Source of truth: [`scripts/agents_catalog.py`](../scripts/agents_catalog.py).
Everything below is generated from it.

| Agent | Product | Risk | Identity | Tools (allow-list) | Consequential | HITL |
|---|---|:-:|---|---|:-:|:-:|
| `banking_assistant` | transactions | HIGH | `…-agent-banking-assi` | balance · history · summary · loan status · knowledge base | no | no |
| `loan_planner` | loans | HIGH | `…-agent-loan-planner` | — (coordinates sub-agents) | no | yes |
| `credit_agent` | loans | HIGH | `…-agent-credit` | `generate_credit_profile` | no | yes |
| `transaction_review_agent` | loans | HIGH | `…-agent-txn-review` | `get_overdraft_history` | no | yes |
| `approval_agent` | loans | HIGH | `…-agent-approval` | `compute_risk` | no | yes |
| `notification_agent` | loans | MEDIUM | `…-agent-notification` | `send_notification` | **yes** | yes |
| `steward_planner` | steward | MEDIUM | `…-agent-steward-plan` | `read_findings` | no | no |
| `steward_generator` | steward | MEDIUM | `…-agent-steward-gene` | — (proposes text only) | no | yes |
| `steward_evaluator` | steward | MEDIUM | `…-agent-steward-eval` | — (judge gate) | no | yes |
| `analyst_data_agent` | analyst | MEDIUM | end-user credentials · SA fallback | `conversational_analytics_chat` | no | no |

Current state is also queryable:

```sql
SELECT agent_id, owner, risk_tier, recert_due, tools
FROM `strongsville-city-schools.finchat_platform_dev.agent_registry`
ORDER BY recert_due
```

**One agent is consequential.** `notification_agent` sends a message to a real customer;
everything else reads, reasons, or recommends. That asymmetry is deliberate and is why the
`consequential` flag exists — it is the field that decides whether a human gate is
mandatory, and CI enforces the pairing.

## Identity: distinct, and used by impersonation

Each agent has its own service account. ADK sub-agents share a process, so the runtime
cannot *be* five identities simultaneously — instead it holds no agent privileges of its
own and mints short-lived credentials for the acting agent
(`roles/iam.serviceAccountTokenCreator`). FinChat already uses this pattern for the
anonymous analyst tier ([ADR-0019](adr/0019-end-user-credential-propagation.md)).

```
Cloud Run service (runtime SA — no agent privileges)
   └─ credit_agent acts
        └─ impersonate finchat-<env>-agent-credit
             └─ tool call runs under the credit agent's identity
                  └─ agent_action_log row: agent_id, service_account, owner, tool, authorized
```

The analyst agent is the exception worth knowing: it runs under the **signed-in analyst's
own credentials**, so BigQuery evaluates that person's IAM and policy tags rather than a
platform identity. Its service account is a fallback for when credential propagation is
unavailable.

## Lifecycle

| Tier | Recertification | Gate behaviour |
|---|---|---|
| HIGH | 90 days | overdue **fails the build**; ≤14 days warns |
| MEDIUM | 180 days | same |
| LOW | 365 days | same |

The cadence matches privileged human access on purpose. Several of these agents can reach
production financial data; the case for treating them as a lesser category is weak.

Deprovisioning is a status change in the catalogue (`status: retired`), which removes the
agent from the Terraform projection and therefore destroys its service account. An agent
cannot outlive its registration.

## The gate

[`scripts/verify_agent_registry.py`](../scripts/verify_agent_registry.py) parses agent
source with `ast` and compares it to the registry. It runs as its own CI job.

| Check | Fails the build when |
|---|---|
| `DRIFT-1` | an agent is constructed in code but not registered |
| `DRIFT-2` | a registered agent no longer exists in code |
| `DRIFT-3` | code grants a tool the registry never approved |
| `REG-1` | owner, business area, risk tier, identity or data scope missing |
| `REG-2` | two agents share a service account |
| `REG-3` | consequential action with no human-in-the-loop gate |
| `LIFE-1` | recertification overdue |
| `PIN-1` | *(warning only)* the agent runs a floating model alias |

**`DRIFT-3` is the control.** Granting an agent a new tool is a permission change; here it
cannot happen without a registry diff carrying a named owner. The gate's own tests
([`test_agent_registry.py`](../scripts/test_agent_registry.py)) exist to prove it fails
when it should — a control that can't be shown to fail isn't one.

```bash
python scripts/verify_agent_registry.py --env prod
pytest scripts/test_agent_registry.py -q
```

## Evidence

`finchat_platform_<env>.agent_action_log` — append-only, day-partitioned, clustered by
agent — records for every action: the acting identity, the accountable owner at the time,
the tool, **whether the tool was inside the allow-list**, the verified human approver where
a gate applied, and the model version that actually served
([ADR-0022](adr/0022-model-version-pinning.md)).

```sql
-- Every consequential action last week, and who was accountable for it.
SELECT ts, agent_id, owner, tool, authorized, hitl_approver, model_version
FROM `strongsville-city-schools.finchat_platform_dev.agent_action_log`
WHERE consequential AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
ORDER BY ts DESC
```

```sql
-- Anything an agent invoked outside its registered allow-list.
SELECT agent_id, tool, COUNT(*) AS calls
FROM `strongsville-city-schools.finchat_platform_dev.agent_action_log`
WHERE NOT authorized
GROUP BY 1, 2 ORDER BY calls DESC
```

## Operating it

```bash
# 1. Change the registry (the only place agent facts are authored)
$EDITOR scripts/agents_catalog.py

# 2. Regenerate the Terraform projection — CI fails if this is stale
for e in dev test prod; do
  python scripts/agents_catalog.py --env $e --emit-tfvars infra/envs/$e/agents.auto.tfvars.json
done

# 3. Apply (creates/destroys per-agent service accounts + impersonation grants)
cd infra/envs/dev && terraform apply

# 4. Publish the queryable registry (refuses to publish if verification fails)
python scripts/agent_registry_bootstrap.py dev
```

## Known limits

- **The scanner sees ADK constructors and declared function entrypoints.** A dynamically
  constructed agent, or one built by a framework FinChat does not parse, would not trip
  `DRIFT-1`. Registry completeness rests on the gate covering how agents are actually
  written here, and that assumption needs re-checking whenever the agent framework changes.
- **Enforcement is at build time, not yet at run time.** `DRIFT-3` stops an unregistered
  tool shipping; it does not stop one being invoked. The `authorized` column exists so
  runtime enforcement has somewhere to land, but the runtime check is not implemented.
- **The managed analyst agent is registered, not governed by this mechanism.** Its version
  and behaviour are controlled by Gemini Data Analytics; the registry records it and its
  perimeter, and nothing more.
- **Impersonation is wired in Terraform; the per-agent token exchange in the ADK tool path
  is not yet implemented.** Identities, grants, and evidence schema are real today;
  agents currently still execute under the runtime SA until that path lands.
