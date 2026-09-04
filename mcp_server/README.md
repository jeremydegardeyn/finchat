# FinChat MCP server

FinChat's capabilities over the Model Context Protocol. Design and rationale:
[ADR-0028](../docs/adr/0028-mcp-as-the-agent-channel.md) ·
[docs/27](../docs/27-mcp-service.md).

## Install locally

```bash
claude mcp add finchat --scope user -- python /path/to/finchat/mcp_server/server.py
```

Requires `pip install mcp`. With nothing else configured it serves the transactions and
loan APIs' own demo repositories, so it runs before any GCP access exists. Point it at
deployed services for real data:

```bash
claude mcp add finchat --scope user \
  --env FINCHAT_TXN_API_URL=https://finchat-dev-txn-api-....run.app \
  --env FINCHAT_LOAN_API_URL=https://finchat-dev-loan-api-....run.app \
  -- python /path/to/finchat/mcp_server/server.py
```

Those services are private. The call carries an OIDC ID token minted from your gcloud
identity, so you need `roles/run.invoker` — and BigQuery's column-level security is
evaluated against **you**, not a shared key ([ADR-0019](../docs/adr/0019-end-user-credential-propagation.md)).
Call `finchat_status` to see which data source is live.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `FINCHAT_TXN_API_URL` | unset | unset → in-process demo data |
| `FINCHAT_LOAN_API_URL` | unset | unset → in-process demo data |
| `FINCHAT_AGENT_URL` | unset | knowledge base via the agent's `/search`; unset → local BM25 |
| `FINCHAT_MCP_PERSONA` | `customer` | `approver` adds `list_loans`, `get_loan_audit` |
| `FINCHAT_MCP_ALLOW_WRITES` | off | enables `submit_loan_application` |
| `FINCHAT_MCP_TRANSPORT` | `stdio` | `http` for streamable-HTTP on Cloud Run |
| `FINCHAT_MCP_ALLOWED_HOSTS` | unset | comma-separated; **required behind a proxy**, else HTTP 421 |
| `FINCHAT_MCP_TIMEOUT` | `30` | backend call timeout, seconds |

`FINCHAT_MCP_PERSONA` selects which tools are *offered*. It is not access control — it
is an environment variable the caller owns. Enforcement lives where the caller cannot
reach it: IAM on the private services, CLS in BigQuery, the approver check in the loan API.

## Layout

| File | Role |
|---|---|
| `server.py` | tools, resources, instructions, transport selection |
| `backends.py` | HTTP-or-demo access to the governed APIs, OIDC minting |
| `knowledge.py` | the knowledge plane, read from the compiled OKF SSOT |
| `loader.py` | imports sibling-service modules by path, not via `sys.path` |
| `Dockerfile` | streamable-HTTP image; **build context is the repo root** |

## Tests

```bash
python -m pytest mcp_server -q
```

Offline, no GCP. Two of them are negative tests — that writes are opt-in, and that no
persona can ever record a loan decision. Those omissions are the design, and an omission
without a test becomes an oversight the first time someone adds a tool by copying the one
above it.

`test_mcp_image.py` reads the `loader.load(...)` call sites out of the source and asserts
each borrowed module is COPYed into the image. The loads are lazy, so a missing file
survives the build, boot and health check, then breaks one tool in production.

## What it does not do

- **No direct data access.** Every tool calls the same private APIs the web UI calls.
- **No loan decisions.** `POST /v1/loans/{id}/decision` is exposed to no persona.
- **No OAuth.** Header-capable clients (Claude Code, Claude Desktop) work today; hosted
  connector flows need the DCR proxy in [ADR-0020](../docs/adr/0020-remote-mcp-workspace-federation.md), which is designed and not built.

## The knowledge base

`search_knowledge_base` returns **passages, not an answer**, so the calling model grounds
on sources it can cite and check. It prefers the agent service's `POST /search` — the same
dense + BM25 + RRF + Gemini-rerank path the agent's own tool uses (docs/21) — and falls
back to BM25 over the shipped 22-document corpus when no agent is configured, when the
agent is unreachable, or when the deployed revision predates `/search`.

The fallback is real, not a stub, and it labels itself: `retriever: "sparse-local"`. What
it loses is the dense arm, which is what catches a paraphrase sharing no tokens with the
source ("spend more than I have" → overdraft). Exact-token questions — a zip code, `NSF`,
`$225` — are exactly what BM25 is best at, so the offline path handles the common case well
and says when it is the one answering.
