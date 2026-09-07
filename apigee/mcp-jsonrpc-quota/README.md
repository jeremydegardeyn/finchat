# Per-tool quota and analytics for an MCP endpoint

**Status: written and unit-tested, never run inside Apigee.** The JavaScript logic is
covered by `node --test apigee/mcp-jsonrpc-quota/`; the policy XML is reviewed but
unexercised, because standing up an Apigee org costs real money and this repository's
premise is near-zero cost. Treat the XML as a starting point, not a tested artifact.
See [docs/27 §3](../../docs/27-mcp-service.md) for the argument this implements.

## The problem

API management is built on the operation being visible in the request. `GET /v1/accounts/{id}/balance`
and `POST /v1/loans` are different rows in every quota, every latency chart and every
"which endpoint is expensive" conversation.

MCP moves the operation into the body. Every call is:

```
POST /mcp
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_account_balance",...}}
```

So a gateway in front of an MCP server sees **one path, one operation, one quota bucket**
for however many tools the server offers — eleven, on FinChat's customer persona. A read
tool and a write tool share a limit. Analytics cannot say which tool anyone used. Nothing
is misconfigured; the dimension the whole product keys on has simply moved somewhere the
product does not look.

This is the sharp edge for anyone putting MCP behind an API program, and it is not
addressed by any vendor out of the box.

## The fix

Read the body, and set a variable the existing policies can key on.

| | Policy | Does |
|---|---|---|
| 1 | `SetMcpDefaults.xml` | Guarantees the variables exist, so a later failure degrades instead of faulting |
| 2 | `ExtractMcpTool.xml` → `extract-mcp-tool.js` | Parses the JSON-RPC body, sets `mcp.operation` to `tools/call:<name>` |
| 3 | `ComposeQuotaKey.xml` | `{client_id}\|{mcp.operation}` — per consumer *and* per tool |
| 4 | `QuotaPerTool.xml` | One Quota policy, N buckets, via `<Class ref="mcp.operation">` |
| 5 | `CollectMcpStats.xml` | The same key as an analytics dimension |

Attach 1–3 and 5 to the proxy's request `PreFlow`, and 4 after whatever authenticates the
caller. Upload `extract-mcp-tool.js` as a `jsc` resource.

## What the callout gets right, and why each one is deliberate

Every item here is a failure mode the obvious version has.

- **It never throws.** An unparseable body resolves to `mcp.operation = "unknown"` and is
  passed to the target. A callout that faults on unexpected input takes the proxy down for
  every caller, including the ones sending valid requests — and deciding a request is
  invalid is the MCP server's job, not the gateway's.
- **Tool names are validated before becoming keys.** The name arrives from the caller and
  is about to become a quota identifier and an analytics dimension. Raw, it is an
  unbounded-cardinality injection point and a trivial way around a per-tool limit: vary
  the name, get a fresh bucket. Names must match `^[A-Za-z_][A-Za-z0-9_.-]*$` and fit in
  64 characters — the same shape Vertex accepts in a `functionDeclaration` — or they
  resolve to `tools/call:invalid`.
- **Batches are sized, not flattened.** JSON-RPC permits an array. MCP's 2025-06-18
  revision removed batching, but a policy that assumes clients honour a spec is a policy
  with a hole in it: charging a twenty-call batch as the one tool it names first is
  exactly the bypass a quota exists to stop.
- **Protocol methods are counted separately.** `initialize` and `tools/list` are real
  traffic with real cost. A client looping on `tools/list` is invisible if that cost lands
  on whichever tool was called last.

## What this does not solve

- **Quota counts belong in a KVM.** They are inline here so the file reads as one thing.
  A limit change that needs a proxy redeploy is a limit that stops being changed.
- **Streaming responses.** Streamable HTTP can return SSE. Response-side policies that
  buffer will hurt; this folder only touches the request.
- **Authorization.** Per-tool *quota* is not per-tool *permission*. FinChat scopes which
  tools exist per persona in the server (`FINCHAT_MCP_PERSONA`) and enforces access with
  Cloud Run IAM ([ADR-0031](../../docs/adr/0031-mcp-over-http-service-identity.md)). A
  gateway policy that decided which tools a caller may reach would be a second, divergent
  copy of that decision.
- **It has not run in Apigee.** Said twice on purpose.
